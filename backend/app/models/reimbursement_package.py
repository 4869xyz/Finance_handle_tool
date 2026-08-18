from datetime import datetime
from enum import Enum

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Enum as SQLEnum,
    Integer,
    JSON,
    LargeBinary,
    String,
    Text,
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


class PackageStatus(str, Enum):
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ReimbursementPackage(Base):
    __tablename__ = "reimbursement_packages"

    id = Column(Integer, primary_key=True, index=True)
    employee_name = Column(String(100), nullable=False)
    status = Column(
        _enum_type(PackageStatus, "package_status"),
        nullable=False,
        default=PackageStatus.UPLOADED,
        index=True,
    )
    status_reason = Column(String(100), nullable=True)
    status_detail = Column(Text, nullable=True)
    original_filename = Column(String(255), nullable=False)
    file_hash = Column(String(64), nullable=False, index=True)
    file_size = Column(BigInteger, nullable=False)
    file_data = Column(LargeBinary, nullable=False)
    sheet_name = Column(String(255), nullable=True)
    parsed_claims = Column(JSON, nullable=False, default=list)
    audit_results = Column(JSON, nullable=True)
    audited_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    batches = relationship(
        "ProcessingBatch",
        back_populates="package",
    )
