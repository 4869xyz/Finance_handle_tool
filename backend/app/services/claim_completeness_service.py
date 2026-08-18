"""Match claim rows against monthly recognition results and build 系统审核 remarks."""

from __future__ import annotations

import logging
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.batch import (
    BatchStatus,
    DocumentType,
    ProcessingBatch,
    ProcessingStatus,
    SourceDocument,
    SourceFolder,
)
from app.models.reimbursement_package import PackageStatus, ReimbursementPackage
from app.services.claim_workbook_service import (
    ClaimRow,
    annotate_claim_workbook,
    claims_from_json,
)
from app.services.expense_classification_service import (
    ExpenseClassificationStatus,
    classify_expenses,
    normalize_expense_type,
)
from app.services.expense_record_service import ExpenseRecordView, build_expense_records


logger = logging.getLogger(__name__)

INVOICE_FOLDERS = {SourceFolder.INVOICE, SourceFolder.REPLACEMENT_INVOICE}
PAYMENT_FOLDERS = {SourceFolder.PAYMENT_EVIDENCE}
TAXI_CLAIM_ITEMS = {"打车", "本月打车"}
TAXI_KEYWORDS = ("打车", "出行", "滴滴", "高德", "网约车", "出租车")
TAXI_DOCUMENT_TYPES = {
    DocumentType.TAXI_INVOICE,
    DocumentType.ITINERARY,
}
ELIGIBLE_STATUSES = {
    ProcessingStatus.RECOGNIZED,
    ProcessingStatus.NEEDS_MANUAL_REVIEW,
    ProcessingStatus.FAILED,
}
TERMINAL_BATCH_STATUSES = {BatchStatus.COMPLETED, BatchStatus.FAILED}


@dataclass(slots=True)
class ClaimAuditResult:
    excel_row: int
    sheet: str
    remark: str
    invoice_document_ids: list[int]
    payment_document_ids: list[int]


def _normalize(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "").casefold()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)


def _item_key(value: str | None) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or "").strip())


def _format_amount(value: Decimal | None) -> str:
    if value is None:
        return ""
    return format(value.quantize(Decimal("0.01")), "f")


def _text_blob(record: ExpenseRecordView) -> str:
    return _normalize(
        " ".join(
            part
            for part in (
                record.description,
                record.item_name,
                record.counterparty,
            )
            if part
        )
    )


def _item_overlap(claim_item: str, record: ExpenseRecordView) -> bool:
    left = _normalize(claim_item)
    right = _text_blob(record)
    if len(left) < 2 or len(right) < 2:
        return False
    if left in right or right in left:
        return True
    if len(left) < 4:
        return False
    left_grams = {left[index : index + 3] for index in range(len(left) - 2)}
    right_grams = {right[index : index + 3] for index in range(len(right) - 2)}
    return len(left_grams & right_grams) >= 2


def _is_invoice(record: ExpenseRecordView) -> bool:
    return record.source_folder in INVOICE_FOLDERS


def _is_payment(record: ExpenseRecordView) -> bool:
    return record.source_folder in PAYMENT_FOLDERS


def _is_taxi_aggregate_claim(claim: ClaimRow) -> bool:
    return _item_key(claim.item) in TAXI_CLAIM_ITEMS


def _is_taxi_record(record: ExpenseRecordView) -> bool:
    if record.document_type in TAXI_DOCUMENT_TYPES:
        return True
    blob = _text_blob(record)
    return any(keyword in blob for keyword in TAXI_KEYWORDS)


def _usable(record: ExpenseRecordView) -> bool:
    return record.processing_status in ELIGIBLE_STATUSES


def _unreliable(records: list[ExpenseRecordView]) -> bool:
    return any(
        record.processing_status != ProcessingStatus.RECOGNIZED for record in records
    )


