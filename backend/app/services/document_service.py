import json
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.config import get_settings
from app.models.batch import DocumentType, ProcessingStatus, SourceFolder
from app.services.document_prompts import (
    DOCUMENT_CLASSIFICATION_PROMPT,
    DOCUMENT_EXTRACTION_PROMPT,
    DOCUMENT_JSON_SYSTEM_PROMPT,
)
from app.services.llm_service import get_llm_service
from app.services.ocr_service import get_ocr_service


class ClassificationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_type: DocumentType
    confidence: float = Field(ge=0, le=1)

    @field_validator("document_type")
    @classmethod
    def reject_video(cls, value: DocumentType) -> DocumentType:
        if value == DocumentType.VIDEO:
            raise ValueError("模型不能把可处理文档分类为视频")
        return value


class TransactionCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_date: date | None
    counterparty: str | None
    amount: Decimal | None
    transaction_number: str | None

    @field_validator("amount")
    @classmethod
    def normalize_amount(cls, value: Decimal | None) -> Decimal | None:
        return abs(value) if value is not None else None


class ExtractionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_date: date | None
    counterparty: str | None
    description: str | None
    amount: Decimal | None
    currency: str | None
    invoice_number: str | None
    transaction_number: str | None
    provider: str | None
    seller: str | None
    payee: str | None
    merchant: str | None
    total_with_tax: Decimal | None
    payment_amount: Decimal | None
    trip_amount: Decimal | None
    order_number: str | None
    aggregate_amount: Decimal | None
    transactions: list[TransactionCandidate]
    confidence: float = Field(ge=0, le=1)

    @field_validator(
        "amount",
        "total_with_tax",
        "payment_amount",
        "trip_amount",
        "aggregate_amount",
    )
    @classmethod
    def normalize_amounts(cls, value: Decimal | None) -> Decimal | None:
        return abs(value) if value is not None else None

    @field_validator(
        "counterparty",
        "description",
        "currency",
        "invoice_number",
        "transaction_number",
        "provider",
        "seller",
        "payee",
        "merchant",
        "order_number",
    )
    @classmethod
    def blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


@dataclass
class DocumentProcessingResult:
    document_type: DocumentType = DocumentType.UNKNOWN
    processing_status: ProcessingStatus = ProcessingStatus.NEEDS_MANUAL_REVIEW
    status_reason: str | None = None
    status_detail: str | None = None
    raw_text: str | None = None
    classification_confidence: Decimal | None = None
    extraction_confidence: Decimal | None = None
    document_date: date | None = None
    counterparty: str | None = None
    description: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    invoice_number: str | None = None
    transaction_number: str | None = None
    extracted_fields_json: dict[str, Any] | None = None
    classification_raw_response: str | None = None
    extraction_raw_response: str | None = None
    model_provider: str | None = None
    model_name: str | None = None
    missing_critical_fields: list[str] = field(default_factory=list)

    def as_update_values(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in vars(self).items()
            if key != "missing_critical_fields"
        }


INVOICE_TYPES = {
    DocumentType.VAT_INVOICE,
    DocumentType.ELECTRONIC_INVOICE,
    DocumentType.TAXI_INVOICE,
}
ITINERARY_TYPES = {
    DocumentType.ITINERARY,
    DocumentType.TRAIN_OR_AIR_TICKET,
}


def _strict_json(content: str) -> dict[str, Any]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
        if not match:
            raise ValueError("模型响应的 Markdown 代码块不完整")
        cleaned = match.group(1)
    value = json.loads(cleaned)
    if not isinstance(value, dict):
        raise ValueError("模型响应必须是 JSON 对象")
    return value


_DOCUMENT_TYPE_ALIASES = {
    "其他": DocumentType.OTHER_DOCUMENT.value,
    "其它": DocumentType.OTHER_DOCUMENT.value,
    "未知": DocumentType.UNKNOWN.value,
    "电子发票": DocumentType.ELECTRONIC_INVOICE.value,
    "数电发票": DocumentType.ELECTRONIC_INVOICE.value,
    "电子普通发票": DocumentType.ELECTRONIC_INVOICE.value,
    "增值税发票": DocumentType.VAT_INVOICE.value,
    "增值税专用发票": DocumentType.VAT_INVOICE.value,
    "出租车发票": DocumentType.TAXI_INVOICE.value,
    "行程单": DocumentType.ITINERARY.value,
    "行程明细": DocumentType.ITINERARY.value,
    "火车票": DocumentType.TRAIN_OR_AIR_TICKET.value,
    "机票": DocumentType.TRAIN_OR_AIR_TICKET.value,
    "支付凭证": DocumentType.PAYMENT_SCREENSHOT.value,
    "支付截图": DocumentType.PAYMENT_SCREENSHOT.value,
    "订单": DocumentType.ORDER_SCREENSHOT.value,
    "订单截图": DocumentType.ORDER_SCREENSHOT.value,
    "收据": DocumentType.RECEIPT.value,
    "小票": DocumentType.RECEIPT.value,
    "境外发票": DocumentType.OVERSEAS_INVOICE_OR_RECEIPT.value,
}


