import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.config import get_settings
from app.models.batch import DocumentType, ProcessingStatus, SourceFolder
from app.services.document_prompts import (
    DOCUMENT_CLASSIFICATION_PROMPT,
    DOCUMENT_EXTRACTION_PROMPT_VERSION,
    DOCUMENT_JSON_SYSTEM_PROMPT,
    build_document_extraction_prompt,
)
from app.services.llm_service import get_llm_service
from app.services.ocr_service import get_ocr_service


logger = logging.getLogger(__name__)
DOCUMENT_EXTRACTION_MAX_OUTPUT_TOKENS = 12000


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


class AmountComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None
    amount: Decimal
    operation: Literal["ADD", "SUBTRACT"]

    @field_validator("amount")
    @classmethod
    def normalize_amount(cls, value: Decimal) -> Decimal:
        return abs(value)

    @field_validator("label")
    @classmethod
    def blank_label_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class EvidenceAmount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: Decimal | None = None
    evidence: str | None = None
    kind: Literal[
        "TOTAL_WITH_TAX",
        "PAID",
        "ORDER_TOTAL",
        "TRIP_TOTAL",
        "TICKET_PRICE",
        "TRANSFER",
        "RECEIPT_TOTAL",
        "ITEM_SUM",
        "ITEM",
        "ORIGINAL",
        "DISCOUNT",
        "TRIP_DETAIL",
        "UNKNOWN",
    ] = "UNKNOWN"

    @field_validator("value", mode="before")
    @classmethod
    def require_two_decimal_string(cls, value: Any) -> Any:
        if value is None or value == "":
            return None
        if isinstance(value, str):
            cleaned = value.strip().replace(",", "")
            if not re.fullmatch(r"\d+\.\d{2}", cleaned):
                raise ValueError("交易级金额必须是两位小数字符串")
            return cleaned
        if isinstance(value, Decimal):
            return value
        raise ValueError("交易级金额必须使用字符串输出")

    @field_validator("value")
    @classmethod
    def normalize_value(cls, value: Decimal | None) -> Decimal | None:
        return abs(value).quantize(Decimal("0.01")) if value is not None else None

    @field_validator("evidence")
    @classmethod
    def blank_evidence_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @model_validator(mode="after")
    def require_evidence_for_amount(self):
        if self.value is not None and not self.evidence:
            raise ValueError("每个交易级金额必须保留原始证据")
        return self


class ItemDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name_visible: str | None = None
    name_complete: bool = False
    specification: str | None = None
    quantity: str | None = None
    item_amount: EvidenceAmount = Field(default_factory=EvidenceAmount)
    original_amount: EvidenceAmount = Field(default_factory=EvidenceAmount)
    merchant: str | None = None


class TripDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_time: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    service_provider: str | None = None
    vehicle_type: str | None = None
    city: str | None = None
    origin: str | None = None
    destination: str | None = None
    stated_total: EvidenceAmount = Field(default_factory=EvidenceAmount)
    detail_amounts: list[EvidenceAmount] = Field(default_factory=list)


class TransferDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: Literal["TRANSFER_OUT", "TRANSFER_IN", "DIRECTION_UNKNOWN"]
    card_side: Literal["LEFT", "RIGHT", "UNKNOWN"] = "UNKNOWN"
    status_text: str | None = None


class RecordExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: str = Field(min_length=1, max_length=40)
    record_type: Literal[
        "INVOICE",
        "ORDER",
        "TRIP",
        "PAYMENT",
        "TRANSFER",
        "RECEIPT",
        "OTHER",
    ]
    document_date: date | None = None
    counterparty: str | None = None
    description: str | None = None
    invoice_number: str | None = None
    order_number: str | None = None
    transaction_number: str | None = None
    primary_amount: EvidenceAmount = Field(default_factory=EvidenceAmount)
    item_subtotal: EvidenceAmount = Field(default_factory=EvidenceAmount)
    original_amount: EvidenceAmount = Field(default_factory=EvidenceAmount)
    discount_amount: EvidenceAmount = Field(default_factory=EvidenceAmount)
    items: list[ItemDetail] = Field(default_factory=list)
    trip: TripDetail | None = None
    transfer: TransferDetail | None = None
    manual_review: bool = False
    review_reasons: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)

    @field_validator(
        "record_id",
        "counterparty",
        "description",
        "invoice_number",
        "order_number",
        "transaction_number",
    )
    @classmethod
    def strip_record_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class ExtractionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_date: date | None
    counterparty: str | None
    description: str | None
    amount: Decimal | None
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
    amount_derivation: Literal["EXPLICIT", "CALCULATED", "NOT_FOUND"]
    amount_evidence: str | None
    amount_components: list[AmountComponent]
    transactions: list[TransactionCandidate]
    records: list[RecordExtraction] = Field(default_factory=list)
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
        "invoice_number",
        "transaction_number",
        "provider",
        "seller",
        "payee",
        "merchant",
        "order_number",
        "amount_evidence",
    )
    @classmethod
    def blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @model_validator(mode="after")
    def validate_amount_derivation(self):
        amount_values = (
            self.amount,
            self.total_with_tax,
            self.payment_amount,
            self.trip_amount,
            self.aggregate_amount,
        )
        if self.amount_derivation == "NOT_FOUND" and any(
            value is not None for value in amount_values
        ):
            raise ValueError("NOT_FOUND 不能包含汇总金额")
        if self.amount_derivation == "CALCULATED" and (
            not self.amount_evidence or not self.amount_components
        ):
            raise ValueError("CALCULATED 必须包含计算依据和组成金额")
        if self.amount_derivation != "CALCULATED" and self.amount_components:
            raise ValueError("只有 CALCULATED 可以包含组成金额")
        record_ids = [record.record_id for record in self.records]
        if len(record_ids) != len(set(record_ids)):
            raise ValueError("records 中的 record_id 必须唯一")
        return self


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
    "amount_derivation",
    "amount_evidence",
    "amount_components",
    "transactions",
    "records",
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
_AMOUNT_COMPONENT_FIELDS = {"label", "amount", "operation"}
_EXTRACTION_STRATEGY = "LLM_PRIMARY_WHOLE_DOCUMENT_FALLBACK"


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


