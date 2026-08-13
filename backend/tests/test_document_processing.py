import json
import re
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models.batch import DocumentType, ProcessingStatus, SourceFolder
from app.services import document_service
from app.services.document_service import (
    AmountComponent,
    ClassificationModel,
    EvidenceAmount,
    ExtractionModel,
    ItemDetail,
    RecordExtraction,
    TransactionCandidate,
    TransferDetail,
    TripDetail,
    _clean_invoice_item_name,
    _coerce_classification_payload,
    _coerce_extraction_payload,
    _ensure_extraction_records,
    _extract_itinerary_by_rules,
    _map_public_fields,
    _promote_display_fields,
    _rule_extraction_acceptable,
    _strict_json,
    classify_document_by_text,
    extract_fields_by_rules,
    missing_critical_fields,
    process_document_content,
)
from app.services.document_prompts import (
    DOCUMENT_EXTRACTION_PROMPT_VERSION,
    DOCUMENT_TYPE_EXTRACTION_RULES,
    build_document_extraction_prompt,
)

GAODE_GOLDEN_CASES = json.loads(
    (
        Path(__file__).parent
        / "fixtures"
        / "gaode_itinerary_golden.json"
    ).read_text(encoding="utf-8")
)


def extraction_payload(**overrides):
    payload = {
        "document_date": "2025-03-05",
        "counterparty": "测试商户",
        "description": "办公用品",
        "amount": "100.00",
        "item_name": None,
        "invoice_number": None,
        "transaction_number": "TX123456",
        "provider": None,
        "seller": None,
        "payee": "测试商户",
        "merchant": "测试商户",
        "total_with_tax": None,
        "payment_amount": "100.00",
        "trip_amount": None,
        "order_number": None,
        "aggregate_amount": None,
        "amount_derivation": "EXPLICIT",
        "amount_evidence": "正文明确金额",
        "amount_components": [],
        "transactions": [],
        "confidence": 0.95,
    }
    payload.update(overrides)
    return payload


def test_deterministic_classification_obeys_type_precedence():
    assert classify_document_by_text("增值税 电子发票 发票号码") == (
        DocumentType.ELECTRONIC_INVOICE,
        1.0,
    )
    assert classify_document_by_text("网约车行程明细 电子发票 发票号码") == (
        DocumentType.ELECTRONIC_INVOICE,
        1.0,
    )
    assert classify_document_by_text("出租车 发票 纸质票") == (
        DocumentType.TAXI_INVOICE,
        1.0,
    )
    assert classify_document_by_text("微信支付 支付成功 交易单号 123") == (
        DocumentType.PAYMENT_SCREENSHOT,
        1.0,
    )


def test_strict_extraction_rejects_missing_and_extra_fields():
    payload = extraction_payload()
    payload["unexpected"] = "not allowed"
    with pytest.raises(ValidationError):
        ExtractionModel.model_validate(payload)

    missing = extraction_payload()
    del missing["confidence"]
    with pytest.raises(ValidationError):
        ExtractionModel.model_validate(missing)

    assert _strict_json("```json\n{\"a\": 1}\n```") == {"a": 1}


def test_extraction_prompt_v4_covers_supported_types_and_exact_schema():
    supported_types = {
        item.value
        for item in DocumentType
        if item != DocumentType.VIDEO
    }
    assert set(DOCUMENT_TYPE_EXTRACTION_RULES) == supported_types

    prompt = build_document_extraction_prompt(
        DocumentType.ELECTRONIC_INVOICE.value,
        "电子发票正文",
    )
    prompt_fields = set(re.findall(r'"([a-z_]+)"\s*:', prompt))
    expected_fields = (
        set(ExtractionModel.model_fields)
        | set(AmountComponent.model_fields)
        | set(TransactionCandidate.model_fields)
        | set(EvidenceAmount.model_fields)
        | set(ItemDetail.model_fields)
        | set(TripDetail.model_fields)
        | set(TransferDetail.model_fields)
        | set(RecordExtraction.model_fields)
    )
    assert prompt_fields == expected_fields
    assert f"字段提取 Prompt 版本：{DOCUMENT_EXTRACTION_PROMPT_VERSION}" in prompt
    assert DOCUMENT_EXTRACTION_PROMPT_VERSION == "4.3"
    assert "不得使用文件名、目录名、批次月份" in prompt
    assert "发票号码，不得填发票代码" in prompt
    assert "item_name 只取货物或应税劳务名称" in prompt or "item_name 只取" in prompt
    assert '"item_name"' in prompt
    assert '"currency"' not in prompt


@pytest.mark.parametrize(
    ("document_type", "expected_rule"),
    [
        (DocumentType.ELECTRONIC_INVOICE, "价税合计"),
        (DocumentType.PAYMENT_SCREENSHOT, "本次实际支付/转账金额"),
        (DocumentType.ORDER_SCREENSHOT, "订单合计或应付总额"),
        (DocumentType.ITINERARY, "实际出行、乘车、乘机"),
        (DocumentType.RECEIPT, "收据及其他资料取值规则"),
    ],
)
def test_extraction_prompt_injects_document_type_rules(document_type, expected_rule):
    prompt = build_document_extraction_prompt(document_type.value, "资料正文")
    assert expected_rule in prompt


def test_type_specific_public_mapping_and_critical_fields():
    extraction = ExtractionModel.model_validate(
        extraction_payload(
            seller="销售方",
            invoice_number="12345678",
            total_with_tax="88.60",
            payment_amount=None,
        )
    )
    mapped = _map_public_fields(
        DocumentType.ELECTRONIC_INVOICE,
        extraction,
    )
    assert mapped["amount"] == Decimal("88.60")
    assert mapped["counterparty"] == "销售方"
    assert "currency" not in mapped
    assert missing_critical_fields(DocumentType.ELECTRONIC_INVOICE, mapped) == []


