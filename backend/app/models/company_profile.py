from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, JSON, String

from app.database import Base


class CompanyProfile(Base):
    """Append-only company identity used by reimbursement filtering."""

    __tablename__ = "company_profiles"

    version = Column(Integer, primary_key=True, autoincrement=True)
    official_name = Column(String(255), nullable=False)
    aliases = Column(JSON, nullable=False, default=list)
    tax_ids = Column(JSON, nullable=False, default=list)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
