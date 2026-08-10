import asyncio
import json
import time
from datetime import date
from decimal import Decimal

from app.models.batch import (
    DocumentType,
    ProcessingStatus,
    SourceDocument,
    SourceFolder,
)
from app.services import audit_classification_service as service
from app.services.exclusion_service import FilterOutcome
from app.services.expense_classification_service import ExpenseClassificationStatus
from app.services.expense_record_service import build_expense_records


def document(
    document_id: int,
    *,
    amount: str = "100.00",
    description: str = "无法由规则确定的采购事项",
    source_folder: SourceFolder = SourceFolder.INVOICE,
    document_type: DocumentType = DocumentType.ELECTRONIC_INVOICE,
) -> SourceDocument:
    return SourceDocument(
        id=document_id,
        batch_id=1,
        source_folder=source_folder,
        relative_path=f"发票/{document_id}.pdf",
        file_name=f"{document_id}.pdf",
        file_type="pdf",
        file_size=1,
        file_hash=str(document_id) * 64,
        document_type=document_type,
        processing_status=ProcessingStatus.RECOGNIZED,
        document_date=date(2025, 7, 8),
        counterparty="测试商户",
        description=description,
        amount=Decimal(amount),
        currency="CNY",
        processing_attempts=1,
    )


class FakeProvider:
    def __init__(self, *, delay: float = 0) -> None:
        self.calls = 0
        self.delay = delay
        self.running = 0
        self.max_running = 0

    def chat_completion(self, _system, prompt, **_kwargs):
        self.calls += 1
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        try:
            if self.delay:
                time.sleep(self.delay)
            transaction_payload = prompt.split("交易：", 1)[1]
            refs = [item["record_ref"] for item in json.loads(transaction_payload)]
            return json.dumps({"items": [
                {
                    "record_ref": record_ref,
                    "consumption_nature": "BUSINESS",
                    "expense_scene": "OTHER",
                    "buyer_name": "测试商户",
                    "buyer_tax_id": None,
                    "buyer_title_status": "PRESENT",
                    "personal_evidence_source": "NONE",
                    "audit_confidence": 0.95,
                    "audit_evidence": "业务采购事项",
                    "audit_needs_manual_review": False,
                    "expense_type": "办公费",
                    "standard_subcategory": "其他",
                    "expense_item": "测试采购",
                    "hospitality_target": None,
                    "classification_confidence": 0.95,
                    "classification_evidence": "采购内容",
                    "classification_evidence_source": "DOCUMENT_BODY",
                    "classification_needs_manual_review": False,
                }
                for record_ref in refs
            ]}, ensure_ascii=False)
        finally:
            self.running -= 1


class FakeLlmService:
    def __init__(self, provider: FakeProvider) -> None:
        self.is_available = True
        self.active_provider = provider


def signal(record_ref: str, **overrides) -> service.AuditClassificationSignal:
    payload = {
        "record_ref": record_ref,
        "consumption_nature": "BUSINESS",
        "expense_scene": "OTHER",
        "buyer_name": None,
        "buyer_tax_id": None,
        "buyer_title_status": "NOT_APPLICABLE",
        "personal_evidence_source": "NONE",
        "audit_confidence": 0.95,
        "audit_evidence": "资料显示业务用途",
        "audit_needs_manual_review": False,
        "expense_type": "培训费",
        "standard_subcategory": "",
        "expense_item": "员工专业课程",
        "hospitality_target": None,
        "classification_confidence": 0.90,
        "classification_evidence": "描述明确为专业课程培训",
        "classification_evidence_source": "DESCRIPTION",
        "classification_needs_manual_review": False,
    }
    payload.update(overrides)
    return service.AuditClassificationSignal.model_validate(payload)


def test_prompt_v2_contains_boundaries_confidence_contract_and_strict_schema():
    prompt = service.AUDIT_CLASSIFICATION_SYSTEM_PROMPT

    for text in (
        "审核与分类是两个独立任务",
        "分类证据优先级",
        "车票、机票、出租车",
        "批量货物运输、托运、搬运",
        "项目测试服务器/研发模块",
        "研发组加班餐/部门团建",
        "培训费、会议费、维修费、通讯费",
        "0.75-0.89",
        "输出前检查",
    ):
        assert text in prompt
    request_prompt = service._prompt([], {})
    assert "audit_needs_manual_review" in request_prompt
    assert "classification_needs_manual_review" in request_prompt
    assert "classification_evidence_source" in request_prompt
    assert '"amount"' not in request_prompt.split("交易：", 1)[0]


def test_prompt_v2_rejects_missing_extra_and_duplicate_output_fields():
    item = signal("1:R1").model_dump(mode="json")
    missing = dict(item)
    missing.pop("classification_evidence_source")
    assert service._parse_signals(json.dumps({"items": [missing]}), {"1:R1"}) == {}

    extra = {**item, "unexpected": True}
    assert service._parse_signals(json.dumps({"items": [extra]}), {"1:R1"}) == {}

    assert service._parse_signals(
        json.dumps({"items": [item, item]}), {"1:R1"}
    ) == {}