@pytest.mark.parametrize(
    ("document_type", "overrides", "amount", "counterparty", "number"),
    [
        (
            DocumentType.PAYMENT_SCREENSHOT,
            {"payee": "收款公司", "payment_amount": "56.70"},
            Decimal("56.70"),
            "收款公司",
            "TX123456",
        ),
        (
            DocumentType.ORDER_SCREENSHOT,
            {"merchant": "订单商户", "aggregate_amount": "78.90", "order_number": "OD001"},
            Decimal("78.90"),
            "订单商户",
            "OD001",
        ),
        (
            DocumentType.ITINERARY,
            {"provider": "行程服务商", "trip_amount": "23.40"},
            Decimal("23.40"),
            "行程服务商",
            "TX123456",
        ),
    ],
)
def test_type_specific_model_fields_map_to_public_columns(
    document_type,
    overrides,
    amount,
    counterparty,
    number,
):
    extraction = ExtractionModel.model_validate(extraction_payload(**overrides))
    mapped = _map_public_fields(document_type, extraction)
    assert mapped["amount"] == amount
    assert mapped["counterparty"] == counterparty
    assert mapped["transaction_number"] == number


class FakeOcr:
    def __init__(self, text, layout_items=None):
        self.text = text
        self.layout_items = layout_items or []

    def process_image(self, _data):
        return self.text, 99.0, self.layout_items

    def process_document_pdf(self, _data):
        return self.text, 99.0, self.layout_items


class FakeProvider:
    def __init__(self, response):
        self.response = response
        self.calls = 0
        self.chat_prompts = []
        self.vision_prompts = []

    def chat_completion(self, _system, prompt, **_kwargs):
        self.calls += 1
        self.chat_prompts.append(prompt)
        return self.response

    def vision_completion(self, _system, prompt, image_data, mime_type, **kwargs):
        self.calls += 1
        self.vision_prompts.append((prompt, image_data, mime_type, kwargs))
        return self.response


class FakeLlm:
    def __init__(self, provider=None, available=True, supports_vision=False):
        self.active_provider = provider
        self.is_available = available
        self._supports_vision = supports_vision

    def supports_vision(self):
        return self._supports_vision


def _install_document_services(
    monkeypatch,
    *,
    text,
    provider,
    supports_vision=False,
    layout_items=None,
):
    monkeypatch.setattr(
        document_service,
        "get_ocr_service",
        lambda: FakeOcr(text, layout_items),
    )
    monkeypatch.setattr(
        document_service,
        "get_llm_service",
        lambda: FakeLlm(provider, supports_vision=supports_vision),
    )


def test_payment_image_with_sufficient_ocr_uses_text_llm(monkeypatch):
    text = (
        "微信支付\n支付成功\n支付时间：2025-03-05\n"
        "收款方：测试公司\n支付金额：100.00\n交易单号：TX123456"
    )
    provider = FakeProvider(json.dumps(extraction_payload(), ensure_ascii=False))
    _install_document_services(
        monkeypatch,
        text=text,
        provider=provider,
        supports_vision=True,
    )

    result = process_document_content(
        file_data=b"original-image",
        file_type="jpg",
        source_folder=SourceFolder.PAYMENT_EVIDENCE,
    )

    assert len(provider.chat_prompts) == 1
    assert text in provider.chat_prompts[0]
    assert not provider.vision_prompts
    assert result.extracted_fields_json["_validation"]["final_source"] == "LLM_TEXT"
    assert result.extracted_fields_json["_validation"]["escalation_path"] == "TEXT"


def test_payment_image_with_sparse_ocr_uses_vision(monkeypatch):
    text = "支付"
    provider = FakeProvider(json.dumps(extraction_payload(), ensure_ascii=False))
    _install_document_services(
        monkeypatch,
        text=text,
        provider=provider,
        supports_vision=True,
    )
    monkeypatch.setattr(
        document_service,
        "classify_document_by_text",
        lambda _text: (DocumentType.PAYMENT_SCREENSHOT, 1.0),
    )

    result = process_document_content(
        file_data=b"original-image",
        file_type="jpg",
        source_folder=SourceFolder.PAYMENT_EVIDENCE,
    )

    assert not provider.chat_prompts
    assert len(provider.vision_prompts) == 1
    prompt, image_data, mime_type, options = provider.vision_prompts[0]
    assert image_data == b"original-image"
    assert mime_type == "image/jpeg"
    assert (
        options["max_output_tokens"]
        == document_service.DOCUMENT_EXTRACTION_MAX_OUTPUT_TOKENS
    )
    assert result.extracted_fields_json["_validation"]["final_source"] == "LLM_VISION"
    assert "VISION" in result.extracted_fields_json["_validation"]["escalation_path"]


def test_pdf_incomplete_invoice_escalates_to_text_llm(monkeypatch):
    text = "电子发票\n开票日期：2025-03-05\n价税合计：100.00"
    provider = FakeProvider(
        json.dumps(
            extraction_payload(
                seller="销方公司",
                invoice_number="1234567890",
                total_with_tax="100.00",
                payment_amount=None,
                payee=None,
                merchant=None,
                transaction_number=None,
            ),
            ensure_ascii=False,
        )
    )
    _install_document_services(
        monkeypatch,
        text=text,
        provider=provider,
        supports_vision=True,
    )

    process_document_content(
        file_data=b"pdf-bytes",
        file_type="pdf",
        source_folder=SourceFolder.INVOICE,
    )

    assert len(provider.chat_prompts) == 1
    assert text in provider.chat_prompts[0]
    assert not provider.vision_prompts


