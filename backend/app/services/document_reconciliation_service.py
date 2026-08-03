"""Batch-level reconciliation for transaction records.

Candidate generation and all arithmetic are deterministic.  The model is only
asked to judge whether candidate records describe the same expense and, if so,
which record is the primary credential.
"""

import asyncio
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.batch import ProcessingBatch, SourceDocument
from app.services.expense_record_service import (
    RECONCILIATION_VERSION,
    ExpenseRecordView,
    build_expense_records,
)
from app.services.llm_service import get_llm_service


logger = logging.getLogger(__name__)
RECONCILIATION_PROMPT_VERSION = "1.0"
RECONCILIATION_ACCEPT_THRESHOLD = 0.85
RECONCILIATION_MAX_OUTPUT_TOKENS = 6000


class ReconciliationSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: str
    relation: Literal[
        "confirmed_duplicate",
        "possible_duplicate",
        "independent",
        "unknown",
    ]
    record_refs: list[str] = Field(min_length=2)
    primary_record_ref: str | None = None
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(min_length=1, max_length=1000)


@dataclass(slots=True)
class CandidateGroup:
    group_id: str
    records: list[ExpenseRecordView]
    candidate_evidence: list[str]


@dataclass(slots=True)
class ReconciliationSummary:
    batch_id: int
    status: str
    total_records: int
    confirmed_groups: int
    review_groups: int
    eligible_total: Decimal | None
    message: str


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value.casefold())


def _normalize_number(value: str | None) -> str:
    return re.sub(r"[^0-9a-z]+", "", (value or "").casefold())


def _dates_close(left: date | None, right: date | None, days: int = 7) -> bool:
    return bool(left and right and abs((left - right).days) <= days)


def _description_overlap(left: str | None, right: str | None) -> bool:
    left_value = _normalize_text(left)
    right_value = _normalize_text(right)
    if len(left_value) < 4 or len(right_value) < 4:
        return False
    if left_value in right_value or right_value in left_value:
        return True
    left_grams = {left_value[index : index + 3] for index in range(len(left_value) - 2)}
    right_grams = {
        right_value[index : index + 3] for index in range(len(right_value) - 2)
    }
    return len(left_grams & right_grams) >= 2


def _candidate_evidence(
    left: ExpenseRecordView,
    right: ExpenseRecordView,
) -> list[str]:
    if left.source_document_id == right.source_document_id:
        return []
    evidence: list[str] = []
    identifiers = (
        ("发票号", left.invoice_number, right.invoice_number),
        ("订单号", left.order_number, right.order_number),
        ("交易号", left.transaction_number, right.transaction_number),
    )
    for label, left_value, right_value in identifiers:
        normalized = _normalize_number(left_value)
        if normalized and normalized == _normalize_number(right_value):
            evidence.append(f"{label}一致")

    same_amount = left.amount is not None and left.amount == right.amount
    same_counterparty = bool(
        _normalize_text(left.counterparty)
        and _normalize_text(left.counterparty) == _normalize_text(right.counterparty)
    )
    close_date = _dates_close(left.document_date, right.document_date)
    similar_description = _description_overlap(left.description, right.description)
    if same_amount:
        evidence.append("金额一致")
    if same_counterparty:
        evidence.append("对方一致")
    if close_date:
        evidence.append("日期相近")
    if similar_description:
        evidence.append("事项相近")

    exact_identifier = any(item.endswith("号一致") for item in evidence)
    contextual_matches = sum((same_counterparty, close_date, similar_description))
    if exact_identifier or (same_amount and contextual_matches >= 2):
        return evidence
    return []


