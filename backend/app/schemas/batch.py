from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from app.models.batch import (
    BatchStatus,
    DocumentType,
    ProcessingStatus,
    SourceFolder,
)


class BatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_name: str
    expense_month: date
    status: BatchStatus
    status_reason: str | None = None
    total_files: int
    recognized_files: int
    manual_review_files: int
    skipped_files: int
    failed_files: int
    package_id: int | None = None
    created_at: datetime
    updated_at: datetime

    @field_serializer("expense_month", when_used="json")
    def serialize_expense_month(self, value: date) -> str:
        return value.strftime("%Y-%m")


class BatchListResponse(BaseModel):
    items: list[BatchResponse]
    total: int
    page: int
    page_size: int


class SourceDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: int
    batch_id: int
    source_folder: SourceFolder
    relative_path: str
    file_name: str
    file_type: str
    file_size: int
    file_hash: str
    document_type: DocumentType
    processing_status: ProcessingStatus
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
    processing_started_at: datetime | None = None
    processing_attempts: int
    created_at: datetime
    updated_at: datetime

    @field_serializer("amount", "classification_confidence", "extraction_confidence")
    def serialize_decimal(self, value: Decimal | None) -> str | None:
        return str(value) if value is not None else None


class SourceDocumentListResponse(BaseModel):
    items: list[SourceDocumentResponse]
    total: int
    page: int
    page_size: int


class BatchDetailResponse(BatchResponse):
    source_counts: dict[str, int] = Field(default_factory=dict)
    status_counts: dict[str, int] = Field(default_factory=dict)


class ReprocessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_ids: list[int] | None = None

    @field_validator("document_ids")
    @classmethod
    def validate_document_ids(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        if not value:
            raise ValueError("document_ids 不能为空")
        if any(document_id <= 0 for document_id in value):
            raise ValueError("document_ids 必须为正整数")
        return list(dict.fromkeys(value))


class ReprocessResponse(BaseModel):
    batch_id: int
    scheduled_count: int
    message: str


class ReconcileResponse(BaseModel):
    batch_id: int
    status: str
    total_records: int
    confirmed_groups: int
    review_groups: int
    eligible_total: str | None
    message: str


class ReviewReferenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    batch_id: int
    version: int
    original_filename: str
    file_hash: str
    file_size: int
    sheet_names: list[str]
    row_count: int
    created_at: datetime
    is_current: bool = False


class ReviewReferenceListResponse(BaseModel):
    items: list[ReviewReferenceResponse]
