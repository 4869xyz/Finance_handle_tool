import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models.batch import DocumentType, ProcessingStatus, SourceFolder
from app.services import document_service
from app.services.document_service import (
    ClassificationModel,
    ExtractionModel,
    _coerce_classification_payload,
    _coerce_extraction_payload,
    _map_public_fields,
    _strict_json,
    classify_document_by_text,
    missing_critical_fields,
    process_document_content,
)


def extraction_payload(**overrides):
    payload = {
        "document_date": "2025-03-05",
        "counterparty": "测试商户",
        "description": "办公用品",
        "amount": "100.00",
        "currency": "CNY",
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
        "价税合计 ￥88.60 人民币",
    )
    assert mapped["amount"] == Decimal("88.60")
    assert mapped["counterparty"] == "销售方"
    assert missing_critical_fields(DocumentType.ELECTRONIC_INVOICE, mapped) == []

    without_currency_evidence = _map_public_fields(
        DocumentType.ELECTRONIC_INVOICE,
        extraction,
        "价税合计 88.60",
    )
    assert without_currency_evidence["currency"] is None


class FakeOcr:
    def __init__(self, text):
        self.text = text

    def process_image(self, _data):
        return self.text, 99.0, []


class FakeProvider:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def chat_completion(self, _system, _prompt, **_kwargs):
        self.calls += 1
        return self.response


class FakeLlm:
    def __init__(self, provider=None, available=True):
        self.active_provider = provider
        self.is_available = available

    def supports_vision(self):
        return False


def test_multiple_transactions_without_total_requires_manual_review(monkeypatch):
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
    )
    assert result.document_date.isoformat() == "2025-05-02"
    assert result.processing_status == ProcessingStatus.NEEDS_MANUAL_REVIEW
    assert result.status_reason == "LLM_UNAVAILABLE"
    assert result.invoice_number is None


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
        unexpected="drop-me",
    )
    coerced = _coerce_extraction_payload(raw)
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