_CLASSIFICATION_FIELDS = {"document_type", "confidence"}
_EXTRACTION_FIELDS = {
    "document_date",
    "counterparty",
    "description",
    "amount",
    "currency",
    "invoice_number",
    "transaction_number",
    "provider",
    "seller",
    "payee",
    "merchant",
    "total_with_tax",
    "payment_amount",
    "trip_amount",
    "order_number",
    "aggregate_amount",
    "transactions",
    "confidence",
}
_AMOUNT_FIELDS = {
    "amount",
    "total_with_tax",
    "payment_amount",
    "trip_amount",
    "aggregate_amount",
}
_TRANSACTION_FIELDS = {
    "document_date",
    "counterparty",
    "amount",
    "transaction_number",
}


def _empty_to_none(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _coerce_confidence(value: Any, default: float = 0.5) -> float:
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number > 1.0 and number <= 100.0:
        number = number / 100.0
    return max(0.0, min(1.0, number))


def _coerce_document_type(value: Any) -> Any:
    if value is None:
        return DocumentType.UNKNOWN.value
    text = str(value).strip()
    if not text:
        return DocumentType.UNKNOWN.value
    if text in DocumentType.__members__:
        return text
    mapped = _DOCUMENT_TYPE_ALIASES.get(text) or _DOCUMENT_TYPE_ALIASES.get(
        re.sub(r"\s+", "", text)
    )
    if mapped:
        return mapped
    upper = text.upper().replace("-", "_").replace(" ", "_")
    if upper in DocumentType.__members__:
        return upper
    return text


def _coerce_amount_value(value: Any) -> Any:
    value = _empty_to_none(value)
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return value
    text = str(value).strip()
    cleaned = re.sub(r"[¥￥$€,，\s]", "", text)
    if not cleaned:
        return None
    return cleaned


def _coerce_classification_payload(raw: dict[str, Any]) -> dict[str, Any]:
    payload = {key: raw.get(key) for key in _CLASSIFICATION_FIELDS}
    payload["document_type"] = _coerce_document_type(payload.get("document_type"))
    payload["confidence"] = _coerce_confidence(payload.get("confidence"))
    return payload


def _coerce_transaction(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    item = {key: _empty_to_none(raw.get(key)) for key in _TRANSACTION_FIELDS}
    item["amount"] = _coerce_amount_value(raw.get("amount"))
    return item


def _coerce_extraction_payload(raw: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in _EXTRACTION_FIELDS:
        if key == "transactions":
            continue
        if key in _AMOUNT_FIELDS:
            payload[key] = _coerce_amount_value(raw.get(key))
        else:
            payload[key] = _empty_to_none(raw.get(key))
    transactions = raw.get("transactions")
    if transactions is None:
        payload["transactions"] = []
    elif isinstance(transactions, list):
        payload["transactions"] = [
            item for item in (_coerce_transaction(row) for row in transactions) if item
        ]
    else:
        payload["transactions"] = []
    payload["confidence"] = _coerce_confidence(payload.get("confidence"))
    return payload


def _safe_exception_detail(prefix: str, exc: Exception, limit: int = 180) -> str:
    message = f"{prefix}：{exc.__class__.__name__}: {exc}"
    return message[:limit]


def classify_document_by_text(text: str) -> tuple[DocumentType, float] | None:
    """Return only high-certainty, content-based classifications."""
    normalized = re.sub(r"\s+", "", text).lower()
    if not normalized:
        return None

    has_invoice = "发票" in normalized
    if has_invoice and any(
        keyword in normalized
        for keyword in ("电子发票", "数电发票", "全面数字化", "电子普通发票")
    ):
        return DocumentType.ELECTRONIC_INVOICE, 1.0
    if has_invoice and any(keyword in normalized for keyword in ("出租车", "taxi")):
        return DocumentType.TAXI_INVOICE, 1.0
    if has_invoice and "增值税" in normalized:
        return DocumentType.VAT_INVOICE, 1.0

    if "行程单" in normalized or "行程明细" in normalized:
        return DocumentType.ITINERARY, 1.0
    if any(keyword in normalized for keyword in ("航空运输电子客票", "火车票", "铁路电子客票")):
        return DocumentType.TRAIN_OR_AIR_TICKET, 1.0

    payment_hits = sum(
        keyword in normalized
        for keyword in (
            "支付成功",
            "交易单号",
            "转账成功",
            "付款成功",
            "收款方",
            "微信支付",
            "支付宝",
        )
    )
    order_hits = sum(
        keyword in normalized
        for keyword in ("订单编号", "订单号", "下单时间", "订单金额", "商品详情")
    )
    if payment_hits >= 2 and payment_hits > order_hits:
        return DocumentType.PAYMENT_SCREENSHOT, 1.0
    if order_hits >= 2 and order_hits > payment_hits:
        return DocumentType.ORDER_SCREENSHOT, 1.0

    if any(keyword in normalized for keyword in ("收据", "购物小票", "销售小票")):
        return DocumentType.RECEIPT, 1.0
    if "invoice" in normalized and any(
        keyword in normalized for keyword in ("usd", "eur", "hkd", "jpy", "$", "€")
    ):
        return DocumentType.OVERSEAS_INVOICE_OR_RECEIPT, 1.0
    return None


def _first_match(patterns: list[str], text: str) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def _decimal_or_none(value: str | None) -> Decimal | None:
    if not value:
        return None
    try:
        return abs(Decimal(value.replace(",", "")))
    except InvalidOperation:
        return None


def extract_fields_by_rules(text: str, ocr_confidence: float) -> ExtractionModel:
    date_value = _first_match(
        [
            r"(?:开票日期|交易时间|支付时间|下单时间|行程日期|日期)\s*[:：]?\s*(\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?)",
            r"\b(\d{4}-\d{2}-\d{2})\b",
        ],
        text,
    )
    parsed_date = None
    if date_value:
        normalized_date = (
            date_value.replace("年", "-").replace("月", "-").replace("日", "")
            .replace("/", "-").replace(".", "-")
        )
        try:
            year, month, day = [int(part) for part in normalized_date.split("-")]
            parsed_date = date(year, month, day)
        except (TypeError, ValueError):
            parsed_date = None

    amount_value = _first_match(
        [
            r"(?:价税合计|支付金额|实付金额|订单金额|行程金额|合计金额|总计)\s*[:：]?\s*[¥￥]?\s*([\d,]+(?:\.\d{1,2})?)",
            r"[¥￥]\s*([\d,]+\.\d{2})",
        ],
        text,
    )
    invoice_number = _first_match(
        [r"发票号码\s*[:：]?\s*([A-Za-z0-9]{8,30})"],
        text,
    )
    transaction_number = _first_match(
        [r"(?:交易单号|交易号|订单号|订单编号)\s*[:：]?\s*([A-Za-z0-9_-]{6,64})"],
        text,
    )
    seller = _first_match(
        [
            r"(?:销售方名称|销方名称|收款方|商户名称|服务商)\s*[:：]?\s*([^\n\r]{2,80})",
        ],
        text,
    )
    currency = "CNY" if re.search(r"[¥￥]|人民币|\bCNY\b|\bRMB\b", text, re.IGNORECASE) else None
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    return ExtractionModel(
        document_date=parsed_date,
        counterparty=seller,
        description=lines[0][:500] if lines else None,
        amount=_decimal_or_none(amount_value),
        currency=currency,
        invoice_number=invoice_number,
        transaction_number=transaction_number,
        provider=seller,
        seller=seller,
        payee=seller,
        merchant=seller,
        total_with_tax=_decimal_or_none(amount_value),
        payment_amount=_decimal_or_none(amount_value),
        trip_amount=_decimal_or_none(amount_value),
        order_number=transaction_number,
        aggregate_amount=_decimal_or_none(amount_value),
        transactions=[],
        confidence=max(0.0, min(1.0, ocr_confidence / 100)),
    )


def _map_public_fields(
    document_type: DocumentType,
    extraction: ExtractionModel,
    source_text: str,
) -> dict[str, Any]:
    if document_type in INVOICE_TYPES:
        amount = extraction.total_with_tax or extraction.aggregate_amount or extraction.amount
        counterparty = extraction.seller or extraction.counterparty
    elif document_type == DocumentType.PAYMENT_SCREENSHOT:
        amount = extraction.payment_amount or extraction.aggregate_amount or extraction.amount
        counterparty = extraction.payee or extraction.counterparty
    elif document_type in ITINERARY_TYPES:
        amount = extraction.trip_amount or extraction.aggregate_amount or extraction.amount
        counterparty = extraction.provider or extraction.counterparty
    elif document_type == DocumentType.ORDER_SCREENSHOT:
        amount = extraction.aggregate_amount or extraction.amount
        counterparty = extraction.merchant or extraction.counterparty
    else:
        amount = extraction.aggregate_amount or extraction.amount
        counterparty = extraction.merchant or extraction.counterparty

    explicit_currency = extraction.currency.upper() if extraction.currency else None
    currency_evidence = {
        "CNY": r"[¥￥]|人民币|\bCNY\b|\bRMB\b",
        "USD": r"\bUSD\b|US\$|美元",
        "EUR": r"\bEUR\b|€|欧元",
        "HKD": r"\bHKD\b|港币|港元|HK\$",
        "JPY": r"\bJPY\b|日元|円",
    }
    if explicit_currency:
        evidence_pattern = currency_evidence.get(
            explicit_currency,
            rf"\b{re.escape(explicit_currency)}\b",
        )
        if not re.search(evidence_pattern, source_text, re.IGNORECASE):
            explicit_currency = None

    return {
        "document_date": extraction.document_date,
        "counterparty": counterparty,
        "description": extraction.description,
        "amount": abs(amount) if amount is not None else None,
        "currency": explicit_currency,
        "invoice_number": extraction.invoice_number,
        "transaction_number": (
            extraction.order_number
            if document_type == DocumentType.ORDER_SCREENSHOT
            else extraction.transaction_number
        ),
    }


def missing_critical_fields(
    document_type: DocumentType,
    values: dict[str, Any],
) -> list[str]:
    missing: list[str] = []

    def require(name: str) -> None:
        if values.get(name) in (None, ""):
            missing.append(name)

    if document_type in INVOICE_TYPES:
        require("invoice_number")
        require("document_date")
        require("amount")
    elif document_type == DocumentType.PAYMENT_SCREENSHOT:
        require("document_date")
        require("amount")
        if not values.get("counterparty") and not values.get("transaction_number"):
            missing.append("counterparty_or_transaction_number")
    elif document_type == DocumentType.ORDER_SCREENSHOT:
        require("document_date")
        require("amount")
        if not values.get("counterparty") and not values.get("transaction_number"):
            missing.append("merchant_or_order_number")
    elif document_type in ITINERARY_TYPES:
        require("document_date")
        require("amount")
        require("counterparty")
    elif document_type in {
        DocumentType.OVERSEAS_INVOICE_OR_RECEIPT,
        DocumentType.RECEIPT,
    }:
        require("document_date")
        require("amount")
        require("counterparty")
    elif document_type == DocumentType.OTHER_DOCUMENT:
        require("document_date")
        require("amount")
        if not values.get("counterparty") and not values.get("description"):
            missing.append("counterparty_or_description")
    else:
        missing.append("document_type")
    return missing


def _model_metadata() -> tuple[str | None, str | None]:
    settings = get_settings()
    provider_name = settings.get_active_llm_provider()
    if not provider_name:
        return None, None
    return provider_name, getattr(settings, f"{provider_name}_model", None)


def _call_model(
    prompt: str,
    image_data: bytes | None,
    mime_type: str,
) -> str:
    service = get_llm_service()
    provider = service.active_provider
    if not provider:
        raise RuntimeError("没有可用的模型提供商")
    if image_data and service.supports_vision():
        return provider.vision_completion(
            DOCUMENT_JSON_SYSTEM_PROMPT,
            prompt,
            image_data,
            mime_type,
        )
    return provider.chat_completion(DOCUMENT_JSON_SYSTEM_PROMPT, prompt)


def _mime_type(file_type: str) -> str:
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
    }.get(file_type, "application/octet-stream")


def process_document_content(
    *,
    file_data: bytes,
    file_type: str,
    source_folder: SourceFolder,
) -> DocumentProcessingResult:
    """Recognize one processable document without performing database I/O."""
    del source_folder  # Reserved for future classifier context; never field authority.
    settings = get_settings()
    ocr = get_ocr_service()
    if file_type == "pdf":
        raw_text, ocr_confidence, _ = ocr.process_document_pdf(file_data)
    else:
        raw_text, ocr_confidence, _ = ocr.process_image(file_data)

    result = DocumentProcessingResult(raw_text=raw_text)
    model_available = get_llm_service().is_available
    provider_name, model_name = _model_metadata()
    result.model_provider = provider_name
    result.model_name = model_name

    rule_classification = classify_document_by_text(raw_text)
    if rule_classification:
        document_type, classification_confidence = rule_classification
    elif model_available:
        prompt = DOCUMENT_CLASSIFICATION_PROMPT.format(
            text=(raw_text[:30_000] or "[OCR 无有效文本，请依据图像判断]")
        )
        image = file_data if file_type in {"jpg", "jpeg", "png"} and len(raw_text) < 20 else None
        try:
            raw_classification = _call_model(prompt, image, _mime_type(file_type))
            result.classification_raw_response = raw_classification
            parsed_classification = ClassificationModel.model_validate(
                _coerce_classification_payload(_strict_json(raw_classification))
            )
            document_type = parsed_classification.document_type
            classification_confidence = parsed_classification.confidence
        except (ValueError, ValidationError, json.JSONDecodeError):
            result.status_reason = "INVALID_MODEL_RESPONSE"
            result.status_detail = "文档分类模型返回的数据不符合严格 JSON Schema"
            return result
        except Exception as exc:
            result.status_reason = "LLM_UNAVAILABLE"
            result.status_detail = _safe_exception_detail("文档分类模型当前不可用", exc)
            return result
    else:
        result.status_reason = "LLM_UNAVAILABLE"
        result.status_detail = "规则无法确定文档类型，且未配置可用模型"
        return result

    result.document_type = document_type
    result.classification_confidence = Decimal(str(classification_confidence))
    if (
        document_type == DocumentType.UNKNOWN
        or classification_confidence < settings.batch_classification_threshold
    ):
        result.status_reason = (
            "UNKNOWN_DOCUMENT_TYPE"
            if document_type == DocumentType.UNKNOWN
            else "LOW_CLASSIFICATION_CONFIDENCE"
        )
        result.status_detail = "文档类型无法可靠确定"
        return result

    used_model_extraction = False
    if model_available:
        prompt = DOCUMENT_EXTRACTION_PROMPT.format(
            document_type=document_type.value,
            text=(raw_text[:30_000] or "[OCR 无有效文本，请依据图像提取]"),
        )
        image = file_data if file_type in {"jpg", "jpeg", "png"} and len(raw_text) < 20 else None
        try:
            raw_extraction = _call_model(prompt, image, _mime_type(file_type))
            result.extraction_raw_response = raw_extraction
            extraction = ExtractionModel.model_validate(
                _coerce_extraction_payload(_strict_json(raw_extraction))
            )
            used_model_extraction = True
        except (ValueError, ValidationError, json.JSONDecodeError):
            # Prefer OCR/rule extraction over hard-failing the whole document.
            extraction = extract_fields_by_rules(raw_text, ocr_confidence)
            result.status_detail = "字段提取模型返回格式无效，已回退规则抽取"
        except Exception as exc:
            extraction = extract_fields_by_rules(raw_text, ocr_confidence)
            result.status_detail = _safe_exception_detail(
                "字段提取模型当前不可用，已回退规则抽取",
                exc,
            )
    else:
        extraction = extract_fields_by_rules(raw_text, ocr_confidence)

    mapped = _map_public_fields(document_type, extraction, raw_text)
    result.extracted_fields_json = extraction.model_dump(mode="json")
    for key, value in mapped.items():
        setattr(result, key, value)

    missing = missing_critical_fields(document_type, mapped)
    completeness = max(0.0, 1.0 - len(missing) / max(1, len(missing) + 3))
    extraction_confidence = extraction.confidence
    if not used_model_extraction:
        extraction_confidence *= completeness
    result.extraction_confidence = Decimal(str(round(extraction_confidence, 4)))
    result.missing_critical_fields = missing

    has_multiple_without_total = (
        len(extraction.transactions) > 1
        and extraction.aggregate_amount is None
        and extraction.amount is None
        and extraction.payment_amount is None
        and extraction.trip_amount is None
        and extraction.total_with_tax is None
    )
    if has_multiple_without_total:
        result.amount = None
        result.processing_status = ProcessingStatus.NEEDS_MANUAL_REVIEW
        result.status_reason = "MULTIPLE_TRANSACTIONS"
        result.status_detail = "资料包含多笔独立交易，未发现可信汇总金额"
    elif missing:
        result.processing_status = ProcessingStatus.NEEDS_MANUAL_REVIEW
        result.status_reason = (
            "LLM_UNAVAILABLE" if not model_available else "MISSING_CRITICAL_FIELDS"
        )
        result.status_detail = "缺少关键字段：" + "、".join(missing)
    elif extraction_confidence < settings.batch_extraction_threshold:
        result.processing_status = ProcessingStatus.NEEDS_MANUAL_REVIEW
        result.status_reason = "LOW_EXTRACTION_CONFIDENCE"
        result.status_detail = "字段提取置信度低于阈值"
    else:
        result.processing_status = ProcessingStatus.RECOGNIZED
        result.status_reason = None
        result.status_detail = None
    return result
