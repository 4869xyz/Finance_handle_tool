from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.batch import DocumentType, ProcessingStatus, SourceDocument, SourceFolder
from app.models.company_profile import CompanyProfile
from app.models.review_reference import BatchReviewReference
from app.services import exclusion_service
from app.services.exclusion_service import (
    BuyerTitleStatus,
    ConsumptionNature,
    ExpenseScene,
    FilterOutcome,
    FilterSignal,
    PersonalEvidenceSource,
    classify_filter_signals,
    company_title_matches,
    decide_document,
    filter_documents,
    normalize_company_name,
    normalize_tax_id,
)
from app.services.review_reference_service import (
    MAX_REFERENCE_FILE_SIZE,
    ReviewReferenceValidationError,
    match_reference_rows,
    parse_review_reference,
)


def document(
    *,
    document_id: int = 1,
    source_folder: SourceFolder = SourceFolder.INVOICE,
    document_type: DocumentType = DocumentType.ELECTRONIC_INVOICE,
    status: ProcessingStatus = ProcessingStatus.RECOGNIZED,
    reason: str | None = None,
) -> SourceDocument:
    return SourceDocument(
        id=document_id,
        batch_id=1,
        source_folder=source_folder,
        relative_path="发票/a.pdf",
        file_name="a.pdf",
        file_type="pdf",
        file_size=1,
        file_hash="a" * 64,
        document_type=document_type,
        processing_status=status,
        status_reason=reason,
        processing_attempts=1,
    )


def signal(
    *,
    document_id: int = 1,
    nature: ConsumptionNature = ConsumptionNature.BUSINESS,
    scene: ExpenseScene = ExpenseScene.OTHER,
    title_status: BuyerTitleStatus = BuyerTitleStatus.PRESENT,
    buyer_name: str | None = "示例科技有限公司",
    buyer_tax_id: str | None = None,
    confidence: float = 0.95,
    personal_evidence_source: PersonalEvidenceSource = PersonalEvidenceSource.DOCUMENT_BODY,
) -> FilterSignal:
    return FilterSignal(
        document_id=document_id,
        consumption_nature=nature,
        expense_scene=scene,
        buyer_name=buyer_name,
        buyer_tax_id=buyer_tax_id,
        buyer_title_status=title_status,
        personal_evidence_source=personal_evidence_source,
        confidence=confidence,
        evidence="正文证据",
    )


def profile() -> CompanyProfile:
    return CompanyProfile(
        version=3,
        official_name="示例科技有限公司",
        aliases=["示例科技（中国）有限公司"],
        tax_ids=["913000000000000001", "913000000000000002"],
    )


def test_company_identity_normalization_and_exact_matching():
    configured = profile()
    assert normalize_company_name(" 示例科技（中国）有限公司 ") == "示例科技中国有限公司"
    assert normalize_tax_id(" 9130 0000 0000 0000 01 ") == "913000000000000001"
    assert company_title_matches("示例科技（中国）有限公司", None, configured)
    assert not company_title_matches(None, "9130 0000 0000 0000 01", configured)
    assert not company_title_matches("示例科技有限公司分公司", None, configured)


def test_valid_business_invoice_is_kept():
    result = decide_document(document(), signal(), profile())
    assert result.outcome == FilterOutcome.KEEP
    assert result.company_profile_version == 3

    second_identity = decide_document(
        document(),
        signal(buyer_name="示例科技（中国）有限公司"),
        profile(),
    )
    assert second_identity.outcome == FilterOutcome.KEEP


def test_personal_dining_without_company_title_is_excluded():
    result = decide_document(
        document(),
        signal(
            nature=ConsumptionNature.PERSONAL,
            scene=ExpenseScene.DINING,
            title_status=BuyerTitleStatus.INDIVIDUAL_OR_BLANK,
            buyer_name=None,
        ),
        profile(),
    )
    assert result.outcome == FilterOutcome.EXCLUDE
    assert result.reason_code == "PERSONAL_DINING_CONSUMPTION"


