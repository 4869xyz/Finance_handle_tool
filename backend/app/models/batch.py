from datetime import datetime
from enum import Enum

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    Integer,
    JSON,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base


def _enum_type(enum_class: type[Enum], name: str) -> SQLEnum:
    return SQLEnum(
        enum_class,
        name=name,
        native_enum=False,
        values_callable=lambda values: [item.value for item in values],
    )


class BatchStatus(str, Enum):
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class SourceFolder(str, Enum):
    INVOICE = "INVOICE"
    REPLACEMENT_INVOICE = "REPLACEMENT_INVOICE"
    PAYMENT_EVIDENCE = "PAYMENT_EVIDENCE"


class DocumentType(str, Enum):
    VAT_INVOICE = "VAT_INVOICE"
    ELECTRONIC_INVOICE = "ELECTRONIC_INVOICE"
    TAXI_INVOICE = "TAXI_INVOICE"
    ITINERARY = "ITINERARY"
    TRAIN_OR_AIR_TICKET = "TRAIN_OR_AIR_TICKET"
    PAYMENT_SCREENSHOT = "PAYMENT_SCREENSHOT"
    ORDER_SCREENSHOT = "ORDER_SCREENSHOT"
    OVERSEAS_INVOICE_OR_RECEIPT = "OVERSEAS_INVOICE_OR_RECEIPT"
    RECEIPT = "RECEIPT"
    OTHER_DOCUMENT = "OTHER_DOCUMENT"
    UNKNOWN = "UNKNOWN"
    VIDEO = "VIDEO"


class ProcessingStatus(str, Enum):
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    RECOGNIZED = "RECOGNIZED"
    NEEDS_MANUAL_REVIEW = "NEEDS_MANUAL_REVIEW"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


class ProcessingBatch(Base):
    __tablename__ = "processing_batches"

    id = Column(Integer, primary_key=True, index=True)
    employee_name = Column(String(100), nullable=False)
    expense_month = Column(Date, nullable=False, index=True)
    status = Column(
        _enum_type(BatchStatus, "batch_status"),
        nullable=False,
        default=BatchStatus.UPLOADED,
        index=True,
    )
    status_reason = Column(String(100), nullable=True)
    total_files = Column(Integer, nullable=False, default=0)
    recognized_files = Column(Integer, nullable=False, default=0)
    manual_review_files = Column(Integer, nullable=False, default=0)
    skipped_files = Column(Integer, nullable=False, default=0)
    failed_files = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    documents = relationship(
        "SourceDocument",
        back_populates="batch",
        cascade="all, delete-orphan",
    )
    review_references = relationship(
        "BatchReviewReference",
        back_populates="batch",
        cascade="all, delete-orphan",
    )


class SourceDocument(Base):
    __tablename__ = "source_documents"
    __table_args__ = (
        UniqueConstraint(
            "batch_id",
            "relative_path",
            name="uq_source_documents_batch_relative_path",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(
        Integer,
        ForeignKey("processing_batches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_folder = Column(
        _enum_type(SourceFolder, "source_folder"),
        nullable=False,
        index=True,
    )
    relative_path = Column(String(1000), nullable=False)
    file_name = Column(String(255), nullable=False)
    file_type = Column(String(20), nullable=False)
    file_size = Column(BigInteger, nullable=False)
    file_hash = Column(String(64), nullable=False, index=True)
    file_data = Column(LargeBinary, nullable=True)

    document_type = Column(
        _enum_type(DocumentType, "document_type"),
        nullable=False,
        default=DocumentType.UNKNOWN,
        index=True,
    )
    processing_status = Column(
        _enum_type(ProcessingStatus, "processing_status"),
        nullable=False,
        default=ProcessingStatus.UPLOADED,
        index=True,
    )
    status_reason = Column(String(100), nullable=True)
    status_detail = Column(Text, nullable=True)

    raw_text = Column(Text, nullable=True)
    classification_confidence = Column(Numeric(5, 4), nullable=True)
    extraction_confidence = Column(Numeric(5, 4), nullable=True)

    document_date = Column(Date, nullable=True)
    counterparty = Column(String(255), nullable=True)
    description = Column(String(1000), nullable=True)
    amount = Column(Numeric(14, 2), nullable=True)
    currency = Column(String(10), nullable=True)
    invoice_number = Column(String(100), nullable=True)
    transaction_number = Column(String(150), nullable=True)
    item_name = Column(String(1000), nullable=True)
    order_number = Column(String(150), nullable=True)
    trip_origin = Column(String(1000), nullable=True)
    trip_destination = Column(String(1000), nullable=True)
    trip_time_text = Column(String(1000), nullable=True)

    extracted_fields_json = Column(JSON, nullable=True)
    classification_raw_response = Column(Text, nullable=True)
    extraction_raw_response = Column(Text, nullable=True)
    model_provider = Column(String(50), nullable=True)
    model_name = Column(String(100), nullable=True)

    processing_started_at = Column(DateTime, nullable=True)
    processing_attempts = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    batch = relationship("ProcessingBatch", back_populates="documents")


# Register the related model for workers that import batch models without routers.
from app.models.review_reference import BatchReviewReference  # noqa: E402,F401