def build_candidate_groups(records: list[ExpenseRecordView]) -> list[CandidateGroup]:
    """Build connected candidate components using conservative exact signals."""
    candidate_records = [record for record in records if record.amount is not None]
    parent = {record.record_ref: record.record_ref for record in candidate_records}
    edge_evidence: dict[tuple[str, str], list[str]] = {}

    def find(record_ref: str) -> str:
        while parent[record_ref] != record_ref:
            parent[record_ref] = parent[parent[record_ref]]
            record_ref = parent[record_ref]
        return record_ref

    def union(left_ref: str, right_ref: str) -> None:
        left_root = find(left_ref)
        right_root = find(right_ref)
        if left_root != right_root:
            parent[right_root] = left_root

    for left_index, left in enumerate(candidate_records):
        for right in candidate_records[left_index + 1 :]:
            evidence = _candidate_evidence(left, right)
            if not evidence:
                continue
            refs = tuple(sorted((left.record_ref, right.record_ref)))
            edge_evidence[refs] = evidence
            union(left.record_ref, right.record_ref)

    components: dict[str, list[ExpenseRecordView]] = defaultdict(list)
    for record in candidate_records:
        components[find(record.record_ref)].append(record)

    groups: list[CandidateGroup] = []
    for component in components.values():
        if len(component) < 2:
            continue
        refs = {record.record_ref for record in component}
        evidence = sorted(
            {
                item
                for edge, items in edge_evidence.items()
                if edge[0] in refs and edge[1] in refs
                for item in items
            }
        )
        groups.append(
            CandidateGroup(
                group_id=f"C{len(groups) + 1}",
                records=sorted(component, key=lambda record: record.record_ref),
                candidate_evidence=evidence,
            )
        )
    return groups


def _prompt(groups: list[CandidateGroup]) -> str:
    payload = []
    for group in groups:
        payload.append(
            {
                "group_id": group.group_id,
                "candidate_evidence": group.candidate_evidence,
                "records": [
                    {
                        "record_ref": record.record_ref,
                        "record_type": record.record_type,
                        "source": record.source_folder.value,
                        "date": record.document_date.isoformat()
                        if record.document_date
                        else None,
                        "counterparty": record.counterparty,
                        "description": record.description,
                        "amount": str(record.amount) if record.amount is not None else None,
                        "amount_evidence": record.amount_evidence,
                        "invoice_number": record.invoice_number,
                        "order_number": record.order_number,
                        "transaction_number": record.transaction_number,
                    }
                    for record in group.records
                ],
            }
        )
    return f"""你是企业报销批次的跨文件凭证核对助手。
候选组由程序根据号码、金额、日期、对方和事项生成。请判断每组记录是否证明同一笔费用。

规则：
1. confirmed_duplicate：证据足以确认是同一笔费用；必须从组内选择唯一主凭证，其他为辅助凭证。
2. possible_duplicate：看起来可能重复但证据不足；不得选择主凭证。
3. independent：组内记录属于不同费用；不得选择主凭证。
4. unknown：无法判断；不得选择主凭证。
5. 主凭证不按发票、订单或付款截图固定排序，要根据号码、金额证据、信息完整度和业务事实选择。
6. 只能引用输入中的 record_ref，禁止跨组关联，禁止修改金额或自行补全字段。
7. 只输出合法 JSON，不输出 Markdown 或解释。

严格输出：
{{"prompt_version":"{RECONCILIATION_PROMPT_VERSION}","groups":[{{"group_id":"C1","relation":"confirmed_duplicate|possible_duplicate|independent|unknown","record_refs":["1:R1","2:R1"],"primary_record_ref":"1:R1或null","confidence":0.0,"evidence":"简短核对依据"}}]}}

候选组：
{json.dumps(payload, ensure_ascii=False)}"""