def test_personal_life_payment_is_excluded():
    result = decide_document(
        document(
            source_folder=SourceFolder.PAYMENT_EVIDENCE,
            document_type=DocumentType.PAYMENT_SCREENSHOT,
        ),
        signal(
            nature=ConsumptionNature.PERSONAL,
            scene=ExpenseScene.PERSONAL_LIFE,
            title_status=BuyerTitleStatus.NOT_APPLICABLE,
            buyer_name=None,
        ),
        profile(),
    )
    assert result.outcome == FilterOutcome.EXCLUDE
    assert result.reason_code == "PERSONAL_LIFE_CONSUMPTION"


def test_duplicate_low_confidence_and_nonrecognized_boundaries():
    duplicate = decide_document(
        document(status=ProcessingStatus.SKIPPED, reason="DUPLICATE_SUMMARY_PDF"),
        None,
        profile(),
    )
    assert duplicate.outcome == FilterOutcome.EXCLUDE

    low_confidence_invoice = decide_document(
        document(),
        signal(confidence=0.84),
        profile(),
    )
    assert low_confidence_invoice.outcome == FilterOutcome.KEEP
    assert low_confidence_invoice.reason_code == "BUSINESS_EXPENSE"

    low_confidence = decide_document(
        document(
            source_folder=SourceFolder.PAYMENT_EVIDENCE,
            document_type=DocumentType.PAYMENT_SCREENSHOT,
        ),
        signal(
            confidence=0.84,
            title_status=BuyerTitleStatus.NOT_APPLICABLE,
            buyer_name=None,
        ),
        profile(),
    )
    assert low_confidence.outcome == FilterOutcome.REVIEW
    assert low_confidence.reason_code == "LOW_FILTER_CONFIDENCE"

    manual = decide_document(
        document(status=ProcessingStatus.NEEDS_MANUAL_REVIEW),
        signal(),
        profile(),
    )
    assert manual.outcome == FilterOutcome.REVIEW
    assert manual.reason_code == "SOURCE_NOT_RECOGNIZED"


def test_unaccepted_invoice_titles_are_excluded_and_filename_personal_hint_is_kept():
    title_mismatch = decide_document(
        document(),
        signal(buyer_name="其他公司"),
        profile(),
    )
    assert title_mismatch.outcome == FilterOutcome.EXCLUDE
    assert title_mismatch.reason_code == "BUYER_TITLE_NOT_ACCEPTED"

    blank_title = decide_document(
        document(),
        signal(
            buyer_name=None,
            buyer_tax_id="913000000000000001",
            title_status=BuyerTitleStatus.INDIVIDUAL_OR_BLANK,
        ),
        profile(),
    )
    assert blank_title.outcome == FilterOutcome.EXCLUDE
    assert blank_title.reason_code == "BUYER_TITLE_NOT_ACCEPTED"

    tax_id_only = decide_document(
        document(),
        signal(
            buyer_name=None,
            buyer_tax_id="913000000000000001",
            title_status=BuyerTitleStatus.PRESENT,
        ),
        profile(),
    )
    assert tax_id_only.outcome == FilterOutcome.EXCLUDE
    assert tax_id_only.reason_code == "BUYER_TITLE_NOT_ACCEPTED"

    unconfigured = decide_document(document(), signal(), None)
    assert unconfigured.outcome == FilterOutcome.REVIEW
    assert unconfigured.reason_code == "COMPANY_PROFILE_UNCONFIGURED"

    filename_only = decide_document(
        document(),
        signal(
            nature=ConsumptionNature.PERSONAL,
            scene=ExpenseScene.DINING,
            personal_evidence_source=PersonalEvidenceSource.FILENAME,
        ),
        profile(),
    )
    assert filename_only.outcome == FilterOutcome.KEEP
    assert filename_only.reason_code == "KEPT_BY_RELAXED_RULE"