def _coerce_amount_component(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    item = {key: _empty_to_none(raw.get(key)) for key in _AMOUNT_COMPONENT_FIELDS}
    item["amount"] = _coerce_amount_value(raw.get("amount"))
    operation = str(item.get("operation") or "").strip().upper()
    if item["amount"] is None or operation not in {"ADD", "SUBTRACT"}:
        return None
    item["operation"] = operation
    return item


def _coerce_extraction_payload(raw: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in _EXTRACTION_FIELDS:
        if key in {"transactions", "amount_components", "records"}:
            continue
        if key in _AMOUNT_FIELDS:
            payload[key] = _coerce_amount_value(raw.get(key))
        else:
            payload[key] = _empty_to_none(raw.get(key))

    components = raw.get("amount_components")
    if isinstance(components, list):
        payload["amount_components"] = [
            item
            for item in (_coerce_amount_component(row) for row in components)
            if item
        ]
    else:
        payload["amount_components"] = []

    derivation = str(payload.get("amount_derivation") or "").strip().upper()
    if derivation not in {"EXPLICIT", "CALCULATED", "NOT_FOUND"}:
        if payload["amount_components"]:
            derivation = "CALCULATED"
        elif any(payload.get(field) is not None for field in _AMOUNT_FIELDS):
            derivation = "EXPLICIT"
        else:
            derivation = "NOT_FOUND"
    payload["amount_derivation"] = derivation

    transactions = raw.get("transactions")
    if transactions is None:
        payload["transactions"] = []
    elif isinstance(transactions, list):
        payload["transactions"] = [
            item for item in (_coerce_transaction(row) for row in transactions) if item
        ]
    else:
        payload["transactions"] = []
    records = raw.get("records")
    payload["records"] = records if isinstance(records, list) else []
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
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    parsed_amount = _decimal_or_none(amount_value)

    return ExtractionModel(
        document_date=parsed_date,
        counterparty=seller,
        description=lines[0][:500] if lines else None,
        amount=parsed_amount,
        invoice_number=invoice_number,
        transaction_number=transaction_number,
        provider=seller,
        seller=seller,
        payee=seller,
        merchant=seller,
        total_with_tax=parsed_amount,
        payment_amount=parsed_amount,
        trip_amount=parsed_amount,
        order_number=transaction_number,
        aggregate_amount=parsed_amount,
        amount_derivation="EXPLICIT" if parsed_amount is not None else "NOT_FOUND",
        amount_evidence=(
            "OCR/Python规则匹配明确金额标签"
            if parsed_amount is not None
            else None
        ),
        amount_components=[],
        transactions=[],
        confidence=max(0.0, min(1.0, ocr_confidence / 100)),
    )


def _map_public_fields(
    document_type: DocumentType,
    extraction: ExtractionModel,
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

    return {
        "document_date": extraction.document_date,
        "counterparty": counterparty,
        "description": extraction.description,
        "amount": abs(amount) if amount is not None else None,
        "invoice_number": extraction.invoice_number,
        "transaction_number": (
            extraction.order_number
            if document_type == DocumentType.ORDER_SCREENSHOT
            else extraction.transaction_number
        ),
    }


def _record_type_for_document(document_type: DocumentType) -> str:
    if document_type in INVOICE_TYPES:
        return "INVOICE"
    if document_type == DocumentType.ORDER_SCREENSHOT:
        return "ORDER"
    if document_type in ITINERARY_TYPES:
        return "TRIP"
    if document_type == DocumentType.PAYMENT_SCREENSHOT:
        return "PAYMENT"
    if document_type in {
        DocumentType.RECEIPT,
        DocumentType.OVERSEAS_INVOICE_OR_RECEIPT,
    }:
        return "RECEIPT"
    return "OTHER"


def _ensure_extraction_records(
    document_type: DocumentType,
    extraction: ExtractionModel,
) -> None:
    if extraction.records:
        return
    if len(extraction.transactions) > 1:
        extraction.records = [
            RecordExtraction(
                record_id=f"R{index}",
                record_type=_record_type_for_document(document_type),
                document_date=transaction.document_date,
                counterparty=transaction.counterparty,
                description=extraction.description,
                invoice_number=(
                    extraction.invoice_number
                    if document_type in INVOICE_TYPES
                    else None
                ),
                order_number=(
                    transaction.transaction_number
                    if document_type == DocumentType.ORDER_SCREENSHOT
                    else None
                ),
                transaction_number=transaction.transaction_number,
                primary_amount=EvidenceAmount(),
                review_reasons=["LEGACY_TRANSACTION_WITHOUT_AMOUNT_EVIDENCE"],
                confidence=extraction.confidence,
            )
            for index, transaction in enumerate(extraction.transactions, start=1)
        ]
        return
    mapped = _map_public_fields(document_type, extraction)
    amount = mapped.get("amount")
    evidence = extraction.amount_evidence if amount is not None else None
    if amount is not None and not evidence:
        amount = None
    extraction.records = [
        RecordExtraction(
            record_id="R1",
            record_type=_record_type_for_document(document_type),
            document_date=mapped.get("document_date"),
            counterparty=mapped.get("counterparty"),
            description=mapped.get("description"),
            invoice_number=mapped.get("invoice_number"),
            order_number=(
                extraction.order_number
                if document_type == DocumentType.ORDER_SCREENSHOT
                else None
            ),
            transaction_number=extraction.transaction_number,
            primary_amount=EvidenceAmount(
                value=amount,
                evidence=evidence,
                kind=(
                    "TOTAL_WITH_TAX"
                    if document_type in INVOICE_TYPES
                    else "PAID"
                ),
            ),
            confidence=extraction.confidence,
        )
    ]


def _unique_record_value(records: list[RecordExtraction], field_name: str) -> Any:
    values = {
        getattr(record, field_name)
        for record in records
        if getattr(record, field_name) not in (None, "")
    }
    return next(iter(values)) if len(values) == 1 else None


def _record_missing_critical_fields(
    records: list[RecordExtraction],
) -> list[str]:
    missing: list[str] = []
    for record in records:
        prefix = record.record_id
        if record.primary_amount.value is None:
            missing.append(f"{prefix}.amount")
        if record.record_type in {"INVOICE", "ORDER", "TRIP", "PAYMENT", "RECEIPT"}:
            if record.document_date is None:
                missing.append(f"{prefix}.document_date")
        if record.record_type == "INVOICE" and not record.invoice_number:
            missing.append(f"{prefix}.invoice_number")
        if record.record_type in {"ORDER", "PAYMENT"} and not (
            record.counterparty or record.order_number or record.transaction_number
        ):
            missing.append(f"{prefix}.counterparty_or_number")
        if record.record_type in {"TRIP", "RECEIPT"} and not record.counterparty:
            missing.append(f"{prefix}.counterparty")
    return missing


def _summarize_extraction_records(
    extraction: ExtractionModel,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    records = extraction.records
    record_validations: list[dict[str, Any]] = []
    review_reasons: list[str] = []

    for record in records:
        reasons = list(dict.fromkeys(record.review_reasons))
        validation: dict[str, Any] = {
            "record_id": record.record_id,
            "record_type": record.record_type,
            "review_reasons": reasons,
        }
        item_values = [item.item_amount.value for item in record.items]
        if record.items and all(value is not None for value in item_values):
            item_sum = sum(item_values, Decimal("0.00")).quantize(Decimal("0.01"))
            validation["item_amount_sum"] = str(item_sum)
            if record.primary_amount.value is not None:
                difference = (record.primary_amount.value - item_sum).quantize(
                    Decimal("0.01")
                )
                validation["unexplained_difference"] = str(difference)
                if difference != Decimal("0.00"):
                    reasons.append("ORDER_ITEM_TOTAL_DIFFERENCE")

        if record.trip and record.trip.detail_amounts:
            detail_values = [item.value for item in record.trip.detail_amounts]
            if all(value is not None for value in detail_values):
                detail_sum = sum(detail_values, Decimal("0.00")).quantize(
                    Decimal("0.01")
                )
                validation["trip_detail_sum"] = str(detail_sum)
                stated = record.trip.stated_total.value or record.primary_amount.value
                if stated is not None and stated != detail_sum:
                    reasons.append("TRIP_TOTAL_DETAIL_MISMATCH")

        if record.manual_review:
            reasons.append("MODEL_REQUIRES_REVIEW")
        reasons = list(dict.fromkeys(reasons))
        validation["review_reasons"] = reasons
        record_validations.append(validation)
        review_reasons.extend(f"{record.record_id}:{reason}" for reason in reasons)

    transfer_records = [record for record in records if record.record_type == "TRANSFER"]
    non_transfer_records = [
        record for record in records if record.record_type != "TRANSFER"
    ]
    gross_outflow = Decimal("0.00")
    gross_inflow = Decimal("0.00")
    net_cashflow: Decimal | None = None
    file_amount: Decimal | None = None
    total_source = "NOT_FOUND"

    if non_transfer_records:
        amounts = [record.primary_amount.value for record in non_transfer_records]
        if all(value is not None for value in amounts):
            file_amount = sum(amounts, Decimal("0.00")).quantize(Decimal("0.01"))
            total_source = (
                "EXPLICIT_RECORD" if len(non_transfer_records) == 1 else "VERIFIED_RECORD_SUM"
            )
    elif transfer_records:
        directions_known = True
        amounts_known = True
        for record in transfer_records:
            value = record.primary_amount.value
            if value is None:
                amounts_known = False
                continue
            direction = record.transfer.direction if record.transfer else "DIRECTION_UNKNOWN"
            if direction == "TRANSFER_OUT":
                gross_outflow += value
            elif direction == "TRANSFER_IN":
                gross_inflow += value
            else:
                directions_known = False
                review_reasons.append(f"{record.record_id}:TRANSFER_DIRECTION_UNKNOWN")
        if directions_known and amounts_known:
            net_cashflow = (gross_outflow - gross_inflow).quantize(Decimal("0.01"))
            if net_cashflow >= Decimal("0.00"):
                file_amount = net_cashflow
                total_source = "VERIFIED_NET_CASHFLOW"
            else:
                review_reasons.append("FILE:NEGATIVE_NET_CASHFLOW")

    if len(records) > 1 and file_amount is None:
        review_reasons.append("FILE:INCOMPLETE_RECORD_TOTAL")

    mapped = {
        "document_date": _unique_record_value(records, "document_date"),
        "counterparty": _unique_record_value(records, "counterparty"),
        "description": (
            records[0].description
            if len(records) == 1
            else f"{len(records)}笔独立记录"
        ),
        "amount": file_amount,
        "invoice_number": _unique_record_value(records, "invoice_number"),
        "transaction_number": (
            _unique_record_value(records, "order_number")
            or _unique_record_value(records, "transaction_number")
        ),
    }
    file_validation = {
        "record_count": len(records),
        "total_source": total_source,
        "file_amount": str(file_amount) if file_amount is not None else None,
        "gross_outflow": str(gross_outflow.quantize(Decimal("0.01"))),
        "gross_inflow": str(gross_inflow.quantize(Decimal("0.01"))),
        "net_cashflow": str(net_cashflow) if net_cashflow is not None else None,
        "records": record_validations,
    }
    return mapped, file_validation, list(dict.fromkeys(review_reasons))


def _calculated_amount_matches_components(
    extraction: ExtractionModel,
    mapped: dict[str, Any],
) -> bool:
    if extraction.amount_derivation != "CALCULATED":
        return True
    amount = mapped.get("amount")
    if amount is None or not extraction.amount_components:
        return False
    calculated = Decimal("0")
    for component in extraction.amount_components:
        if component.operation == "ADD":
            calculated += component.amount
        else:
            calculated -= component.amount
    return calculated.quantize(Decimal("0.01")) == Decimal(str(amount)).quantize(
        Decimal("0.01")
    )


def _all_key_fields_empty(
    document_type: DocumentType,
    mapped: dict[str, Any],
) -> bool:
    number_field = (
        "invoice_number"
        if document_type in INVOICE_TYPES
        else "transaction_number"
    )
    return all(
        mapped.get(field_name) in (None, "")
        for field_name in ("document_date", "counterparty", "amount", number_field)
    )


def _all_record_key_fields_empty(records: list[RecordExtraction]) -> bool:
    return all(
        not any(
            (
                record.document_date,
                record.counterparty,
                record.primary_amount.value,
                record.invoice_number,
                record.order_number,
                record.transaction_number,
            )
        )
        for record in records
    )
def _extraction_validation(
    extraction: ExtractionModel,
    *,
    final_source: str,
    fallback_used: bool,
    fallback_reason: str | None,
    calculation_valid: bool,
) -> dict[str, Any]:
    validation = {
        "strategy": _EXTRACTION_STRATEGY,
        "prompt_version": DOCUMENT_EXTRACTION_PROMPT_VERSION,
        "final_source": final_source,
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason,
        "amount_derivation": extraction.amount_derivation,
        "amount_evidence": extraction.amount_evidence,
        "amount_components": [
            component.model_dump(mode="json")
            for component in extraction.amount_components
        ],
        "calculation_valid": calculation_valid,
    }
    if fallback_used:
        validation["rule_confidence"] = extraction.confidence
    return validation


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
            max_output_tokens=DOCUMENT_EXTRACTION_MAX_OUTPUT_TOKENS,
        )
    return provider.chat_completion(
        DOCUMENT_JSON_SYSTEM_PROMPT,
        prompt,
        max_output_tokens=DOCUMENT_EXTRACTION_MAX_OUTPUT_TOKENS,
        json_mode=True,
    )


def _mime_type(file_type: str) -> str:
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
    }.get(file_type, "application/octet-stream")


def filename_description_fallback(file_name: str | None) -> str | None:
    """Use the visible file stem only as a last-resort description."""
    if not file_name:
        return None
    basename = file_name.replace("\\", "/").rsplit("/", 1)[-1]
    stem = basename.rsplit(".", 1)[0] if "." in basename else basename
    summary = re.sub(r"\s+", " ", stem).strip(" ._-")
    return summary[:500] or None


def process_document_content(
    *,
    file_data: bytes,
    file_type: str,
    source_folder: SourceFolder,
    file_name: str | None = None,
) -> DocumentProcessingResult:
    """Recognize one processable document without performing database I/O."""
    del source_folder  # Reserved for future classifier context; never field authority.
    settings = get_settings()
    ocr = get_ocr_service()
    if file_type == "pdf":
        raw_text, ocr_confidence, _ = ocr.process_document_pdf(file_data)
    else:
        raw_text, ocr_confidence, _ = ocr.process_image(file_data)

    filename_summary = filename_description_fallback(file_name)
    result = DocumentProcessingResult(
        raw_text=raw_text,
        description=filename_summary,
    )
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
    fallback_reason = "MODEL_NOT_CONFIGURED"
    calculation_review = False
    calculation_valid = True
    record_validation: dict[str, Any] = {}
    record_review_reasons: list[str] = []
    mapped: dict[str, Any] | None = None
    if model_available:
        prompt = build_document_extraction_prompt(
            document_type.value,
            raw_text[:30_000] or "[OCR 无有效文本，请依据图像提取]",
        )
        logger.info(
            "Using document extraction prompt version=%s provider=%s model=%s document_type=%s",
            DOCUMENT_EXTRACTION_PROMPT_VERSION,
            provider_name,
            model_name,
            document_type.value,
        )
        image = file_data if file_type in {"jpg", "jpeg", "png"} else None
        try:
            raw_extraction = _call_model(prompt, image, _mime_type(file_type))
            result.extraction_raw_response = raw_extraction
            model_extraction = ExtractionModel.model_validate(
                _coerce_extraction_payload(_strict_json(raw_extraction))
            )
            had_model_records = bool(model_extraction.records)
            legacy_model_mapped = _map_public_fields(document_type, model_extraction)
            model_calculation_review = (
                model_extraction.amount_derivation == "CALCULATED"
                and not had_model_records
            )
            model_calculation_valid = _calculated_amount_matches_components(
                model_extraction,
                legacy_model_mapped,
            )
            if (
                model_calculation_review
                and not model_calculation_valid
                and not model_extraction.records
            ):
                model_extraction.amount = None
                model_extraction.total_with_tax = None
                model_extraction.payment_amount = None
                model_extraction.trip_amount = None
                model_extraction.aggregate_amount = None
            _ensure_extraction_records(document_type, model_extraction)
            model_mapped, model_record_validation, model_record_reviews = (
                _summarize_extraction_records(model_extraction)
            )
            if _all_key_fields_empty(
                document_type,
                model_mapped,
            ) and _all_record_key_fields_empty(model_extraction.records):
                fallback_reason = "NO_USABLE_LLM_FIELDS"
                result.status_detail = (
                    "字段提取模型未返回任何有效关键字段，已回退规则抽取"
                )
            else:
                extraction = model_extraction
                mapped = model_mapped
                calculation_review = model_calculation_review
                calculation_valid = model_calculation_valid
                record_validation = model_record_validation
                record_review_reasons = model_record_reviews
                used_model_extraction = True
        except (ValueError, ValidationError, json.JSONDecodeError):
            fallback_reason = "INVALID_MODEL_RESPONSE"
            result.status_detail = "字段提取模型返回格式无效，已回退规则抽取"
        except Exception as exc:
            fallback_reason = "LLM_UNAVAILABLE"
            result.status_detail = _safe_exception_detail(
                "字段提取模型当前不可用，已回退规则抽取",
                exc,
            )
    if not used_model_extraction:
        extraction = extract_fields_by_rules(raw_text, ocr_confidence)
        _ensure_extraction_records(document_type, extraction)
        mapped, record_validation, record_review_reasons = (
            _summarize_extraction_records(extraction)
        )
        validation = _extraction_validation(
            extraction,
            final_source="OCR_RULE_FALLBACK",
            fallback_used=True,
            fallback_reason=fallback_reason,
            calculation_valid=True,
        )
    else:
        validation = _extraction_validation(
            extraction,
            final_source="LLM",
            fallback_used=False,
            fallback_reason=None,
            calculation_valid=calculation_valid,
        )

    assert mapped is not None
    description_source = "DOCUMENT_CONTENT" if mapped.get("description") else "NOT_FOUND"
    if not mapped.get("description") and filename_summary:
        mapped["description"] = filename_summary
        description_source = "FILENAME_FALLBACK"
    validation["record_validation"] = record_validation
    validation["description_source"] = description_source
    extracted_fields_json = extraction.model_dump(mode="json")
    extracted_fields_json["description"] = mapped.get("description")
    extracted_fields_json["_validation"] = validation
    result.extracted_fields_json = extracted_fields_json
    for key, value in mapped.items():
        setattr(result, key, value)

    missing = (
        _record_missing_critical_fields(extraction.records)
        if extraction.records
        else missing_critical_fields(document_type, mapped)
    )
    completeness = max(0.0, 1.0 - len(missing) / max(1, len(missing) + 3))
    extraction_confidence = extraction.confidence
    if not used_model_extraction:
        extraction_confidence *= completeness
    result.extraction_confidence = Decimal(str(round(extraction_confidence, 4)))
    result.missing_critical_fields = missing

    has_multiple_without_total = len(extraction.records) > 1 and mapped.get(
        "amount"
    ) is None
    if has_multiple_without_total:
        result.amount = None
        result.processing_status = ProcessingStatus.NEEDS_MANUAL_REVIEW
        result.status_reason = "MULTIPLE_TRANSACTIONS"
        result.status_detail = "资料包含多笔独立交易，未发现可信汇总金额"
    elif calculation_review:
        result.processing_status = ProcessingStatus.NEEDS_MANUAL_REVIEW
        result.status_reason = "CALCULATED_AMOUNT_REVIEW"
        detail = (
            "模型根据清晰组成项计算金额，需人工复核："
            f"{extraction.amount_evidence or '未提供计算表达式'}"
        )
        if not calculation_valid:
            detail = "模型计算组成与输出金额不一致，未采用该计算金额"
        result.status_detail = detail
    elif record_review_reasons:
        result.processing_status = ProcessingStatus.NEEDS_MANUAL_REVIEW
        result.status_reason = "TRANSACTION_RECORD_REVIEW"
        result.status_detail = "交易级验算需要复核：" + "、".join(
            record_review_reasons
        )
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