def _parse_signals(raw: str, groups: list[CandidateGroup]) -> dict[str, ReconciliationSignal]:
    payload = json.loads(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get("groups"), list):
        raise ValueError("跨文件核对响应缺少 groups")
    expected = {group.group_id: group for group in groups}
    signals: dict[str, ReconciliationSignal] = {}
    for item in payload["groups"]:
        try:
            signal = ReconciliationSignal.model_validate(item)
        except ValidationError:
            continue
        group = expected.get(signal.group_id)
        if group is None or signal.group_id in signals:
            continue
        expected_refs = {record.record_ref for record in group.records}
        if set(signal.record_refs) != expected_refs:
            continue
        if signal.relation == "confirmed_duplicate":
            if signal.primary_record_ref not in expected_refs:
                continue
        elif signal.primary_record_ref is not None:
            continue
        signals[signal.group_id] = signal
    return signals


def _request_signals(groups: list[CandidateGroup]) -> tuple[dict[str, ReconciliationSignal], str | None]:
    llm_service = get_llm_service()
    provider = llm_service.active_provider if llm_service.is_available else None
    if provider is None:
        return {}, "MODEL_NOT_CONFIGURED"
    try:
        raw = provider.chat_completion(
            "你是严谨的财务凭证核对助手，只返回符合要求的 JSON。",
            _prompt(groups),
            max_output_tokens=RECONCILIATION_MAX_OUTPUT_TOKENS,
            json_mode=True,
        )
        return _parse_signals(raw, groups), None
    except Exception as exc:
        status_code = getattr(exc, "status_code", None)
        if status_code in {400, 401, 403, 404, 422}:
            code = "MODEL_CONFIG_ERROR"
        elif isinstance(exc, TimeoutError) or "timeout" in exc.__class__.__name__.lower():
            code = "MODEL_TIMEOUT"
        else:
            code = "MODEL_REQUEST_ERROR"
        logger.warning(
            "Batch reconciliation request failed: %s status=%s",
            exc.__class__.__name__,
            status_code,
        )
        return {}, code


def _independent_state(record: ExpenseRecordView) -> dict:
    eligible = record.amount
    manual_review = eligible is None
    if record.record_type == "TRANSFER":
        if record.transfer_direction == "TRANSFER_IN":
            eligible = Decimal("0.00")
        elif record.transfer_direction != "TRANSFER_OUT":
            eligible = None
            manual_review = True
    return {
        "group_id": None,
        "relation": "independent",
        "role": "INDEPENDENT",
        "primary_record_ref": None,
        "confidence": 1.0,
        "evidence": "未进入跨文件重复候选组",
        "eligible_amount": str(eligible) if eligible is not None else None,
        "manual_review": manual_review,
    }


