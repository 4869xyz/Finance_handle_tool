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
from app.services.ocr_service import get_field_extractor, get_ocr_service


logger = logging.getLogger(__name__)
DOCUMENT_EXTRACTION_MAX_OUTPUT_TOKENS = 12000
OCR_TEXT_SUFFICIENT_CHARS = 40


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
    item_name: str | None = None
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
        "item_name",
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
    item_name: str | None = None
    order_number: str | None = None
    trip_origin: str | None = None
    trip_destination: str | None = None
    trip_time_text: str | None = None
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
STRUCTURED_DOCUMENT_TYPES = INVOICE_TYPES | ITINERARY_TYPES
LLM_FIRST_DOCUMENT_TYPES = {
    DocumentType.PAYMENT_SCREENSHOT,
    DocumentType.ORDER_SCREENSHOT,
    DocumentType.RECEIPT,
    DocumentType.OVERSEAS_INVOICE_OR_RECEIPT,
    DocumentType.OTHER_DOCUMENT,
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
    "item_name",
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
_EXTRACTION_STRATEGY = "TYPE_ROUTED_RULE_THEN_LLM"


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
    order_number = _first_match(
        [
            r"(?:商家订单号|商户订单号|订单编号|订单号)\s*[:：]?\s*([A-Za-z0-9_-]{6,64})",
        ],
        text,
    )
    transaction_number = _first_match(
        [
            r"(?:交易单号|支付单号|转账单号|交易号)\s*[:：]?\s*([A-Za-z0-9_-]{6,64})",
        ],
        text,
    )
    seller = _first_match(
        [
            r"(?:销售方名称|销方名称|收款方|商户名称|服务商)\s*[:：]?\s*([^\n\r]{2,80})",
        ],
        text,
    )
    dirty_item = _first_match(
        [
            r"(\*[^*\n]+\*[^\n\r]{0,80})",
            r"((?:运输服务|电信服务|餐饮服务)\*[^\n\r]{2,40})",
        ],
        text,
    )
    item_name = _clean_invoice_item_name(dirty_item)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    parsed_amount = _decimal_or_none(amount_value)

    return ExtractionModel(
        document_date=parsed_date,
        counterparty=seller,
        description=lines[0][:500] if lines else None,
        amount=parsed_amount,
        item_name=item_name,
        invoice_number=invoice_number,
        transaction_number=transaction_number,
        provider=seller,
        seller=seller,
        payee=seller,
        merchant=seller,
        total_with_tax=parsed_amount,
        payment_amount=parsed_amount,
        trip_amount=parsed_amount,
        order_number=order_number,
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


def _parse_rule_date(value: str | None) -> date | None:
    if not value:
        return None
    normalized = (
        str(value)
        .replace("年", "-")
        .replace("月", "-")
        .replace("日", "")
        .replace("/", "-")
        .replace(".", "-")
        .strip()
    )
    try:
        year, month, day = [int(part) for part in normalized.split("-")[:3]]
        return date(year, month, day)
    except (TypeError, ValueError):
        return None


def _rule_confidence(ocr_confidence: float) -> float:
    return max(0.0, min(1.0, ocr_confidence / 100))


def _clean_invoice_item_name(value: str | None) -> str | None:
    """Keep project/service name; strip qty/rate/tax debris after the name."""
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", str(value)).strip()
    if not cleaned:
        return None
    # Cut at first long decimal / percent / standalone qty pattern after the name.
    cleaned = re.split(
        r"\s+(?=\d+\.\d{3,}|\d+(?:\.\d+)?%|\d+\s+\d+\.\d+)",
        cleaned,
        maxsplit=1,
    )[0].strip()
    cleaned = re.sub(r"[\d.,%]+\s*$", "", cleaned).strip(" ;,|")
    # Prefer "*category*name" or "category*name" core.
    starred = re.search(r"(\*?[^*\s]+(?:\*[^*\s]+)+)", cleaned)
    if starred:
        cleaned = starred.group(1).lstrip("*")
        if not cleaned.startswith("*") and "*" in cleaned:
            # normalize to keep visible tax item style without leading junk
            cleaned = cleaned if cleaned.count("*") >= 1 else cleaned
    return cleaned[:200] or None


TRIP_HEADER_TOKENS = frozenset(
    {
        "车型",
        "上车时间",
        "城市",
        "起点",
        "终点",
        "金额",
        "下车时间",
        "里程",
        "时长",
    }
)
GAODE_TABLE_COLUMNS = (
    ("sequence", "序号"),
    ("provider", "服务商"),
    ("vehicle", "车型"),
    ("start_time", "上车时间"),
    ("city", "城市"),
    ("origin", "起点"),
    ("destination", "终点"),
    ("amount", "金额"),
)
TRIP_LOCATION_NOISE = frozenset(
    {
        "高德地图—打车—",
        "高德地图—打车——",
        "高德地图-打车-",
        "高德地图",
        "打车",
        "行程单",
        "电子行程单",
        "AMAP",
        "ITINERARY",
    }
)


def _is_trip_header_text(value: str | None) -> bool:
    if not value:
        return False
    normalized = re.sub(r"\s+", "", value)
    if not normalized:
        return False
    # Exact header row or header-word soup
    tokens = re.split(r"[\s/|]+", value.strip())
    meaningful = [token for token in tokens if token]
    if not meaningful:
        return False
    header_hits = sum(1 for token in meaningful if token in TRIP_HEADER_TOKENS)
    if header_hits >= 2:
        return True
    return normalized in {"".join(TRIP_HEADER_TOKENS), "车型上车时间城市起点终点金额"}


def _sanitize_trip_party(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", value).strip()
    if not cleaned or _is_trip_header_text(cleaned):
        return None
    return cleaned[:80] or None


def _is_invalid_trip_location(value: str | None) -> bool:
    if not value:
        return True
    cleaned = re.sub(r"\s+", "", value).strip()
    if not cleaned or _is_trip_header_text(cleaned):
        return True
    if cleaned in TRIP_LOCATION_NOISE:
        return True
    return bool(
        ("高德地图" in cleaned or "AMAP" in cleaned)
        and ("打车" in cleaned or "行程" in cleaned or "ITINERARY" in cleaned)
    )


def _sanitize_trip_location(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", "", value).strip(" /|")
    if _is_invalid_trip_location(cleaned):
        return None
    return cleaned[:200] or None


def _layout_cell_text(
    items: list[dict[str, Any]],
    *,
    preserve_space: bool = False,
) -> str | None:
    ordered = sorted(
        items,
        key=lambda item: (
            float(item.get("min_y", 0)),
            float(item.get("min_x", 0)),
        ),
    )
    parts = [str(item.get("text") or "").strip() for item in ordered]
    parts = [part for part in parts if part]
    if not parts:
        return None
    return (" " if preserve_space else "").join(parts)


def _parse_gaode_trip_range(text: str) -> tuple[str | None, str | None]:
    match = re.search(
        r"行程时间\s*[:：]\s*"
        r"(?P<start>\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2})\s*"
        r"至\s*"
        r"(?P<end>\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2})",
        text,
    )
    if not match:
        return None, None
    return match.group("start"), match.group("end")


def _parse_gaode_layout_rows(
    text: str,
    layout_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Restore Gaode table cells from native PDF word coordinates."""
    if not layout_items:
        return []

    rows: list[dict[str, Any]] = []
    pages = sorted({int(item.get("page", 1)) for item in layout_items})
    for page_number in pages:
        page_items = [
            item
            for item in layout_items
            if int(item.get("page", 1)) == page_number
            and item.get("text")
            and item.get("center_x") is not None
            and item.get("center_y") is not None
        ]
        sequence_headers = [
            item for item in page_items if str(item["text"]).strip() == "序号"
        ]
        for sequence_header in sequence_headers:
            header_y = float(sequence_header["center_y"])
            headers: dict[str, dict[str, Any]] = {}
            for field_name, label in GAODE_TABLE_COLUMNS:
                candidates = [
                    item
                    for item in page_items
                    if str(item["text"]).strip() == label
                    and abs(float(item["center_y"]) - header_y) <= 5
                ]
                if candidates:
                    headers[field_name] = min(
                        candidates,
                        key=lambda item: abs(float(item["center_y"]) - header_y),
                    )
            if len(headers) != len(GAODE_TABLE_COLUMNS):
                continue

            fields = [field_name for field_name, _ in GAODE_TABLE_COLUMNS]
            centers = [float(headers[field]["center_x"]) for field in fields]
            boundaries = [
                (centers[index] + centers[index + 1]) / 2
                for index in range(len(centers) - 1)
            ]
            header_bottom = max(float(item["max_y"]) for item in headers.values())
            row_anchors = sorted(
                [
                    item
                    for item in page_items
                    if re.fullmatch(r"[1-9]\d*", str(item["text"]).strip())
                    and float(item["center_y"]) > header_bottom
                    and float(item["center_x"]) < boundaries[0]
                ],
                key=lambda item: float(item["center_y"]),
            )
            if not row_anchors:
                continue

            footer_positions = [
                float(item["min_y"])
                for item in page_items
                if float(item["min_y"]) > header_bottom
                and str(item["text"]).strip().startswith(("说明", "页码"))
            ]
            footer_y = min(footer_positions) if footer_positions else None

            for index, anchor in enumerate(row_anchors):
                anchor_y = float(anchor["center_y"])
                upper_y = (
                    header_bottom
                    if index == 0
                    else (
                        float(row_anchors[index - 1]["center_y"]) + anchor_y
                    )
                    / 2
                )
                lower_y = (
                    (
                        anchor_y + float(row_anchors[index + 1]["center_y"])
                    )
                    / 2
                    if index + 1 < len(row_anchors)
                    else footer_y or anchor_y + 20
                )
                row_items = [
                    item
                    for item in page_items
                    if upper_y < float(item["center_y"]) < lower_y
                ]
                cells: dict[str, list[dict[str, Any]]] = {
                    field: [] for field in fields
                }
                for item in row_items:
                    center_x = float(item["center_x"])
                    column_index = sum(center_x > boundary for boundary in boundaries)
                    cells[fields[column_index]].append(item)

                start_text = _layout_cell_text(
                    cells["start_time"],
                    preserve_space=True,
                )
                start_match = re.search(
                    r"(\d{4}-\d{2}-\d{2})\s+(\d{1,2}:\d{2})",
                    start_text or "",
                )
                origin = _sanitize_trip_location(
                    _layout_cell_text(cells["origin"])
                )
                destination = _sanitize_trip_location(
                    _layout_cell_text(cells["destination"])
                )
                if not start_match or not origin or not destination:
                    continue
                start_time = f"{start_match.group(1)} {start_match.group(2)}"
                rows.append(
                    {
                        "row_number": int(str(anchor["text"]).strip()),
                        "document_date": _parse_rule_date(start_match.group(1)),
                        "origin": origin,
                        "destination": destination,
                        "time": start_time,
                        "end_time": None,
                        "amount": _decimal_or_none(
                            _first_match(
                                [r"(\d+(?:\.\d{1,2})?)"],
                                _layout_cell_text(cells["amount"]) or "",
                            )
                        ),
                        "provider": _sanitize_trip_party(
                            _layout_cell_text(cells["provider"])
                        ),
                        "vehicle": _sanitize_trip_party(
                            _layout_cell_text(cells["vehicle"])
                        ),
                        "city": _sanitize_trip_party(
                            _layout_cell_text(cells["city"])
                        ),
                    }
                )
            break

    rows.sort(key=lambda row: (row["row_number"], row["time"]))
    range_start, range_end = _parse_gaode_trip_range(text)
    if len(rows) == 1 and range_start == rows[0]["time"]:
        rows[0]["end_time"] = range_end
    return rows


def _parse_gaode_trip_rows(
    text: str,
    layout_items: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Parse Gaode/ride-hail itinerary table body rows when present."""
    layout_rows = _parse_gaode_layout_rows(text, layout_items or [])
    if layout_rows:
        return layout_rows

    rows: list[dict[str, Any]] = []
    # Common OCR / text-layer layouts without coordinates:
    # 1) 1 曹操出行 经济型 2025-04-15 08:30 深圳 起点A 终点B 21.90元
    # 2) 2025-04-15 08:30 经济型 深圳 起点A 终点B 21.90
    # 3) 起点A 终点B ... 08:30 ... 21.90
    line_patterns = [
        re.compile(
            r"^(?:(?P<seq>[1-9]\d*)\s+)?"
            r"(?P<provider>[^\s]{2,20})\s+"
            r"(?P<vehicle>[^\s]{1,12})\s+"
            r"(?P<date>\d{4}-\d{2}-\d{2})\s+"
            r"(?P<time>\d{1,2}:\d{2})\s+"
            r"(?P<city>[^\s]{2,12})\s+"
            r"(?P<origin>.+?)\s+"
            r"(?P<destination>.+?)\s+"
            r"(?P<amount>\d+\.\d{2})\s*元?\s*$"
        ),
        re.compile(
            r"(?P<date>\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?)?\s*"
            r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?)\s+"
            r"(?P<vehicle>[^\s]{1,12})?\s*"
            r"(?P<city>[^\s]{1,12})?\s*"
            r"(?P<origin>.+?)\s+"
            r"(?P<destination>.+?)\s+"
            r"(?P<amount>\d+\.\d{2}|\d+)"
            r"\s*元?\s*$"
        ),
        re.compile(
            r"(?P<origin>[^\s]{2,30})\s+(?P<destination>[^\s]{2,30})\s+"
            r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?)\s+"
            r"(?P<amount>\d+\.\d{2})"
        ),
        re.compile(
            r"(?:起点|上车)[:：]?\s*(?P<origin>[^\n]{2,40}?)\s+"
            r"(?:终点|下车)[:：]?\s*(?P<destination>[^\n]{2,40}?)"
            r"(?:.*?上车时间[:：]?\s*(?P<time>\d{1,2}:\d{2}(?::\d{2})?))?"
            r"(?:.*?金额[:：]?\s*[¥￥]?(?P<amount>\d+\.\d{2}|\d+))?"
        ),
    ]
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line or _is_trip_header_text(line):
            continue
        if "高德" in line and "行程单" in line:
            continue
        for pattern in line_patterns:
            match = pattern.search(line)
            if not match:
                continue
            groups = match.groupdict()
            origin = _sanitize_trip_location(groups.get("origin"))
            destination = _sanitize_trip_location(groups.get("destination"))
            if not origin or not destination:
                continue
            amount = _decimal_or_none(groups.get("amount"))
            date_text = groups.get("date")
            time_text = (groups.get("time") or "").strip() or None
            parsed_date = _parse_rule_date(date_text)
            # Full Gaode table lines include provider; keep "YYYY-MM-DD HH:MM".
            if (
                groups.get("provider")
                and parsed_date is not None
                and time_text
                and " " not in time_text
            ):
                trip_time = f"{parsed_date.isoformat()} {time_text}"
            else:
                trip_time = time_text
            rows.append(
                {
                    "document_date": parsed_date,
                    "origin": origin,
                    "destination": destination,
                    "time": trip_time,
                    "amount": amount,
                    "provider": _sanitize_trip_party(groups.get("provider")),
                    "vehicle": _sanitize_trip_party(groups.get("vehicle")),
                    "city": _sanitize_trip_party(groups.get("city")),
                }
            )
            break
    range_start, range_end = _parse_gaode_trip_range(text)
    if len(rows) == 1 and rows[0].get("time") and range_start == rows[0]["time"]:
        rows[0]["end_time"] = range_end
    return rows


def _extract_trip_route(text: str) -> tuple[str | None, str | None]:
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line or "高德地图" in line or "行程单" in line:
            continue
        match = re.fullmatch(
            r"(?P<origin>[^→—]{2,80}?)\s*[→—]\s*"
            r"(?P<destination>[^→—]{2,80})",
            line,
        )
        if not match:
            continue
        origin = _sanitize_trip_location(match.group("origin"))
        destination = _sanitize_trip_location(match.group("destination"))
        if origin and destination:
            return origin, destination
    return None, None


def _extraction_from_invoice_fields(
    fields: dict[str, Any],
    text: str,
    ocr_confidence: float,
) -> ExtractionModel:
    seller = fields.get("seller_name")
    total = _decimal_or_none(
        str(fields["total_with_tax"]) if fields.get("total_with_tax") is not None else None
    )
    amount = _decimal_or_none(
        str(fields["amount"]) if fields.get("amount") is not None else None
    )
    primary_amount = total or amount
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    item_name = _clean_invoice_item_name(fields.get("item_name"))
    if item_name is None:
        item_name = _clean_invoice_item_name(
            _first_match(
                [
                    r"(\*[^*\n]+\*[^\n\r]{0,80})",
                    r"((?:运输服务|电信服务|餐饮服务)\*[^\n\r]{2,80})",
                ],
                text,
            )
        )
    description = item_name or (lines[0][:500] if lines else None)
    return ExtractionModel(
        document_date=_parse_rule_date(fields.get("issue_date")),
        counterparty=seller,
        description=description,
        amount=primary_amount,
        item_name=item_name,
        invoice_number=fields.get("invoice_number"),
        transaction_number=None,
        provider=None,
        seller=seller,
        payee=None,
        merchant=None,
        total_with_tax=total,
        payment_amount=None,
        trip_amount=None,
        order_number=None,
        aggregate_amount=None,
        amount_derivation="EXPLICIT" if primary_amount is not None else "NOT_FOUND",
        amount_evidence=(
            "FieldExtractor匹配发票金额字段"
            if primary_amount is not None
            else None
        ),
        amount_components=[],
        transactions=[],
        confidence=_rule_confidence(ocr_confidence),
    )


def _extract_itinerary_by_rules(
    text: str,
    ocr_confidence: float,
    layout_items: list[dict[str, Any]] | None = None,
) -> ExtractionModel:
    parsed_date = _parse_rule_date(
        _first_match(
            [
                r"(?:行程日期|乘车日期|乘机日期|出发日期|出行日期|日期)\s*[:：]?\s*(\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?)",
                r"\b(\d{4}-\d{2}-\d{2})\b",
            ],
            text,
        )
    )
    amount_value = _first_match(
        [
            r"(?:票价|车费|行程金额|合计|总计|实付|行程费用合计)\s*[:：]?\s*[¥￥]?\s*([\d,]+(?:\.\d{1,2})?)",
            r"[¥￥]\s*([\d,]+\.\d{2})",
        ],
        text,
    )
    provider = _sanitize_trip_party(
        _first_match(
            [
                r"(?:承运方|承运人|服务商|出租汽车公司)\s*[:：]?\s*([^\n\r]{2,80})",
            ],
            text,
        )
    )
    if provider is None:
        for brand in (
            "曹操出行",
            "喜行约车",
            "博度出行",
            "携华出行",
            "添猫出行",
            "立道出行",
            "风韵出行",
            "AA出行",
            "滴滴出行",
            "美团打车",
            "中国铁路",
            "铁路客运",
            "高德打车",
            "高德地图",
        ):
            if brand in text:
                provider = brand
                break
    if provider is None:
        airline = _first_match([r"([^\n]{0,20}航空)"], text)
        provider = _sanitize_trip_party(airline)
    if provider is None and ("高德" in text and "行程" in text):
        provider = "高德打车"
    route_origin, route_destination = _extract_trip_route(text)
    trip_origin = _sanitize_trip_location(
        _first_match(
            [
                r"(?:出发站|起点|上车点|上车)\s*[:：]\s*([^\n\r]{2,40})",
            ],
            text,
        )
    ) or route_origin
    trip_destination = _sanitize_trip_location(
        _first_match(
            [
                r"(?:到达站|终点|下车点|下车)\s*[:：]\s*([^\n\r]{2,40})",
            ],
            text,
        )
    ) or route_destination
    trip_time = _first_match(
        [
            r"(?:发车时间|起飞时间|出发时间|上车时间)\s*[:：]\s*([^\n\r]{4,40})",
            r"\b(\d{1,2}:\d{2}(?:\s*[-~—]\s*\d{1,2}:\d{2})?)\b",
        ],
        text,
    )
    ticket_number = _first_match(
        [
            r"(?:取票号|电子客票号|票号|车次|航班号)\s*[:：]?\s*([A-Za-z0-9-]{4,32})",
        ],
        text,
    )
    parsed_amount = _decimal_or_none(amount_value)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    description = next(
        (
            line[:500]
            for line in lines
            if not _is_trip_header_text(line)
        ),
        None,
    )
    gaode_rows = _parse_gaode_trip_rows(text, layout_items)
    if gaode_rows:
        if trip_origin is None:
            trip_origin = gaode_rows[0].get("origin")
        if trip_destination is None:
            trip_destination = gaode_rows[0].get("destination")
        if trip_time is None:
            trip_time = gaode_rows[0].get("time")
        if parsed_date is None:
            parsed_date = gaode_rows[0].get("document_date")
        if parsed_amount is None:
            amounts = [row["amount"] for row in gaode_rows if row.get("amount") is not None]
            if amounts:
                parsed_amount = sum(amounts, Decimal("0.00")).quantize(Decimal("0.01"))

    extraction = ExtractionModel(
        document_date=parsed_date,
        counterparty=provider,
        description=description,
        amount=parsed_amount,
        item_name=None,
        invoice_number=None,
        transaction_number=ticket_number,
        provider=provider,
        seller=None,
        payee=None,
        merchant=None,
        total_with_tax=None,
        payment_amount=None,
        trip_amount=parsed_amount,
        order_number=None,
        aggregate_amount=None,
        amount_derivation="EXPLICIT" if parsed_amount is not None else "NOT_FOUND",
        amount_evidence=(
            "OCR/Python规则匹配行程金额标签"
            if parsed_amount is not None
            else None
        ),
        amount_components=[],
        transactions=[],
        confidence=_rule_confidence(ocr_confidence),
    )
    if gaode_rows:
        records: list[RecordExtraction] = []
        expected_count_match = re.search(r"共计\s*(\d+)\s*单行程", text)
        expected_count = (
            int(expected_count_match.group(1))
            if expected_count_match
            else len(gaode_rows)
        )
        record_count_mismatch = expected_count != len(gaode_rows)
        for index, row in enumerate(gaode_rows, start=1):
            row_amount = row.get("amount")
            evidence = (
                "行程表行金额"
                if row_amount is not None
                else None
            )
            records.append(
                RecordExtraction(
                    record_id=f"R{index}",
                    record_type="TRIP",
                    document_date=row.get("document_date") or parsed_date,
                    counterparty=provider,
                    description=description,
                    transaction_number=ticket_number,
                    primary_amount=EvidenceAmount(
                        value=row_amount,
                        evidence=evidence,
                        kind="TRIP_TOTAL",
                    ),
                    trip=TripDetail(
                        origin=row.get("origin"),
                        destination=row.get("destination"),
                        start_time=row.get("time"),
                        end_time=row.get("end_time"),
                        service_provider=provider,
                        vehicle_type=row.get("vehicle"),
                        city=row.get("city"),
                        stated_total=EvidenceAmount(
                            value=row_amount,
                            evidence=evidence,
                            kind="TRIP_TOTAL",
                        ),
                    ),
                    manual_review=record_count_mismatch,
                    review_reasons=(
                        ["GAODE_RECORD_COUNT_MISMATCH"]
                        if record_count_mismatch
                        else []
                    ),
                    confidence=extraction.confidence,
                )
            )
        extraction.records = records
    elif trip_origin or trip_destination or trip_time:
        extraction.records = [
            RecordExtraction(
                record_id="R1",
                record_type="TRIP",
                document_date=parsed_date,
                counterparty=provider,
                description=description,
                transaction_number=ticket_number,
                primary_amount=EvidenceAmount(
                    value=parsed_amount,
                    evidence=extraction.amount_evidence,
                    kind="TRIP_TOTAL",
                ),
                trip=TripDetail(
                    origin=trip_origin,
                    destination=trip_destination,
                    start_time=trip_time,
                    service_provider=provider,
                    stated_total=EvidenceAmount(
                        value=parsed_amount,
                        evidence=extraction.amount_evidence,
                        kind="TRIP_TOTAL",
                    ),
                ),
                confidence=extraction.confidence,
            )
        ]
    return extraction


def extract_fields_by_type_rules(
    document_type: DocumentType,
    text: str,
    ocr_confidence: float,
    layout_items: list[dict[str, Any]] | None = None,
) -> ExtractionModel:
    """Type-aware local extraction for structured documents."""
    if document_type in INVOICE_TYPES:
        fields = get_field_extractor().extract_fields(text, None)
        return _extraction_from_invoice_fields(fields, text, ocr_confidence)
    if document_type in ITINERARY_TYPES:
        return _extract_itinerary_by_rules(text, ocr_confidence, layout_items)
    return extract_fields_by_rules(text, ocr_confidence)


def _ocr_text_sufficient(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text or "")
    return len(normalized) >= OCR_TEXT_SUFFICIENT_CHARS


def _is_image_file_type(file_type: str) -> bool:
    return file_type in {"jpg", "jpeg", "png"}


def _missing_for_extraction(
    document_type: DocumentType,
    extraction: ExtractionModel,
    mapped: dict[str, Any],
) -> list[str]:
    if extraction.records:
        return _record_missing_critical_fields(extraction.records)
    return missing_critical_fields(document_type, mapped)


def _rule_extraction_acceptable(
    document_type: DocumentType,
    extraction: ExtractionModel,
    mapped: dict[str, Any],
    threshold: float,
) -> bool:
    if _missing_for_extraction(document_type, extraction, mapped):
        return False
    if extraction.confidence < threshold:
        return False
    if document_type in ITINERARY_TYPES:
        trip_records = [
            record
            for record in extraction.records
            if record.record_type == "TRIP"
        ]
        if not trip_records:
            return False
        for record in trip_records:
            if record.manual_review or "GAODE_RECORD_COUNT_MISMATCH" in record.review_reasons:
                return False
            trip = record.trip
            if trip is None:
                return False
            if (
                _is_invalid_trip_location(trip.origin)
                or _is_invalid_trip_location(trip.destination)
                or not (trip.start_time or trip.request_time)
            ):
                return False
        if mapped.get("counterparty") and _is_trip_header_text(str(mapped["counterparty"])):
            return False
    return True


DISPLAY_FIELD_JOIN = "; "
DISPLAY_TEXT_MAX_LEN = 1000
ORDER_NUMBER_MAX_LEN = 150


def _join_unique_texts(
    values: list[str | None],
    *,
    max_length: int = DISPLAY_TEXT_MAX_LEN,
) -> str | None:
    seen: set[str] = set()
    parts: list[str] = []
    for value in values:
        if value is None:
            continue
        cleaned = re.sub(r"\s+", " ", str(value)).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        parts.append(cleaned)
    if not parts:
        return None
    joined = DISPLAY_FIELD_JOIN.join(parts)
    return joined[:max_length] or None


def _format_trip_time(trip: TripDetail) -> str | None:
    start = (trip.start_time or "").strip()
    end = (trip.end_time or "").strip()
    if start and end:
        return f"{start}至{end}"
    if start:
        return start
    if end:
        return end
    request_time = (trip.request_time or "").strip()
    return request_time or None


def _join_ordered_trip_values(values: list[str | None]) -> str | None:
    if not values:
        return None
    cleaned_values: list[str] = []
    for value in values:
        if value is None:
            return None
        cleaned = re.sub(r"\s+", " ", str(value)).strip()
        if not cleaned:
            return None
        cleaned_values.append(cleaned)
    joined = "/".join(cleaned_values)
    return joined[:DISPLAY_TEXT_MAX_LEN] or None


def _promote_display_fields(extraction: ExtractionModel) -> dict[str, Any]:
    item_names: list[str | None] = [extraction.item_name]
    order_numbers: list[str | None] = [extraction.order_number]
    origins: list[str | None] = []
    destinations: list[str | None] = []
    times: list[str | None] = []
    for record in extraction.records:
        order_numbers.append(record.order_number)
        for item in record.items:
            item_names.append(item.name_visible)
        if record.trip is not None:
            origins.append(record.trip.origin)
            destinations.append(record.trip.destination)
            times.append(_format_trip_time(record.trip))
    return {
        "item_name": _join_unique_texts(item_names),
        "order_number": _join_unique_texts(
            order_numbers,
            max_length=ORDER_NUMBER_MAX_LEN,
        ),
        "trip_origin": _join_ordered_trip_values(origins),
        "trip_destination": _join_ordered_trip_values(destinations),
        "trip_time_text": _join_ordered_trip_values(times),
    }


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

    mapped = {
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
    mapped.update(_promote_display_fields(extraction))
    return mapped


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
                    if document_type
                    in {
                        DocumentType.ORDER_SCREENSHOT,
                        DocumentType.PAYMENT_SCREENSHOT,
                    }
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
                if document_type
                in {
                    DocumentType.ORDER_SCREENSHOT,
                    DocumentType.PAYMENT_SCREENSHOT,
                }
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
    document_type: DocumentType,
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
            (
                _unique_record_value(records, "order_number")
                or _unique_record_value(records, "transaction_number")
            )
            if document_type == DocumentType.ORDER_SCREENSHOT
            else _unique_record_value(records, "transaction_number")
        ),
    }
    mapped.update(_promote_display_fields(extraction))
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
    escalation_path: list[str] | None = None,
) -> dict[str, Any]:
    validation = {
        "strategy": _EXTRACTION_STRATEGY,
        "prompt_version": DOCUMENT_EXTRACTION_PROMPT_VERSION,
        "final_source": final_source,
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason,
        "escalation_path": "→".join(escalation_path or []),
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
        raw_text, ocr_confidence, layout_items = ocr.process_document_pdf(file_data)
    else:
        raw_text, ocr_confidence, layout_items = ocr.process_image(file_data)

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
    fallback_reason: str | None = "MODEL_NOT_CONFIGURED"
    calculation_review = False
    calculation_valid = True
    record_validation: dict[str, Any] = {}
    record_review_reasons: list[str] = []
    mapped: dict[str, Any] | None = None
    extraction: ExtractionModel | None = None
    escalation_path: list[str] = []
    image = file_data if _is_image_file_type(file_type) else None
    can_vision = bool(image and model_available and get_llm_service().supports_vision())

    def _adopt_extraction(
        candidate: ExtractionModel,
        *,
        source: str,
        fallback: bool,
        reason: str | None,
    ) -> None:
        nonlocal extraction, mapped, calculation_review, calculation_valid
        nonlocal record_validation, record_review_reasons
        nonlocal used_model_extraction, fallback_reason
        had_records = bool(candidate.records)
        legacy_mapped = _map_public_fields(document_type, candidate)
        calc_review = (
            candidate.amount_derivation == "CALCULATED" and not had_records
        )
        calc_valid = _calculated_amount_matches_components(candidate, legacy_mapped)
        if calc_review and not calc_valid and not candidate.records:
            candidate.amount = None
            candidate.total_with_tax = None
            candidate.payment_amount = None
            candidate.trip_amount = None
            candidate.aggregate_amount = None
        _ensure_extraction_records(document_type, candidate)
        summarized, rec_validation, rec_reviews = _summarize_extraction_records(
            document_type,
            candidate,
        )
        extraction = candidate
        mapped = summarized
        calculation_review = calc_review
        calculation_valid = calc_valid
        record_validation = rec_validation
        record_review_reasons = rec_reviews
        used_model_extraction = source in {"LLM_TEXT", "LLM_VISION"}
        fallback_reason = reason
        if not fallback:
            fallback_reason = None

    def _try_llm_extraction(*, use_vision: bool) -> tuple[bool, str | None]:
        nonlocal result
        prompt = build_document_extraction_prompt(
            document_type.value,
            raw_text[:30_000] or "[OCR 无有效文本，请依据图像提取]",
        )
        logger.info(
            "Using document extraction prompt version=%s provider=%s model=%s "
            "document_type=%s mode=%s",
            DOCUMENT_EXTRACTION_PROMPT_VERSION,
            provider_name,
            model_name,
            document_type.value,
            "VISION" if use_vision else "TEXT",
        )
        try:
            raw_extraction = _call_model(
                prompt,
                image if use_vision else None,
                _mime_type(file_type),
            )
            result.extraction_raw_response = raw_extraction
            model_extraction = ExtractionModel.model_validate(
                _coerce_extraction_payload(_strict_json(raw_extraction))
            )
            model_mapped = _map_public_fields(document_type, model_extraction)
            if _all_key_fields_empty(
                document_type,
                model_mapped,
            ) and _all_record_key_fields_empty(model_extraction.records):
                return False, "NO_USABLE_LLM_FIELDS"
            _adopt_extraction(
                model_extraction,
                source="LLM_VISION" if use_vision else "LLM_TEXT",
                fallback=False,
                reason=None,
            )
            return True, None
        except (ValueError, ValidationError, json.JSONDecodeError):
            return False, "INVALID_MODEL_RESPONSE"
        except Exception as exc:
            result.status_detail = _safe_exception_detail(
                "字段提取模型当前不可用，已回退规则抽取",
                exc,
            )
            return False, "LLM_UNAVAILABLE"

    def _needs_critical_upgrade() -> bool:
        assert extraction is not None and mapped is not None
        return bool(_missing_for_extraction(document_type, extraction, mapped))

    final_source = "OCR_RULE_FALLBACK"
    if document_type in STRUCTURED_DOCUMENT_TYPES:
        escalation_path.append("RULE")
        rule_extraction = extract_fields_by_type_rules(
            document_type,
            raw_text,
            ocr_confidence,
            layout_items,
        )
        _ensure_extraction_records(document_type, rule_extraction)
        rule_mapped, rule_record_validation, rule_record_reviews = (
            _summarize_extraction_records(document_type, rule_extraction)
        )
        if _rule_extraction_acceptable(
            document_type,
            rule_extraction,
            rule_mapped,
            settings.batch_extraction_threshold,
        ):
            extraction = rule_extraction
            mapped = rule_mapped
            record_validation = rule_record_validation
            record_review_reasons = rule_record_reviews
            final_source = "RULE_PRIMARY"
            fallback_reason = None
            used_model_extraction = False
        else:
            extraction = rule_extraction
            mapped = rule_mapped
            record_validation = rule_record_validation
            record_review_reasons = rule_record_reviews
            llm_ok = False
            llm_reason: str | None = "MODEL_NOT_CONFIGURED"
            if model_available:
                escalation_path.append("TEXT")
                llm_ok, llm_reason = _try_llm_extraction(use_vision=False)
                if llm_ok:
                    final_source = "LLM_TEXT"
                if (not llm_ok or _needs_critical_upgrade()) and can_vision:
                    escalation_path.append("VISION")
                    vision_ok, vision_reason = _try_llm_extraction(use_vision=True)
                    if vision_ok:
                        final_source = "LLM_VISION"
                        llm_ok = True
                        llm_reason = None
                    else:
                        llm_reason = vision_reason or llm_reason
            if not llm_ok:
                escalation_path.append("RULE_FALLBACK")
                extraction = extract_fields_by_rules(raw_text, ocr_confidence)
                _ensure_extraction_records(document_type, extraction)
                mapped, record_validation, record_review_reasons = (
                    _summarize_extraction_records(document_type, extraction)
                )
                final_source = "OCR_RULE_FALLBACK"
                fallback_reason = llm_reason or "MODEL_NOT_CONFIGURED"
                result.status_detail = result.status_detail or (
                    "字段提取模型未返回可用结果，已回退规则抽取"
                    if fallback_reason == "NO_USABLE_LLM_FIELDS"
                    else (
                        "字段提取模型返回格式无效，已回退规则抽取"
                        if fallback_reason == "INVALID_MODEL_RESPONSE"
                        else None
                    )
                )
                used_model_extraction = False
    else:
        llm_ok = False
        llm_reason: str | None = "MODEL_NOT_CONFIGURED"
        if model_available:
            prefer_text = _ocr_text_sufficient(raw_text) or not can_vision
            if prefer_text:
                escalation_path.append("TEXT")
                llm_ok, llm_reason = _try_llm_extraction(use_vision=False)
                if llm_ok:
                    final_source = "LLM_TEXT"
            if can_vision and (
                not prefer_text
                or not llm_ok
                or (llm_ok and _needs_critical_upgrade())
            ):
                if "VISION" not in escalation_path:
                    escalation_path.append("VISION")
                vision_ok, vision_reason = _try_llm_extraction(use_vision=True)
                if vision_ok:
                    final_source = "LLM_VISION"
                    llm_ok = True
                    llm_reason = None
                else:
                    llm_reason = vision_reason or llm_reason
        if not llm_ok:
            escalation_path.append("RULE_FALLBACK")
            extraction = extract_fields_by_rules(raw_text, ocr_confidence)
            _ensure_extraction_records(document_type, extraction)
            mapped, record_validation, record_review_reasons = (
                _summarize_extraction_records(document_type, extraction)
            )
            final_source = "OCR_RULE_FALLBACK"
            fallback_reason = llm_reason or "MODEL_NOT_CONFIGURED"
            used_model_extraction = False
            if fallback_reason == "NO_USABLE_LLM_FIELDS":
                result.status_detail = (
                    "字段提取模型未返回任何有效关键字段，已回退规则抽取"
                )
            elif fallback_reason == "INVALID_MODEL_RESPONSE":
                result.status_detail = "字段提取模型返回格式无效，已回退规则抽取"

    assert extraction is not None
    assert mapped is not None
    validation = _extraction_validation(
        extraction,
        final_source=final_source,
        fallback_used=final_source == "OCR_RULE_FALLBACK",
        fallback_reason=fallback_reason if final_source == "OCR_RULE_FALLBACK" else None,
        calculation_valid=calculation_valid if used_model_extraction else True,
        escalation_path=escalation_path,
    )

    description_source = "DOCUMENT_CONTENT" if mapped.get("description") else "NOT_FOUND"
    if not mapped.get("description") and filename_summary:
        mapped["description"] = filename_summary
        description_source = "FILENAME_FALLBACK"
    validation["record_validation"] = record_validation
    validation["description_source"] = description_source
    extracted_fields_json = extraction.model_dump(mode="json")
    extracted_fields_json["description"] = mapped.get("description")
    for display_key in (
        "item_name",
        "order_number",
        "trip_origin",
        "trip_destination",
        "trip_time_text",
    ):
        extracted_fields_json[display_key] = mapped.get(display_key)
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