def test_complete_invoice_uses_rule_primary_without_llm(monkeypatch):
    text = (
        "电子发票\n发票号码：123456789012\n开票日期：2025-03-05\n"
        "销售方名称：测试销方\n价税合计：¥100.00"
    )
    provider = FakeProvider(json.dumps(extraction_payload(), ensure_ascii=False))
    _install_document_services(
        monkeypatch,
        text=text,
        provider=provider,
        supports_vision=True,
    )

    class FakeExtractor:
        def extract_fields(self, _text, _lines=None):
            return {
                "invoice_number": "123456789012",
                "issue_date": "2025-03-05",
                "seller_name": "测试销方",
                "total_with_tax": "100.00",
                "amount": None,
                "item_name": "办公用品",
            }

    monkeypatch.setattr(
        document_service,
        "get_field_extractor",
        lambda: FakeExtractor(),
    )

    result = process_document_content(
        file_data=b"pdf-bytes",
        file_type="pdf",
        source_folder=SourceFolder.INVOICE,
    )

    assert provider.calls == 0
    assert result.processing_status == ProcessingStatus.RECOGNIZED
    assert result.invoice_number == "123456789012"
    assert result.counterparty == "测试销方"
    assert result.amount == Decimal("100.00")
    validation = result.extracted_fields_json["_validation"]
    assert validation["final_source"] == "RULE_PRIMARY"
    assert validation["escalation_path"] == "RULE"
    assert validation["fallback_used"] is False


def test_structured_invoice_rule_failure_upgrades_text_then_vision(monkeypatch):
    text = "电子发票\n内容不完整"
    text_payload = extraction_payload(
        document_date=None,
        counterparty=None,
        amount=None,
        invoice_number=None,
        transaction_number=None,
        seller=None,
        payee=None,
        merchant=None,
        total_with_tax=None,
        payment_amount=None,
        amount_derivation="NOT_FOUND",
        amount_evidence=None,
    )
    vision_payload = extraction_payload(
        seller="视觉销方",
        invoice_number="9988776655",
        total_with_tax="66.00",
        amount="66.00",
        payment_amount=None,
        payee=None,
        merchant=None,
        transaction_number=None,
    )

    class SequenceProvider:
        def __init__(self):
            self.chat_prompts = []
            self.vision_prompts = []
            self.calls = 0

        def chat_completion(self, _system, prompt, **_kwargs):
            self.calls += 1
            self.chat_prompts.append(prompt)
            return json.dumps(text_payload, ensure_ascii=False)

        def vision_completion(self, _system, prompt, image_data, mime_type, **kwargs):
            self.calls += 1
            self.vision_prompts.append((prompt, image_data, mime_type, kwargs))
            return json.dumps(vision_payload, ensure_ascii=False)

    provider = SequenceProvider()
    _install_document_services(
        monkeypatch,
        text=text,
        provider=provider,
        supports_vision=True,
    )
    monkeypatch.setattr(
        document_service,
        "classify_document_by_text",
        lambda _text: (DocumentType.ELECTRONIC_INVOICE, 1.0),
    )

    class EmptyExtractor:
        def extract_fields(self, _text, _lines=None):
            return {
                "invoice_number": None,
                "issue_date": None,
                "seller_name": None,
                "total_with_tax": None,
                "amount": None,
                "item_name": None,
            }

    monkeypatch.setattr(
        document_service,
        "get_field_extractor",
        lambda: EmptyExtractor(),
    )

    result = process_document_content(
        file_data=b"invoice-image",
        file_type="jpg",
        source_folder=SourceFolder.INVOICE,
    )

    assert len(provider.chat_prompts) == 1
    assert len(provider.vision_prompts) == 1
    validation = result.extracted_fields_json["_validation"]
    assert validation["final_source"] == "LLM_VISION"
    assert validation["escalation_path"] == "RULE→TEXT→VISION"
    assert result.invoice_number == "9988776655"
    assert result.counterparty == "视觉销方"


def test_valid_llm_result_is_used_without_running_rule_extraction(monkeypatch):
    text = (
        "微信支付\n支付成功\n支付时间：2025-03-05\n"
        "收款方：OCR商户\n支付金额：100.00\n交易单号：OCR-TX001"
    )
    payload = extraction_payload(
        counterparty="LLM商户",
        payee="LLM商户",
        amount="88.00",
        payment_amount="88.00",
        transaction_number="LLM-TX001",
    )
    provider = FakeProvider(json.dumps(payload, ensure_ascii=False))
    _install_document_services(monkeypatch, text=text, provider=provider)

    def fail_rule_extraction(*_args, **_kwargs):
        raise AssertionError("有效 LLM 响应不应运行 Python 字段规则")

    monkeypatch.setattr(
        document_service,
        "extract_fields_by_rules",
        fail_rule_extraction,
    )

    result = process_document_content(
        file_data=b"image",
        file_type="jpg",
        source_folder=SourceFolder.PAYMENT_EVIDENCE,
    )

    validation = result.extracted_fields_json["_validation"]
    assert result.processing_status == ProcessingStatus.RECOGNIZED
    assert result.counterparty == "LLM商户"
    assert result.amount == Decimal("88.00")
    assert result.transaction_number == "LLM-TX001"
    assert validation["final_source"] == "LLM_TEXT"
    assert validation["fallback_used"] is False
    assert "rule_fields" not in validation
    assert "matched_fields" not in validation
    assert "conflicts" not in validation
    assert "field_sources" not in validation