def test_reference_match_is_positive_audit_evidence(monkeypatch):
    class FakeProvider:
        def chat_completion(self, _system, prompt, **_kwargs):
            assert "办公用品报销" in prompt
            return (
                '{"items":[{"document_id":1,"consumption_nature":"BUSINESS",'
                '"expense_scene":"OTHER","buyer_name":"示例科技有限公司",'
                '"buyer_tax_id":null,"buyer_title_status":"PRESENT",'
                '"personal_evidence_source":"NONE","confidence":0.95,'
                '"evidence":"办公用品"}]}'
            )

    class FakeLlmService:
        is_available = True
        active_provider = FakeProvider()

    monkeypatch.setattr(exclusion_service, "get_llm_service", lambda: FakeLlmService())
    invoice = document()
    invoice.file_name = "办公用品报销.pdf"
    reference = BatchReviewReference(
        version=2,
        parsed_rows=[{
            "sheet": "明细",
            "row": 3,
            "text": "办公用品报销 | 100.00",
            "normalized": "办公用品报销10000",
        }],
    )

    result = filter_documents([invoice], profile(), reference)[0]

    assert result.outcome == FilterOutcome.KEEP
    assert result.reason_code == "AUXILIARY_BUSINESS_MATCH"
    assert result.reference_version == 2
    assert "明细!第3行" in result.reference_evidence


def test_reference_match_cannot_override_unaccepted_invoice_title(monkeypatch):
    class FakeProvider:
        def chat_completion(self, _system, _prompt, **_kwargs):
            return (
                '{"items":[{"document_id":1,"consumption_nature":"BUSINESS",'
                '"expense_scene":"OTHER","buyer_name":"其他公司",'
                '"buyer_tax_id":"913000000000000001","buyer_title_status":"PRESENT",'
                '"personal_evidence_source":"NONE","confidence":0.95,'
                '"evidence":"办公用品"}]}'
            )

    class FakeLlmService:
        is_available = True
        active_provider = FakeProvider()

    monkeypatch.setattr(exclusion_service, "get_llm_service", lambda: FakeLlmService())
    invoice = document()
    invoice.file_name = "办公用品报销.pdf"
    reference = BatchReviewReference(
        version=2,
        parsed_rows=[{
            "sheet": "明细",
            "row": 3,
            "text": "办公用品报销 | 100.00",
            "normalized": "办公用品报销10000",
        }],
    )

    result = filter_documents([invoice], profile(), reference)[0]

    assert result.outcome == FilterOutcome.EXCLUDE
    assert result.reason_code == "BUYER_TITLE_NOT_ACCEPTED"


def test_reference_matching_priority_and_limit():
    invoice = document()
    invoice.invoice_number = "INV-2025-001"
    invoice.file_name = "客户会议办公用品.pdf"
    invoice.document_date = date(2025, 7, 8)
    invoice.amount = Decimal("88.50")
    invoice.counterparty = "示例商贸"
    reference = BatchReviewReference(
        version=1,
        parsed_rows=[
            {"sheet": "明细", "row": 2, "text": "INV-2025-001 办公用品", "normalized": "inv2025001办公用品"},
            {"sheet": "明细", "row": 3, "text": "客户会议办公用品", "normalized": "客户会议办公用品"},
            {"sheet": "明细", "row": 4, "text": "2025-07-08 88.50", "normalized": "202507088850"},
            {"sheet": "明细", "row": 5, "text": "示例商贸 88.50", "normalized": "示例商贸8850"},
        ],
    )

    matches = match_reference_rows(invoice, reference)

    assert len(matches) == 3
    assert [item["match_reason"] for item in matches] == [
        "发票号或交易单号匹配",
        "文件名匹配",
        "日期与金额匹配",
    ]


def test_reference_parser_rejects_invalid_and_oversized_files():
    with pytest.raises(ReviewReferenceValidationError):
        parse_review_reference(b"not a workbook", "明细.xlsx")
    with pytest.raises(ReviewReferenceValidationError):
        parse_review_reference(b"x" * (MAX_REFERENCE_FILE_SIZE + 1), "明细.xlsx")


def test_model_unavailable_marks_recognized_rows_for_review(monkeypatch):
    class FakeLlmService:
        is_available = False
        active_provider = None

    monkeypatch.setattr(exclusion_service, "get_llm_service", lambda: FakeLlmService())
    results = filter_documents([document()], profile())
    assert results[0].outcome == FilterOutcome.REVIEW
    assert results[0].reason_code == "LLM_UNAVAILABLE"