def test_audit_and_classification_review_flags_are_independent():
    source = document(
        1,
        source_folder=SourceFolder.PAYMENT_EVIDENCE,
        document_type=DocumentType.PAYMENT_SCREENSHOT,
    )
    record = build_expense_records([source])[0]
    audit_review = signal(record.record_ref, audit_needs_manual_review=True)

    decision = service._decision_from_signal(record, audit_review, None, None, [])
    classification = service._classification_from_signal(record, audit_review, [], None)

    assert decision.outcome == FilterOutcome.REVIEW
    assert decision.reason_code == "MODEL_REQUESTED_AUDIT_REVIEW"
    assert classification.status == ExpenseClassificationStatus.CLASSIFIED

    classification_review = signal(
        record.record_ref,
        classification_needs_manual_review=True,
    )
    decision = service._decision_from_signal(record, classification_review, None, None, [])
    classification = service._classification_from_signal(
        record, classification_review, [], None
    )

    assert decision.outcome == FilterOutcome.KEEP
    assert classification.status == ExpenseClassificationStatus.REVIEW
    assert classification.error_code == "MODEL_REQUESTED_CLASSIFICATION_REVIEW"
    assert classification.evidence_source == "DESCRIPTION"


def test_deterministic_rules_override_model_review_flags():
    source = document(1, description="打印纸采购")
    record = build_expense_records([source])[0]
    model_signal = signal(
        record.record_ref,
        expense_type="培训费",
        classification_needs_manual_review=True,
    )

    classification = service._classification_from_signal(record, model_signal, [], None)

    assert classification.expense_type == "办公费"
    assert classification.status == ExpenseClassificationStatus.CLASSIFIED
    assert classification.evidence_source == "RULE"


def test_hard_exclusion_is_not_weakened_by_audit_review_flag():
    source = document(1)
    source.status_reason = "DUPLICATE_SUMMARY_PDF"
    record = build_expense_records([source])[0]

    decision = service._decision_from_signal(
        record,
        signal(record.record_ref, audit_needs_manual_review=True),
        None,
        None,
        [],
    )

    assert decision.outcome == FilterOutcome.EXCLUDE
    assert decision.reason_code == "DUPLICATE_SUMMARY_PDF"


async def test_second_export_reuses_cached_audit_and_classification(monkeypatch):
    provider = FakeProvider()
    documents = [document(1), document(2)]
    records = build_expense_records(documents)
    monkeypatch.setattr(service, "get_llm_service", lambda: FakeLlmService(provider))
    monkeypatch.setattr(service, "_model_metadata", lambda: ("fake", "model-1"))

    first = await service.ensure_audit_classification_results(documents, records, None, None)
    second = await service.ensure_audit_classification_results(documents, records, None, None)

    assert first.metrics.cache_miss_count == 2
    assert second.metrics.cache_hit_count == 2
    assert second.metrics.llm_request_count == 0
    assert provider.calls == 1
    assert all(
        "_audit_classification" in item.extracted_fields_json for item in documents
    )


async def test_changing_recognized_amount_invalidates_only_that_record(monkeypatch):
    provider = FakeProvider()
    documents = [document(1, amount="100.00"), document(2, amount="200.00")]
    monkeypatch.setattr(service, "get_llm_service", lambda: FakeLlmService(provider))
    monkeypatch.setattr(service, "_model_metadata", lambda: ("fake", "model-1"))

    await service.ensure_audit_classification_results(
        documents, build_expense_records(documents), None, None
    )
    documents[1].amount = Decimal("201.00")
    refreshed = await service.ensure_audit_classification_results(
        documents, build_expense_records(documents), None, None
    )

    assert refreshed.metrics.cache_hit_count == 1
    assert refreshed.metrics.cache_miss_count == 1
    assert provider.calls == 2


async def test_prompt_v1_cache_is_invalidated_by_prompt_v2(monkeypatch):
    provider = FakeProvider()
    documents = [document(1)]
    monkeypatch.setattr(service, "get_llm_service", lambda: FakeLlmService(provider))
    monkeypatch.setattr(service, "_model_metadata", lambda: ("fake", "model-1"))

    await service.ensure_audit_classification_results(
        documents, build_expense_records(documents), None, None
    )
    cache_records = documents[0].extracted_fields_json["_audit_classification"]["records"]
    cache_records["R1"]["prompt_version"] = "1.0"

    refreshed = await service.ensure_audit_classification_results(
        documents, build_expense_records(documents), None, None
    )

    assert service.AUDIT_CLASSIFICATION_PROMPT_VERSION == "2.0"
    assert refreshed.metrics.cache_miss_count == 1
    assert provider.calls == 2


async def test_llm_batch_requests_respect_configured_concurrency(monkeypatch):
    provider = FakeProvider(delay=0.05)
    documents = [document(index) for index in range(1, 22)]
    monkeypatch.setattr(service, "get_llm_service", lambda: FakeLlmService(provider))
    monkeypatch.setattr(service, "_model_metadata", lambda: ("fake", "model-1"))
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: type("Settings", (), {"llm_batch_concurrency": 2})(),
    )

    results = await service.ensure_audit_classification_results(
        documents, build_expense_records(documents), None, None
    )

    assert results.metrics.llm_batch_count == 3
    assert provider.max_running == 2
    assert provider.calls == 3


async def test_missing_single_record_result_becomes_review_without_recursive_retry(monkeypatch):
    class MissingProvider(FakeProvider):
        def chat_completion(self, _system, _prompt, **_kwargs):
            self.calls += 1
            return '{"items": []}'

    provider = MissingProvider()
    documents = [document(1)]
    records = build_expense_records(documents)
    monkeypatch.setattr(service, "get_llm_service", lambda: FakeLlmService(provider))
    monkeypatch.setattr(service, "_model_metadata", lambda: ("fake", "model-1"))

    results = await asyncio.wait_for(
        service.ensure_audit_classification_results(
            documents, records, None, None
        ),
        timeout=1,
    )

    assert results.metrics.llm_request_count == 1
    assert (
        results.classifications[records[0].id].error_code
        == "MISSING_AUDIT_CLASSIFICATION_RESULT"
    )
    assert provider.calls == 1