def test_partial_llm_result_keeps_null_fields_without_rule_fill(monkeypatch):
    text = (
        "微信支付\n支付成功\n支付时间：2025-03-05\n"
        "收款方：规则收款公司\n支付金额：100.00\n交易单号：TX123456"
    )
    payload = extraction_payload(
        counterparty=None,
        payee=None,
        transaction_number=None,
    )
    provider = FakeProvider(json.dumps(payload, ensure_ascii=False))
    _install_document_services(monkeypatch, text=text, provider=provider)

    def fail_rule_extraction(*_args, **_kwargs):
        raise AssertionError("部分字段为空不应触发整单规则兜底")

    monkeypatch.setattr(
        document_service,
        "extract_fields_by_rules",
        fail_rule_extraction,
    )

    result = process_document_content(
        file_data=b"image",
        file_type="jpg",
        source_folder=SourceFolder.PAYMENT_EVIDENCE,
    )

    validation = result.extracted_fields_json["_validation"]
    assert result.counterparty is None
    assert result.transaction_number is None
    assert result.processing_status == ProcessingStatus.NEEDS_MANUAL_REVIEW
    assert result.status_reason == "MISSING_CRITICAL_FIELDS"
    assert validation["final_source"] == "LLM_TEXT"
    assert validation["fallback_used"] is False


def test_llm_with_all_key_fields_empty_uses_whole_document_rule_fallback(monkeypatch):
    text = (
        "微信支付\n支付成功\n支付时间：2025-03-05\n"
        "收款方：测试商户\n支付金额：100.00\n交易单号：TX123456"
    )
    payload = extraction_payload(
        document_date=None,
        counterparty=None,
        amount=None,
        invoice_number=None,
        transaction_number=None,
        payee=None,
        merchant=None,
        payment_amount=None,
        aggregate_amount=None,
        order_number=None,
        amount_derivation="NOT_FOUND",
        amount_evidence=None,
    )
    provider = FakeProvider(json.dumps(payload, ensure_ascii=False))
    _install_document_services(monkeypatch, text=text, provider=provider)
    real_rule_extraction = document_service.extract_fields_by_rules
    rule_calls = []

    def tracked_rule_extraction(*args, **kwargs):
        rule_calls.append((args, kwargs))
        return real_rule_extraction(*args, **kwargs)

    monkeypatch.setattr(
        document_service,
        "extract_fields_by_rules",
        tracked_rule_extraction,
    )

    result = process_document_content(
        file_data=b"image",
        file_type="jpg",
        source_folder=SourceFolder.PAYMENT_EVIDENCE,
    )

    validation = result.extracted_fields_json["_validation"]
    assert len(rule_calls) == 1
    assert result.amount == Decimal("100.00")
    assert result.counterparty == "测试商户"
    assert result.transaction_number == "TX123456"
    assert validation["final_source"] == "OCR_RULE_FALLBACK"
    assert validation["fallback_used"] is True
    assert validation["fallback_reason"] == "NO_USABLE_LLM_FIELDS"
    assert "RULE_FALLBACK" in validation["escalation_path"]


def test_low_confidence_llm_result_does_not_switch_to_rule_fallback(monkeypatch):
    text = (
        "微信支付\n支付成功\n支付时间：2025-03-05\n"
        "收款方：规则商户\n支付金额：100.00\n交易单号：RULE-TX001"
    )
    payload = extraction_payload(
        counterparty="LLM商户",
        payee="LLM商户",
        transaction_number="LLM-TX001",
        confidence=0.1,
    )
    provider = FakeProvider(json.dumps(payload, ensure_ascii=False))
    _install_document_services(monkeypatch, text=text, provider=provider)

    def fail_rule_extraction(*_args, **_kwargs):
        raise AssertionError("低置信度 LLM 结果不应切换到规则兜底")

    monkeypatch.setattr(
        document_service,
        "extract_fields_by_rules",
        fail_rule_extraction,
    )

    result = process_document_content(
        file_data=b"image",
        file_type="jpg",
        source_folder=SourceFolder.PAYMENT_EVIDENCE,
    )

    assert result.counterparty == "LLM商户"
    assert result.status_reason == "LOW_EXTRACTION_CONFIDENCE"
    assert result.extracted_fields_json["_validation"]["final_source"] == "LLM_TEXT"
    assert result.extracted_fields_json["_validation"]["fallback_used"] is False


def test_calculated_amount_is_verified_and_requires_review(monkeypatch):
    text = (
        "微信支付\n支付成功\n支付时间：2025-03-05\n"
        "收款方：测试商户\n交易单号：TXCALC01\n项目甲10元 项目乙20元"
    )
    payload = extraction_payload(
        amount="30.00",
        payment_amount="30.00",
        amount_derivation="CALCULATED",
        amount_evidence="10.00 + 20.00 = 30.00",
        amount_components=[
            {"label": "项目甲", "amount": "10.00", "operation": "ADD"},
            {"label": "项目乙", "amount": "20.00", "operation": "ADD"},
        ],
    )
    provider = FakeProvider(json.dumps(payload, ensure_ascii=False))
    _install_document_services(monkeypatch, text=text, provider=provider)

    result = process_document_content(
        file_data=b"image",
        file_type="jpg",
        source_folder=SourceFolder.PAYMENT_EVIDENCE,
    )

    validation = result.extracted_fields_json["_validation"]
    assert result.amount == Decimal("30.00")
    assert result.status_reason == "CALCULATED_AMOUNT_REVIEW"
    assert validation["calculation_valid"] is True
    assert len(validation["amount_components"]) == 2


def test_invalid_calculated_amount_is_not_adopted(monkeypatch):
    text = (
        "微信支付\n支付成功\n支付时间：2025-03-05\n"
        "收款方：测试商户\n交易单号：TXCALC01\n项目甲10元 项目乙15元"
    )
    payload = extraction_payload(
        amount="30.00",
        payment_amount="30.00",
        amount_derivation="CALCULATED",
        amount_evidence="10.00 + 15.00 = 30.00",
        amount_components=[
            {"label": "项目甲", "amount": "10.00", "operation": "ADD"},
            {"label": "项目乙", "amount": "15.00", "operation": "ADD"},
        ],
    )
    provider = FakeProvider(json.dumps(payload, ensure_ascii=False))
    _install_document_services(monkeypatch, text=text, provider=provider)

    result = process_document_content(
        file_data=b"image",
        file_type="jpg",
        source_folder=SourceFolder.PAYMENT_EVIDENCE,
    )

    assert result.amount is None
    assert result.status_reason == "CALCULATED_AMOUNT_REVIEW"
    assert result.extracted_fields_json["_validation"]["calculation_valid"] is False


