from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from app.models.reimbursement_package import PackageStatus
from app.schemas.batch import BatchResponse


class PackageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_name: str
    status: PackageStatus
    status_reason: str | None = None
    status_detail: str | None = None
    original_filename: str
    file_hash: str
    file_size: int
    sheet_name: str | None = None
    claim_count: int = 0
    audited_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class PackageListResponse(BaseModel):
    items: list[PackageResponse]
    total: int
    page: int
    page_size: int


class PackageDetailResponse(PackageResponse):
    batches: list[BatchResponse] = Field(default_factory=list)
    months: list[str] = Field(default_factory=list)


class PackageAuditResponse(BaseModel):
    package_id: int
    status: PackageStatus
    claim_count: int
    audited_at: datetime | None = None
    message: str

    @field_serializer("audited_at", when_used="json")
    def serialize_audited_at(self, value: datetime | None) -> str | None:
        return value.isoformat() if value else None
