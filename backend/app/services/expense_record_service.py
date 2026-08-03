"""Record-level views over monthly source documents.

The database ledger remains file-oriented.  This module exposes one lightweight
view per extracted transaction so filtering, classification and exports can work
at transaction granularity without a schema migration.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.models.batch import DocumentType, ProcessingStatus, SourceDocument, SourceFolder


RECONCILIATION_VERSION = "1.0"


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


@dataclass(slots=True)
class ExpenseRecordView:
    id: int
    record_ref: str
    record_id: str
    source_document_id: int
    batch_id: int
    source_folder: SourceFolder
    relative_path: str
    file_name: str
    file_type: str
    document_type: DocumentType
    processing_status: ProcessingStatus
    status_reason: str | None
    status_detail: str | None
    raw_text: str | None
    classification_confidence: Decimal | None
    extraction_confidence: Decimal | None
    document_date: date | None
    counterparty: str | None
    description: str | None
    amount: Decimal | None
    currency: str | None
    invoice_number: str | None
    transaction_number: str | None
    order_number: str | None
    record_type: str
    amount_evidence: str | None
    transfer_direction: str | None
    reconciliation_relation: str
    reconciliation_role: str
    reconciliation_group_id: str | None
    reconciliation_primary_ref: str | None
    reconciliation_confidence: float | None
    reconciliation_evidence: str | None
    eligible_amount: Decimal | None
    processing_attempts: int
    created_at: datetime
    updated_at: datetime


def _legacy_record(document: SourceDocument) -> dict[str, Any]:
    return {
        "record_id": "R1",
        "record_type": "INVOICE" if document.invoice_number else "OTHER",
        "document_date": document.document_date,
        "counterparty": document.counterparty,
        "description": document.description,
        "invoice_number": document.invoice_number,
        "order_number": None,
        "transaction_number": document.transaction_number,
        "primary_amount": {
            "value": str(document.amount) if document.amount is not None else None,
            "evidence": None,
        },
    }


def build_expense_records(
    documents: list[SourceDocument],
) -> list[ExpenseRecordView]:
    """Return stable transaction views, including historical file-only records."""
    views: list[ExpenseRecordView] = []
    for document in documents:
        payload = (
            document.extracted_fields_json
            if isinstance(document.extracted_fields_json, dict)
            else {}
        )
        raw_records = payload.get("records")
        records = (
            [record for record in raw_records if isinstance(record, dict)]
            if isinstance(raw_records, list)
            else []
        )
        if not records:
            records = [_legacy_record(document)]

        reconciliation = payload.get("_reconciliation")
        reconciliation_records = (
            reconciliation.get("records", {})
            if isinstance(reconciliation, dict)
            and isinstance(reconciliation.get("records"), dict)
            else {}
        )

        seen_record_ids: set[str] = set()
        for index, record in enumerate(records, start=1):
            record_id = str(record.get("record_id") or f"R{index}")
            if record_id in seen_record_ids:
                record_id = f"R{index}"
                while record_id in seen_record_ids:
                    record_id += "_"
            seen_record_ids.add(record_id)
            record_ref = f"{document.id}:{record_id}"
            state = reconciliation_records.get(record_id)
            if not isinstance(state, dict):
                state = {}
            amount_payload = record.get("primary_amount")
            if not isinstance(amount_payload, dict):
                amount_payload = {}
            transfer = record.get("transfer")
            if not isinstance(transfer, dict):
                transfer = {}
            order_number = record.get("order_number") or None
            transaction_number = record.get("transaction_number") or order_number
            status_detail = document.status_detail
            role = str(state.get("role") or "UNRESOLVED")
            relation = str(state.get("relation") or "unknown")
            if role == "AUXILIARY":
                status_detail = "跨文件核对：该记录为辅助凭证，不重复计入合计"
            elif role == "UNRESOLVED":
                status_detail = "跨文件核对尚未确认，相关分类合计留空"

            views.append(
                ExpenseRecordView(
                    id=document.id * 1_000_000 + index,
                    record_ref=record_ref,
                    record_id=record_id,
                    source_document_id=document.id,
                    batch_id=document.batch_id,
                    source_folder=document.source_folder,
                    relative_path=document.relative_path,
                    file_name=document.file_name,
                    file_type=document.file_type,
                    document_type=document.document_type,
                    processing_status=document.processing_status,
                    status_reason=document.status_reason,
                    status_detail=status_detail,
                    raw_text=document.raw_text,
                    classification_confidence=document.classification_confidence,
                    extraction_confidence=document.extraction_confidence,
                    document_date=_date(record.get("document_date"))
                    or document.document_date,
                    counterparty=record.get("counterparty") or document.counterparty,
                    description=record.get("description") or document.description,
                    amount=_decimal(amount_payload.get("value")),
                    currency=document.currency,
                    invoice_number=record.get("invoice_number") or None,
                    transaction_number=transaction_number,
                    order_number=order_number,
                    record_type=str(record.get("record_type") or "OTHER"),
                    amount_evidence=amount_payload.get("evidence") or None,
                    transfer_direction=transfer.get("direction") or None,
                    reconciliation_relation=relation,
                    reconciliation_role=role,
                    reconciliation_group_id=state.get("group_id") or None,
                    reconciliation_primary_ref=state.get("primary_record_ref") or None,
                    reconciliation_confidence=(
                        float(state["confidence"])
                        if state.get("confidence") is not None
                        else None
                    ),
                    reconciliation_evidence=state.get("evidence") or None,
                    eligible_amount=_decimal(state.get("eligible_amount")),
                    processing_attempts=document.processing_attempts,
                    created_at=document.created_at,
                    updated_at=document.updated_at,
                )
            )
    return views