def test_multiple_transactions_without_total_requires_manual_review(monkeypatch, caplog):
    payload = extraction_payload(
        amount=None,
        payment_amount=None,
        aggregate_amount=None,
        transactions=[
            {
                "document_date": "2025-03-01",
                "counterparty": "甲",
                "amount": "10.00",
                "transaction_number": "TX000001",
            },
            {
                "document_date": "2025-03-02",
                "counterparty": "乙",
                "amount": "20.00",
                "transaction_number": "TX000002",
            },
        ],
    )
    provider = FakeProvider(json.dumps(payload, ensure_ascii=False))
    monkeypatch.setattr(
        document_service,
        "get_ocr_service",
        lambda: FakeOcr("微信支付\n支付成功\n交易单号 TX000001\n人民币"),
    )
    monkeypatch.setattr(
        document_service,
        "get_llm_service",
        lambda: FakeLlm(provider),
    )

    with caplog.at_level("INFO", logger="app.services.document_service"):
        result = process_document_content(
            file_data=b"image",
            file_type="jpg",
            source_folder=SourceFolder.PAYMENT_EVIDENCE,
        )
    assert provider.calls == 1
    assert result.processing_status == ProcessingStatus.NEEDS_MANUAL_REVIEW
    assert result.status_reason == "MULTIPLE_TRANSACTIONS"
    assert result.amount is None
    assert len(result.extracted_fields_json["transactions"]) == 2
    assert f"prompt version={DOCUMENT_EXTRACTION_PROMPT_VERSION}" in caplog.text


def test_incomplete_invoice_is_never_completed_from_filename_or_month(monkeypatch):
    monkeypatch.setattr(
        document_service,
        "get_ocr_service",
        lambda: FakeOcr("电子发票\n开票日期：2025-05-02\n价税合计：￥20.00"),
    )
    monkeypatch.setattr(
        document_service,
        "get_llm_service",
        lambda: FakeLlm(available=False),
    )

    result = process_document_content(
        file_data=b"image",
        file_type="jpg",
        source_folder=SourceFolder.INVOICE,
        file_name="某公司_888元_发票12345678.jpg",
    )
    assert result.document_date.isoformat() == "2025-05-02"
    assert result.processing_status == ProcessingStatus.NEEDS_MANUAL_REVIEW
    assert result.status_reason == "LLM_UNAVAILABLE"
    assert result.invoice_number is None
    validation = result.extracted_fields_json["_validation"]
    assert validation["final_source"] == "OCR_RULE_FALLBACK"
    assert validation["fallback_used"] is True
    assert validation["fallback_reason"] == "MODEL_NOT_CONFIGURED"


def test_filename_is_used_only_as_description_when_document_has_no_valid_content(
    monkeypatch,
):
    monkeypatch.setattr(
        document_service,
        "get_ocr_service",
        lambda: FakeOcr(""),
    )
    monkeypatch.setattr(
        document_service,
        "get_llm_service",
        lambda: FakeLlm(available=False),
    )

    result = process_document_content(
        file_data=b"image",
        file_type="jpg",
        source_folder=SourceFolder.PAYMENT_EVIDENCE,
        file_name="西安出差项目-体验活动.jpg",
    )

    assert result.description == "西安出差项目-体验活动"
    assert result.document_date is None
    assert result.counterparty is None
    assert result.amount is None
    assert result.invoice_number is None
    assert result.transaction_number is None
    assert result.status_reason == "LLM_UNAVAILABLE"


def test_filename_description_fallback_is_recorded_in_validation(monkeypatch):
    payload = extraction_payload(description=None)
    provider = FakeProvider(json.dumps(payload, ensure_ascii=False))
    _install_document_services(
        monkeypatch,
        text="微信支付\n支付成功\n交易单号 TX123456",
        provider=provider,
    )

    result = process_document_content(
        file_data=b"image",
        file_type="jpg",
        source_folder=SourceFolder.PAYMENT_EVIDENCE,
        file_name="客户接待晚餐.png",
    )

    assert result.description == "客户接待晚餐"
    assert result.extracted_fields_json["description"] == "客户接待晚餐"
    assert (
        result.extracted_fields_json["_validation"]["description_source"]
        == "FILENAME_FALLBACK"
    )


def test_coerce_classification_accepts_chinese_type_without_confidence():
    payload = _coerce_classification_payload({"document_type": "其他"})
    parsed = ClassificationModel.model_validate(payload)
    assert parsed.document_type == DocumentType.OTHER_DOCUMENT
    assert 0.0 <= parsed.confidence <= 1.0


def test_coerce_extraction_accepts_blank_strings_and_string_confidence():
    raw = extraction_payload(
        transaction_number="",
        order_number="",
        trip_amount="0",
        confidence="0",
        currency="USD",
        unexpected="drop-me",
    )
    coerced = _coerce_extraction_payload(raw)
    assert "currency" not in coerced
    assert "unexpected" not in coerced
    parsed = ExtractionModel.model_validate(coerced)
    assert parsed.transaction_number is None
    assert parsed.order_number is None
    assert parsed.trip_amount == Decimal("0")
    assert parsed.confidence == 0.0