def reconcile_records(records: list[ExpenseRecordView]) -> tuple[dict[str, dict], dict]:
    """Reconcile records synchronously and return per-record durable state."""
    states = {record.record_ref: _independent_state(record) for record in records}
    groups = build_candidate_groups(records)
    signals: dict[str, ReconciliationSignal] = {}
    request_error: str | None = None
    for offset in range(0, len(groups), 20):
        chunk = groups[offset : offset + 20]
        chunk_signals, chunk_error = _request_signals(chunk)
        signals.update(chunk_signals)
        request_error = request_error or chunk_error

    confirmed_groups = 0
    review_groups = 0
    for group in groups:
        signal = signals.get(group.group_id)
        record_refs = {record.record_ref for record in group.records}
        same_amount = len({record.amount for record in group.records}) == 1
        primary_record = next(
            (
                record
                for record in group.records
                if signal and record.record_ref == signal.primary_record_ref
            ),
            None,
        )
        confirmed = bool(
            signal
            and signal.relation == "confirmed_duplicate"
            and signal.confidence >= RECONCILIATION_ACCEPT_THRESHOLD
            and signal.primary_record_ref in record_refs
            and same_amount
            and primary_record is not None
            and not (
                primary_record.record_type == "TRANSFER"
                and primary_record.transfer_direction != "TRANSFER_OUT"
            )
        )
        if confirmed and signal:
            confirmed_groups += 1
            primary = signal.primary_record_ref
            for record in group.records:
                is_primary = record.record_ref == primary
                states[record.record_ref] = {
                    "group_id": group.group_id,
                    "relation": "confirmed_duplicate",
                    "role": "PRIMARY" if is_primary else "AUXILIARY",
                    "primary_record_ref": primary,
                    "confidence": signal.confidence,
                    "evidence": signal.evidence,
                    "eligible_amount": (
                        str(record.amount) if is_primary and record.amount is not None else "0.00"
                    ),
                    "manual_review": False,
                }
            continue

        if (
            signal
            and signal.relation == "independent"
            and signal.confidence >= RECONCILIATION_ACCEPT_THRESHOLD
        ):
            for record in group.records:
                state = _independent_state(record)
                state.update(
                    group_id=group.group_id,
                    confidence=signal.confidence,
                    evidence=signal.evidence,
                )
                states[record.record_ref] = state
            continue

        review_groups += 1
        relation = signal.relation if signal else "unknown"
        if relation in {"confirmed_duplicate", "independent"}:
            relation = "possible_duplicate"
        evidence = (
            (
                signal.evidence
                + "；模型结论未通过程序置信度、主凭证或金额一致性校验"
            )
            if signal
            else f"跨文件核对未取得有效模型判断（{request_error or 'INVALID_RESPONSE'}）"
        )
        confidence = signal.confidence if signal else None
        for record in group.records:
            states[record.record_ref] = {
                "group_id": group.group_id,
                "relation": relation,
                "role": "UNRESOLVED",
                "primary_record_ref": None,
                "confidence": confidence,
                "evidence": evidence,
                "eligible_amount": None,
                "manual_review": True,
            }

    eligible_values = [
        Decimal(state["eligible_amount"])
        for state in states.values()
        if state.get("eligible_amount") is not None
    ]
    unresolved = any(state.get("eligible_amount") is None for state in states.values())
    summary = {
        "status": "REVIEW_REQUIRED" if review_groups or unresolved else "COMPLETED",
        "total_records": len(records),
        "candidate_groups": len(groups),
        "confirmed_groups": confirmed_groups,
        "review_groups": review_groups,
        "eligible_total": (
            None
            if unresolved
            else str(sum(eligible_values, Decimal("0.00")).quantize(Decimal("0.01")))
        ),
        "error_code": request_error,
    }
    return states, summary


async def reconcile_batch(db: AsyncSession, batch_id: int) -> ReconciliationSummary:
    """Reconcile one terminal batch and persist state in extracted_fields_json."""
    batch = await db.get(ProcessingBatch, batch_id)
    if batch is None:
        raise LookupError("批次不存在")
    result = await db.execute(
        select(SourceDocument)
        .where(SourceDocument.batch_id == batch_id)
        .order_by(SourceDocument.id)
        .with_for_update()
    )
    documents = list(result.scalars().all())
    records = build_expense_records(documents)
    states, summary = await asyncio.to_thread(reconcile_records, records)

    for document in documents:
        payload = dict(document.extracted_fields_json or {})
        document_states: dict[str, dict] = {}
        for record in records:
            if record.source_document_id == document.id:
                document_states[record.record_id] = states[record.record_ref]
        payload["_reconciliation"] = {
            "version": RECONCILIATION_VERSION,
            "prompt_version": RECONCILIATION_PROMPT_VERSION,
            "status": summary["status"],
            "error_code": summary["error_code"],
            "records": document_states,
        }
        document.extracted_fields_json = payload
    await db.commit()

    eligible_total = (
        Decimal(summary["eligible_total"])
        if summary["eligible_total"] is not None
        else None
    )
    return ReconciliationSummary(
        batch_id=batch_id,
        status=summary["status"],
        total_records=summary["total_records"],
        confirmed_groups=summary["confirmed_groups"],
        review_groups=summary["review_groups"],
        eligible_total=eligible_total,
        message=(
            "跨文件核对完成"
            if summary["status"] == "COMPLETED"
            else "跨文件核对完成，存在无法计入的记录或需人工确认的候选组"
        ),
    )
