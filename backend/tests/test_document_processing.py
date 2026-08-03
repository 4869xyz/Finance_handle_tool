import json
import re
from decimal import Decimal

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
    _coerce_classification_payload,
    _coerce_extraction_payload,
    _map_public_fields,
    _strict_json,
    classify_document_by_text,
    missing_critical_fields,
    process_document_content,
)
from app.services.document_prompts import (
    DOCUMENT_EXTRACTION_PROMPT_VERSION,
    DOCUMENT_TYPE_EXTRACTION_RULES,
    build_document_extraction_prompt,
)


def extraction_payload(**overrides):
    payload = {
        "document_date": "2025-03-05",
        "counterparty": "测试商户",
        "description": "办公用品",
        "amount": "100.00",
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
    assert "不得使用文件名、目录名、批次月份" in prompt
    assert "发票号码，不得填发票代码" in prompt
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
    def __init__(self, text):
        self.text = text

    def process_image(self, _data):
        return self.text, 99.0, []

    def process_document_pdf(self, _data):
        return self.text, 99.0, []


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


def _install_document_services(monkeypatch, *, text, provider, supports_vision=False):
    monkeypatch.setattr(
        document_service,
        "get_ocr_service",
        lambda: FakeOcr(text),
    )
    monkeypatch.setattr(
        document_service,
        "get_llm_service",
        lambda: FakeLlm(provider, supports_vision=supports_vision),
    )


def test_image_field_extraction_always_uses_vision_with_ocr_text(monkeypatch):
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

    process_document_content(
        file_data=b"original-image",
        file_type="jpg",
        source_folder=SourceFolder.PAYMENT_EVIDENCE,
    )

    assert not provider.chat_prompts
    assert len(provider.vision_prompts) == 1
    prompt, image_data, mime_type, options = provider.vision_prompts[0]
    assert text in prompt
    assert image_data == b"original-image"
    assert mime_type == "image/jpeg"
    assert (
        options["max_output_tokens"]
        == document_service.DOCUMENT_EXTRACTION_MAX_OUTPUT_TOKENS
    )


def test_pdf_field_extraction_keeps_text_only_model_call(monkeypatch):
    text = "电子发票\n开票日期：2025-03-05\n价税合计：100.00"
    provider = FakeProvider(json.dumps(extraction_payload(), ensure_ascii=False))
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
    assert validation["final_source"] == "LLM"
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
    assert validation["final_source"] == "LLM"
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
    assert result.extracted_fields_json["_validation"]["final_source"] == "LLM"
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