def test_invalid_extraction_json_falls_back_to_rules(monkeypatch):
    provider = FakeProvider("这不是合法JSON")
    monkeypatch.setattr(
        document_service,
        "get_ocr_service",
        lambda: FakeOcr(
            "电子发票\n发票号码：25327000000520047218\n"
            "销售方名称：测试公司\n开票日期：2025-05-02\n价税合计：￥28.26\n人民币"
        ),
    )
    monkeypatch.setattr(
        document_service,
        "get_llm_service",
        lambda: FakeLlm(provider),
    )

    result = process_document_content(
        file_data=b"image",
        file_type="jpg",
        source_folder=SourceFolder.INVOICE,
    )
    assert provider.calls == 1
    assert result.document_type == DocumentType.ELECTRONIC_INVOICE
    assert result.status_reason != "INVALID_MODEL_RESPONSE"
    assert result.invoice_number == "25327000000520047218"
    assert result.processing_status in {
        ProcessingStatus.RECOGNIZED,
        ProcessingStatus.NEEDS_MANUAL_REVIEW,
    }
    validation = result.extracted_fields_json["_validation"]
    assert validation["final_source"] == "OCR_RULE_FALLBACK"
    assert validation["fallback_used"] is True
    assert validation["fallback_reason"] == "INVALID_MODEL_RESPONSE"


def test_extraction_api_exception_uses_whole_document_rule_fallback(monkeypatch):
    class BoomProvider:
        def chat_completion(self, _system, _prompt, **_kwargs):
            raise RuntimeError("temporary model outage")

    text = (
        "电子发票\n发票号码：25327000000520047218\n"
        "销售方名称：测试公司\n开票日期：2025-05-02\n价税合计：￥28.26"
    )
    monkeypatch.setattr(
        document_service,
        "get_ocr_service",
        lambda: FakeOcr(text),
    )
    monkeypatch.setattr(
        document_service,
        "get_llm_service",
        lambda: FakeLlm(BoomProvider()),
    )

    result = process_document_content(
        file_data=b"image",
        file_type="jpg",
        source_folder=SourceFolder.INVOICE,
    )

    assert result.invoice_number == "25327000000520047218"
    assert result.amount == Decimal("28.26")
    validation = result.extracted_fields_json["_validation"]
    assert validation["final_source"] == "OCR_RULE_FALLBACK"
    assert validation["fallback_used"] is True
    assert validation["fallback_reason"] == "LLM_UNAVAILABLE"


def test_llm_api_exception_detail_is_preserved_for_classification(monkeypatch):
    class BoomProvider:
        def chat_completion(self, _system, _prompt, **_kwargs):
            raise RuntimeError("model_not_found qwen3.5-vl")

    monkeypatch.setattr(
        document_service,
        "get_ocr_service",
        lambda: FakeOcr("普通收据 购物小票"),
    )
    # Force LLM classification path by using ambiguous text without strong rule hits
    monkeypatch.setattr(
        document_service,
        "get_ocr_service",
        lambda: FakeOcr("这是一份需要模型判断的模糊资料正文"),
    )
    monkeypatch.setattr(
        document_service,
        "get_llm_service",
        lambda: FakeLlm(BoomProvider()),
    )

    result = process_document_content(
        file_data=b"image",
        file_type="jpg",
        source_folder=SourceFolder.PAYMENT_EVIDENCE,
    )
    assert result.status_reason == "LLM_UNAVAILABLE"
    assert "RuntimeError" in (result.status_detail or "")
    assert "model_not_found" in (result.status_detail or "")


def test_promote_display_fields_joins_invoice_items_and_trip_details():
    extraction = ExtractionModel.model_validate(
        extraction_payload(
            item_name=None,
            payment_amount=None,
            amount="88.00",
            total_with_tax="88.00",
            records=[
                {
                    "record_id": "R1",
                    "record_type": "INVOICE",
                    "document_date": "2025-03-05",
                    "counterparty": "测试商户",
                    "description": "办公用品",
                    "invoice_number": "INV001",
                    "order_number": None,
                    "transaction_number": None,
                    "primary_amount": {
                        "value": "50.00",
                        "evidence": "价税合计50.00",
                        "kind": "TOTAL_WITH_TAX",
                    },
                    "items": [
                        {
                            "name_visible": "打印纸",
                            "name_complete": True,
                            "item_amount": {
                                "value": "30.00",
                                "evidence": "30.00",
                                "kind": "ITEM",
                            },
                        },
                        {
                            "name_visible": "签字笔",
                            "name_complete": True,
                            "item_amount": {
                                "value": "20.00",
                                "evidence": "20.00",
                                "kind": "ITEM",
                            },
                        },
                    ],
                    "manual_review": False,
                    "review_reasons": [],
                    "confidence": 0.9,
                },
                {
                    "record_id": "R2",
                    "record_type": "TRIP",
                    "document_date": "2025-03-06",
                    "counterparty": "滴滴",
                    "description": "打车",
                    "invoice_number": None,
                    "order_number": None,
                    "transaction_number": None,
                    "primary_amount": {
                        "value": "38.00",
                        "evidence": "车费38.00",
                        "kind": "TRIP_TOTAL",
                    },
                    "trip": {
                        "request_time": "申请 09:00",
                        "start_time": "09:10",
                        "end_time": "09:40",
                        "origin": "公司门口",
                        "destination": "火车站",
                        "stated_total": {
                            "value": "38.00",
                            "evidence": "合计38.00",
                            "kind": "TRIP_TOTAL",
                        },
                        "detail_amounts": [],
                    },
                    "manual_review": False,
                    "review_reasons": [],
                    "confidence": 0.9,
                },
            ],
        )
    )
    display = _promote_display_fields(extraction)
    assert display["item_name"] == "打印纸; 签字笔"
    assert display["trip_origin"] == "公司门口"
    assert display["trip_destination"] == "火车站"
    assert display["trip_time_text"] == "09:10至09:40"