def _normalize_invoice_number(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def _pick_unique_amount(
    candidates: list[ExpenseRecordView],
    amount: Decimal,
) -> list[ExpenseRecordView]:
    hits = [record for record in candidates if record.amount == amount]
    return hits


def _disambiguate_by_item(
    claim: ClaimRow,
    hits: list[ExpenseRecordView],
) -> list[ExpenseRecordView]:
    overlapped = [record for record in hits if _item_overlap(claim.item, record)]
    if len(overlapped) == 1:
        return overlapped
    if len(overlapped) > 1:
        return overlapped
    return hits


def _sum_amounts(records: list[ExpenseRecordView]) -> Decimal:
    total = Decimal("0.00")
    for record in records:
        if record.amount is not None:
            total += record.amount
    return total.quantize(Decimal("0.01"))


def _group_payments(
    matched_invoices: list[ExpenseRecordView],
    payment_records: list[ExpenseRecordView],
    claim: ClaimRow,
) -> tuple[list[ExpenseRecordView], bool]:
    if not matched_invoices:
        return [], False
    group_ids = {
        record.reconciliation_group_id
        for record in matched_invoices
        if record.reconciliation_group_id
    }
    grouped = [
        record
        for record in payment_records
        if record.reconciliation_group_id
        and record.reconciliation_group_id in group_ids
        and record.reconciliation_relation == "confirmed_duplicate"
    ]
    if grouped:
        return grouped, False

    if matched_invoices:
        invoice_amounts = {
            record.amount for record in matched_invoices if record.amount is not None
        }
        same_amount = [
            record for record in payment_records if record.amount in invoice_amounts
        ]
        if same_amount:
            return same_amount, False

    overlapped = [
        record for record in payment_records if _item_overlap(claim.item, record)
    ]
    if overlapped:
        return overlapped, True
    return [], False


def _classification_remark(
    claim: ClaimRow,
    invoice_records: list[ExpenseRecordView],
    documents_by_id: dict[int, SourceDocument],
) -> str | None:
    if not claim.expense_type or not invoice_records:
        return None
    documents = []
    seen: set[int] = set()
    for record in invoice_records:
        if record.source_document_id in seen:
            continue
        seen.add(record.source_document_id)
        document = documents_by_id.get(record.source_document_id)
        if document is not None:
            documents.append(document)
    if not documents:
        return None
    results = classify_expenses(documents)
    types = []
    pending = False
    for document in documents:
        classification = results.get(document.id)
        if classification is None:
            pending = True
            continue
        if classification.status != ExpenseClassificationStatus.CLASSIFIED:
            pending = True
            continue
        types.append(normalize_expense_type(classification.expense_type))
    claimed = normalize_expense_type(claim.expense_type)
    unique_types = {item for item in types if item and item != "待分类"}
    if len(unique_types) > 1:
        return "费用类型待复核"
    if unique_types:
        recognized = next(iter(unique_types))
        if claimed and claimed != "待分类" and recognized != claimed:
            return f"费用类型与资料分类不符（总表：{claim.expense_type}，识别：{recognized}）"
        return None
    if pending or claimed:
        return "费用类型待复核"
    return None


def _build_remark(
    *,
    flags: list[str],
    has_invoice: bool,
    has_payment: bool,
    no_month: bool,
) -> str:
    parts = list(flags)
    if no_month:
        if "总表月份在提交包中无对应文件夹" not in parts:
            parts.insert(0, "总表月份在提交包中无对应文件夹")
        return "；".join(parts)
    missing: list[str] = []
    if not has_invoice:
        missing.append("发票")
    if not has_payment:
        missing.append("支付凭证")
    if missing:
        if missing == ["发票"]:
            parts.insert(0, "缺少发票")
        elif missing == ["支付凭证"]:
            parts.insert(0, "缺少支付凭证")
        else:
            parts.insert(0, "缺少发票和支付凭证")
    if not parts:
        return "通过"
    return "；".join(dict.fromkeys(parts))


def audit_claims(
    *,
    employee_name: str,
    claims: list[ClaimRow],
    batches: list[ProcessingBatch],
    documents: list[SourceDocument],
) -> list[ClaimAuditResult]:
    month_batches = {batch.expense_month: batch for batch in batches}
    records = [record for record in build_expense_records(documents) if _usable(record)]
    records_by_batch: dict[int, list[ExpenseRecordView]] = defaultdict(list)
    for record in records:
        records_by_batch[record.batch_id].append(record)
    documents_by_id = {document.id: document for document in documents}
    used_invoice_refs: set[str] = set()
    results: list[ClaimAuditResult] = []

    for claim in claims:
        flags: list[str] = []
        matched_invoices: list[ExpenseRecordView] = []
        matched_payments: list[ExpenseRecordView] = []
        weak_payment = False
        no_month = False

        if (
            claim.applicant
            and employee_name
            and _normalize(claim.applicant) != _normalize(employee_name)
        ):
            flags.append("申请人员与提交包姓名不一致")

        batch = (
            month_batches.get(claim.expense_month) if claim.expense_month else None
        )
        if batch is None:
            no_month = True
            results.append(
                ClaimAuditResult(
                    excel_row=claim.excel_row,
                    sheet=claim.sheet,
                    remark=_build_remark(
                        flags=flags,
                        has_invoice=False,
                        has_payment=False,
                        no_month=True,
                    ),
                    invoice_document_ids=[],
                    payment_document_ids=[],
                )
            )
            continue

        month_records = records_by_batch.get(batch.id, [])
        invoice_pool = [
            record
            for record in month_records
            if _is_invoice(record) and record.record_ref not in used_invoice_refs
        ]
        payment_pool = [record for record in month_records if _is_payment(record)]

        if claim.invoice_number:
            number = _normalize_invoice_number(claim.invoice_number)
            number_hits = [
                record
                for record in invoice_pool
                if _normalize_invoice_number(record.invoice_number) == number
            ]
            if not number_hits:
                flags.append("总表发票号码在资料中未找到")
            elif len(number_hits) > 1:
                flags.append("匹配存疑，需人工复核")
            else:
                hit = number_hits[0]
                matched_invoices = [hit]
                if claim.amount is not None and hit.amount != claim.amount:
                    flags.append(
                        "金额不一致（总表："
                        f"{_format_amount(claim.amount)}，发票："
                        f"{_format_amount(hit.amount)}）"
                    )

        if not matched_invoices and _is_taxi_aggregate_claim(claim) and claim.amount is not None:
            taxi_invoices = [
                record
                for record in invoice_pool
                if record.amount is not None and _is_taxi_record(record)
            ]
            if taxi_invoices and _sum_amounts(taxi_invoices) == claim.amount:
                matched_invoices = taxi_invoices
            taxi_payments = [
                record
                for record in payment_pool
                if record.amount is not None and _is_taxi_record(record)
            ]
            if taxi_payments and _sum_amounts(taxi_payments) == claim.amount:
                matched_payments = taxi_payments

        if not matched_invoices and claim.amount is not None:
            amount_hits = _pick_unique_amount(invoice_pool, claim.amount)
            if len(amount_hits) > 1:
                amount_hits = _disambiguate_by_item(claim, amount_hits)
            if len(amount_hits) == 1:
                matched_invoices = amount_hits
            elif len(amount_hits) > 1:
                flags.append("匹配存疑，需人工复核")

        if not matched_payments:
            matched_payments, weak_payment = _group_payments(
                matched_invoices,
                payment_pool,
                claim,
            )

        if not matched_payments and not matched_invoices and claim.amount is not None:
            payment_hits = _pick_unique_amount(payment_pool, claim.amount)
            if len(payment_hits) > 1:
                payment_hits = _disambiguate_by_item(claim, payment_hits)
            if len(payment_hits) == 1:
                matched_payments = payment_hits
            elif len(payment_hits) > 1:
                flags.append("匹配存疑，需人工复核")

        for record in matched_invoices:
            used_invoice_refs.add(record.record_ref)

        if weak_payment and matched_payments:
            flags.append("支付凭证为弱匹配，需人工看")
        if _unreliable(matched_invoices + matched_payments):
            flags.append("对应文件未可靠识别")

        classification_flag = _classification_remark(
            claim,
            matched_invoices,
            documents_by_id,
        )
        if classification_flag:
            flags.append(classification_flag)

        remark = _build_remark(
            flags=flags,
            has_invoice=bool(matched_invoices),
            has_payment=bool(matched_payments),
            no_month=False,
        )
        results.append(
            ClaimAuditResult(
                excel_row=claim.excel_row,
                sheet=claim.sheet,
                remark=remark,
                invoice_document_ids=sorted(
                    {record.source_document_id for record in matched_invoices}
                ),
                payment_document_ids=sorted(
                    {record.source_document_id for record in matched_payments}
                ),
            )
        )
    return results


def results_to_json(results: list[ClaimAuditResult]) -> list[dict[str, Any]]:
    return [
        {
            "excel_row": item.excel_row,
            "sheet": item.sheet,
            "remark": item.remark,
            "invoice_document_ids": item.invoice_document_ids,
            "payment_document_ids": item.payment_document_ids,
        }
        for item in results
    ]


async def audit_package(db: AsyncSession, package_id: int) -> ReimbursementPackage:
    package = await db.get(ReimbursementPackage, package_id)
    if package is None:
        raise ValueError("提交包不存在")
    result = await db.execute(
        select(ProcessingBatch)
        .where(ProcessingBatch.package_id == package_id)
        .options(selectinload(ProcessingBatch.documents))
        .order_by(ProcessingBatch.expense_month, ProcessingBatch.id)
    )
    batches = result.scalars().unique().all()
    documents = [document for batch in batches for document in batch.documents]
    claims = claims_from_json(package.parsed_claims)
    audits = audit_claims(
        employee_name=package.employee_name,
        claims=claims,
        batches=list(batches),
        documents=documents,
    )
    package.audit_results = results_to_json(audits)
    package.audited_at = datetime.utcnow()
    package.status = PackageStatus.COMPLETED
    package.status_reason = None
    package.status_detail = None
    await db.commit()
    await db.refresh(package)
    return package


async def maybe_audit_package(batch_id: int) -> None:
    from app.database import async_session_maker

    async with async_session_maker() as db:
        batch = await db.get(ProcessingBatch, batch_id)
        if batch is None or batch.package_id is None:
            return
        result = await db.execute(
            select(ProcessingBatch.status).where(
                ProcessingBatch.package_id == batch.package_id
            )
        )
        statuses = list(result.scalars().all())
        if not statuses or any(status not in TERMINAL_BATCH_STATUSES for status in statuses):
            return
        package_id = batch.package_id
    try:
        async with async_session_maker() as db:
            await audit_package(db, package_id)
    except Exception as exc:
        logger.warning(
            "Automatic claim audit failed for package=%s: %s",
            package_id,
            exc.__class__.__name__,
        )


def export_audited_workbook(package: ReimbursementPackage) -> bytes:
    remarks = {
        int(item["excel_row"]): str(item.get("remark") or "")
        for item in (package.audit_results or [])
        if item.get("excel_row")
    }
    return annotate_claim_workbook(
        package.file_data,
        sheet_name=package.sheet_name or "Sheet1",
        remarks_by_row=remarks,
    )