def test_invalid_model_ids_are_reviewed_without_aborting(monkeypatch):
    class FakeProvider:
        def chat_completion(self, _system, _prompt, **_kwargs):
            return '{"items":[{"document_id":999,"consumption_nature":"BUSINESS",' \
                '"expense_scene":"OTHER","buyer_name":null,"buyer_tax_id":null,' \
                '"buyer_title_status":"NOT_APPLICABLE","confidence":0.95,' \
                '"evidence":"测试"}]}'

    class FakeLlmService:
        is_available = True
        active_provider = FakeProvider()

    monkeypatch.setattr(exclusion_service, "get_llm_service", lambda: FakeLlmService())
    payment = document(
        source_folder=SourceFolder.PAYMENT_EVIDENCE,
        document_type=DocumentType.PAYMENT_SCREENSHOT,
    )
    results = filter_documents([payment], profile())
    assert results[0].outcome == FilterOutcome.REVIEW
    assert results[0].reason_code == "INVALID_FILTER_RESPONSE"


def test_model_exception_is_reviewed_without_aborting(monkeypatch):
    class FakeProvider:
        calls = 0

        def chat_completion(self, _system, _prompt, **_kwargs):
            self.calls += 1
            raise TimeoutError("timeout")

    class FakeLlmService:
        is_available = True
        active_provider = FakeProvider()

    monkeypatch.setattr(exclusion_service, "get_llm_service", lambda: FakeLlmService())
    results = filter_documents([document()], profile())
    assert results[0].outcome == FilterOutcome.REVIEW
    assert results[0].reason_code == "FILTER_MODEL_TIMEOUT"
    assert FakeLlmService.active_provider.calls == 2


def test_truncated_batch_is_split_and_retried(monkeypatch):
    class FakeProvider:
        def __init__(self):
            self.calls = []

        def chat_completion(self, _system, _prompt, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                from app.services.llm_service import LLMOutputTruncatedError

                raise LLMOutputTruncatedError("truncated")
            document_id = len(self.calls) - 1
            return (
                '{"items":[{"document_id":%d,"consumption_nature":"BUSINESS",'
                '"expense_scene":"OTHER","buyer_name":null,"buyer_tax_id":null,'
                '"buyer_title_status":"NOT_APPLICABLE","confidence":0.95,'
                '"evidence":"business payment"}]}' % document_id
            )

    class FakeLlmService:
        is_available = True
        active_provider = FakeProvider()

    monkeypatch.setattr(exclusion_service, "get_llm_service", lambda: FakeLlmService())
    documents = [
        document(
            document_id=document_id,
            source_folder=SourceFolder.PAYMENT_EVIDENCE,
            document_type=DocumentType.PAYMENT_SCREENSHOT,
        )
        for document_id in (1, 2)
    ]

    signals, errors = classify_filter_signals(documents)

    assert set(signals) == {1, 2}
    assert errors == {}
    assert len(FakeLlmService.active_provider.calls) == 3
    assert all(
        call == {"max_output_tokens": 8192, "json_mode": True}
        for call in FakeLlmService.active_provider.calls
    )


def test_qwen_chat_options_are_scoped_per_request():
    from app.services.llm_service import LLMConfigurationError, QwenProvider

    class FakeCompletions:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content='{"ok":true}'),
                    )
                ]
            )

    completions = FakeCompletions()
    provider = QwenProvider()
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
    )

    provider.chat_completion(
        "Return JSON",
        "Return JSON",
        max_output_tokens=8192,
        json_mode=True,
    )
    provider.chat_completion("system", "Reply OK")

    assert completions.calls[0]["max_tokens"] == 8192
    assert completions.calls[0]["response_format"] == {"type": "json_object"}
    assert completions.calls[1]["max_tokens"] == 1000
    assert "response_format" not in completions.calls[1]
    with pytest.raises(LLMConfigurationError):
        provider.chat_completion("system", "prompt", max_output_tokens=100000)