def test_payment_public_fields_keep_transaction_and_order_separate():
    extraction = ExtractionModel.model_validate(
        extraction_payload(
            order_number="ORD-9988",
            transaction_number="TX-123456",
            payment_amount="100.00",
            amount="100.00",
            records=[],
        )
    )
    _ensure_extraction_records(DocumentType.PAYMENT_SCREENSHOT, extraction)
    assert extraction.records[0].order_number == "ORD-9988"
    assert extraction.records[0].transaction_number == "TX-123456"
    mapped = _map_public_fields(DocumentType.PAYMENT_SCREENSHOT, extraction)
    assert mapped["transaction_number"] == "TX-123456"
    assert mapped["order_number"] == "ORD-9988"
    assert mapped["item_name"] is None


def test_clean_invoice_item_name_strips_rate_and_qty_debris():
    assert (
        _clean_invoice_item_name(
            "运输服务*客运服务费 21.2621359223301 1 21.26 3% 0.64"
        )
        == "运输服务*客运服务费"
    )
    assert _clean_invoice_item_name("*电信服务*服务费次 1 60.377 6% 3.62") in {
        "电信服务*服务费次",
        "*电信服务*服务费次",
    }


def test_gaode_itinerary_rules_extract_od_time_not_header_as_party():
    text = (
        "高德地图—打车——行程单\n"
        "车型 上车时间 城市 起点 终点 金额\n"
        "2025-04-15 08:30 经济型 深圳 科技园地铁站 机场T3航站楼 21.90\n"
        "行程费用合计：21.90\n"
        "曹操出行"
    )
    extraction = _extract_itinerary_by_rules(text, 99.0)
    mapped = _map_public_fields(DocumentType.ITINERARY, extraction)
    assert extraction.counterparty == "曹操出行"
    assert "车型" not in (extraction.counterparty or "")
    assert mapped["trip_origin"] == "科技园地铁站"
    assert mapped["trip_destination"] == "机场T3航站楼"
    assert mapped["trip_time_text"] == "08:30"
    assert mapped["amount"] == Decimal("21.90")
    assert _rule_extraction_acceptable(
        DocumentType.ITINERARY,
        extraction,
        mapped,
        0.75,
    )


def test_gaode_title_fragments_are_not_trip_locations():
    text = (
        "高德地图—打车——行程单\n"
        "AMAP ITINERARY\n"
        "申请时间：2025-05-20 行程时间：2025-04-17 18:55至2025-04-17 19:30\n"
        "共计1单行程，合计21.90元\n"
        "序号 服务商 车型 上车时间 城市 起点 终点 金额\n"
    )
    extraction = _extract_itinerary_by_rules(text, 99.0)
    mapped = _map_public_fields(DocumentType.ITINERARY, extraction)
    assert mapped["trip_origin"] is None or "高德" not in mapped["trip_origin"]
    assert mapped["trip_destination"] != "行程单"
    assert not _rule_extraction_acceptable(
        DocumentType.ITINERARY,
        extraction,
        mapped,
        0.75,
    )


def test_gaode_text_fallback_without_layout_keeps_single_line_od():
    text = (
        "高德地图—打车——行程单\n"
        "共计1单行程，合计21.90元\n"
        "序号 服务商 车型 上车时间 城市 起点 终点 金额\n"
        "1 曹操出行 经济型 2025-04-15 08:30 深圳 科技园地铁站 机场T3航站楼 21.90元\n"
    )
    extraction = _extract_itinerary_by_rules(text, 99.0, layout_items=None)
    mapped = _map_public_fields(DocumentType.ITINERARY, extraction)
    assert mapped["trip_origin"] == "科技园地铁站"
    assert mapped["trip_destination"] == "机场T3航站楼"
    assert mapped["trip_time_text"] == "2025-04-15 08:30"
    assert mapped["amount"] == Decimal("21.90")
    assert _rule_extraction_acceptable(
        DocumentType.ITINERARY,
        extraction,
        mapped,
        0.75,
    )


@pytest.mark.parametrize(
    "case",
    GAODE_GOLDEN_CASES,
    ids=[case["case_id"] for case in GAODE_GOLDEN_CASES],
)
def test_gaode_layout_golden_cases_use_rule_primary_without_llm(
    monkeypatch,
    case,
):
    provider = FakeProvider("{}")
    _install_document_services(
        monkeypatch,
        text=case["text"],
        provider=provider,
        supports_vision=False,
        layout_items=case["layout_items"],
    )

    result = process_document_content(
        file_data=b"native-pdf",
        file_type="pdf",
        source_folder=SourceFolder.PAYMENT_EVIDENCE,
        file_name=case["file_name"],
    )

    expected = case["expected"]
    assert result.processing_status == ProcessingStatus.RECOGNIZED
    assert result.counterparty == expected["provider"]
    assert result.amount == Decimal(expected["amount"])
    assert result.trip_origin == expected["trip_origin"]
    assert result.trip_destination == expected["trip_destination"]
    assert result.trip_time_text == expected["trip_time_text"]
    assert result.extracted_fields_json["_validation"]["final_source"] == "RULE_PRIMARY"
    assert result.extracted_fields_json["_validation"]["escalation_path"] == "RULE"
    assert provider.calls == 0

    records = result.extracted_fields_json["records"]
    assert [record["trip"]["origin"] for record in records] == expected["origins"]
    assert [
        record["trip"]["destination"] for record in records
    ] == expected["destinations"]
    assert [
        record["trip"]["start_time"] for record in records
    ] == expected["start_times"]
    assert records[-1]["trip"]["end_time"] == expected["end_time"]


