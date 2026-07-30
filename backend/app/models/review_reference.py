from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base


class BatchReviewReference(Base):
    __tablename__ = "batch_review_references"
    __table_args__ = (
        UniqueConstraint(
            "batch_id",
            "version",
            name="uq_batch_review_references_batch_version",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(
        Integer,
        ForeignKey("processing_batches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version = Column(Integer, nullable=False)
    original_filename = Column(String(255), nullable=False)
    file_hash = Column(String(64), nullable=False, index=True)
    file_size = Column(BigInteger, nullable=False)
    file_data = Column(LargeBinary, nullable=False)
    sheet_names = Column(JSON, nullable=False, default=list)
    parsed_rows = Column(JSON, nullable=False, default=list)
    row_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    batch = relationship("ProcessingBatch", back_populates="review_references")