def test_gaode_layout_record_count_mismatch_is_not_rule_primary():
    case = GAODE_GOLDEN_CASES[0]
    text = re.sub(r"共计1单行程", "共计2单行程", case["text"])
    extraction = _extract_itinerary_by_rules(
        text,
        99.0,
        case["layout_items"],
    )
    mapped = _map_public_fields(DocumentType.ITINERARY, extraction)

    assert extraction.records[0].manual_review
    assert "GAODE_RECORD_COUNT_MISMATCH" in extraction.records[0].review_reasons
    assert not _rule_extraction_acceptable(
        DocumentType.ITINERARY,
        extraction,
        mapped,
        0.75,
    )


def test_itinerary_without_od_is_not_rule_primary_acceptable():
    text = (
        "高德地图—打车——行程单\n"
        "车型 上车时间 城市 起点 终点 金额\n"
        "行程费用合计：21.90\n"
    )
    extraction = _extract_itinerary_by_rules(text, 99.0)
    _ensure_extraction_records(DocumentType.ITINERARY, extraction)
    mapped = _map_public_fields(DocumentType.ITINERARY, extraction)
    assert not _rule_extraction_acceptable(
        DocumentType.ITINERARY,
        extraction,
        mapped,
        0.75,
    )


def test_itinerary_missing_od_escalates_to_text_llm(monkeypatch):
    text = (
        "高德地图—打车——行程单\n"
        "车型 上车时间 城市 起点 终点 金额\n"
        "行程费用合计：21.90\n"
    )
    payload = extraction_payload(
        provider="曹操出行",
        counterparty="曹操出行",
        trip_amount="21.90",
        amount="21.90",
        payment_amount=None,
        payee=None,
        merchant=None,
        transaction_number=None,
        records=[
            {
                "record_id": "R1",
                "record_type": "TRIP",
                "document_date": "2025-04-15",
                "counterparty": "曹操出行",
                "description": "打车",
                "invoice_number": None,
                "order_number": None,
                "transaction_number": None,
                "primary_amount": {
                    "value": "21.90",
                    "evidence": "行程费用合计",
                    "kind": "TRIP_TOTAL",
                },
                "item_subtotal": {"value": None, "evidence": None, "kind": "UNKNOWN"},
                "original_amount": {"value": None, "evidence": None, "kind": "UNKNOWN"},
                "discount_amount": {"value": None, "evidence": None, "kind": "UNKNOWN"},
                "items": [],
                "trip": {
                    "request_time": None,
                    "start_time": "08:30",
                    "end_time": None,
                    "service_provider": "曹操出行",
                    "vehicle_type": None,
                    "city": "深圳",
                    "origin": "科技园",
                    "destination": "机场",
                    "stated_total": {
                        "value": "21.90",
                        "evidence": "行程费用合计",
                        "kind": "TRIP_TOTAL",
                    },
                    "detail_amounts": [],
                },
                "transfer": None,
                "manual_review": False,
                "review_reasons": [],
                "confidence": 0.9,
            }
        ],
    )
    provider = FakeProvider(json.dumps(payload, ensure_ascii=False))
    _install_document_services(
        monkeypatch,
        text=text,
        provider=provider,
        supports_vision=False,
    )
    monkeypatch.setattr(
        document_service,
        "classify_document_by_text",
        lambda _text: (DocumentType.ITINERARY, 1.0),
    )

    result = process_document_content(
        file_data=b"pdf-bytes",
        file_type="pdf",
        source_folder=SourceFolder.PAYMENT_EVIDENCE,
    )

    validation = result.extracted_fields_json["_validation"]
    assert validation["final_source"] == "LLM_TEXT"
    assert "TEXT" in validation["escalation_path"]
    assert validation["final_source"] != "RULE_PRIMARY"
    assert result.trip_origin == "科技园"
    assert result.trip_destination == "机场"
    assert len(provider.chat_prompts) == 1


def test_invoice_rules_clean_item_name_into_display_field(monkeypatch):
    text = (
        "电子发票\n发票号码：25442000000290843167\n开票日期：2025-04-15\n"
        "销售方名称：测试销方\n"
        "*运输服务*客运服务费 21.2621359223301 1 21.26 3% 0.64\n"
        "价税合计：¥21.90"
    )
    provider = FakeProvider(json.dumps(extraction_payload(), ensure_ascii=False))
    _install_document_services(
        monkeypatch,
        text=text,
        provider=provider,
        supports_vision=True,
    )

    class FakeExtractor:
        def extract_fields(self, _text, _lines=None):
            return {
                "invoice_number": "25442000000290843167",
                "issue_date": "2025-04-15",
                "seller_name": "测试销方",
                "total_with_tax": "21.90",
                "amount": None,
                "item_name": "运输服务*客运服务费 21.2621359223301 1 21.26 3% 0.64",
            }

    monkeypatch.setattr(
        document_service,
        "get_field_extractor",
        lambda: FakeExtractor(),
    )

    result = process_document_content(
        file_data=b"pdf-bytes",
        file_type="pdf",
        source_folder=SourceFolder.INVOICE,
    )

    assert provider.calls == 0
    assert result.invoice_number == "25442000000290843167"
    assert result.amount == Decimal("21.90")
    assert result.item_name == "运输服务*客运服务费"
    assert "3%" not in (result.item_name or "")
    assert result.extracted_fields_json["_validation"]["final_source"] == "RULE_PRIMARY"


def test_payment_rules_split_order_and_transaction_numbers():
    text = (
        "微信支付\n支付成功\n支付时间：2025-03-05\n"
        "收款方：农家小炒\n支付金额：102.00\n"
        "交易单号：10001073012025041500336997573149\n"
        "商家订单号：ORDER20250415001"
    )
    extraction = extract_fields_by_rules(text, 99.0)
    assert extraction.transaction_number == "10001073012025041500336997573149"
    assert extraction.order_number == "ORDER20250415001"
    assert extraction.transaction_number != extraction.order_number
