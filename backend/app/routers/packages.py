from io import BytesIO
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.batch import ProcessingBatch
from app.models.reimbursement_package import ReimbursementPackage
from app.schemas.batch import BatchResponse
from app.schemas.package import (
    PackageAuditResponse,
    PackageDetailResponse,
    PackageListResponse,
    PackageResponse,
)
from app.services.batch_service import BatchValidationError
from app.services.claim_completeness_service import audit_package, export_audited_workbook
from app.services.package_ingest_service import register_package


router = APIRouter()


def _package_response(package: ReimbursementPackage) -> PackageResponse:
    claims = package.parsed_claims or []
    return PackageResponse.model_validate(package).model_copy(
        update={"claim_count": len(claims)}
    )


@router.post("", response_model=PackageDetailResponse, status_code=202)
async def create_package(
    response: Response,
    files: list[UploadFile] = File(...),
    relative_paths: list[str] = Form(...),
    db: AsyncSession = Depends(get_db),
):
    try:
        package = await register_package(
            db,
            files=files,
            relative_paths=relative_paths,
        )
    except BatchValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result = await db.execute(
        select(ProcessingBatch)
        .where(ProcessingBatch.package_id == package.id)
        .order_by(ProcessingBatch.expense_month)
    )
    batches = result.scalars().all()
    response.headers["Location"] = f"/api/packages/{package.id}"
    return PackageDetailResponse(
        **_package_response(package).model_dump(),
        batches=[BatchResponse.model_validate(item) for item in batches],
        months=[item.expense_month.strftime("%Y-%m") for item in batches],
    )


@router.get("", response_model=PackageListResponse)
async def list_packages(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    total = await db.scalar(select(func.count()).select_from(ReimbursementPackage)) or 0
    result = await db.execute(
        select(ReimbursementPackage)
        .order_by(ReimbursementPackage.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    packages = result.scalars().all()
    return PackageListResponse(
        items=[_package_response(item) for item in packages],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{package_id}", response_model=PackageDetailResponse)
async def get_package(
    package_id: int,
    db: AsyncSession = Depends(get_db),
):
    package = await db.get(ReimbursementPackage, package_id)
    if not package:
        raise HTTPException(status_code=404, detail="提交包不存在")
    result = await db.execute(
        select(ProcessingBatch)
        .where(ProcessingBatch.package_id == package_id)
        .order_by(ProcessingBatch.expense_month)
    )
    batches = result.scalars().all()
    return PackageDetailResponse(
        **_package_response(package).model_dump(),
        batches=[BatchResponse.model_validate(item) for item in batches],
        months=[item.expense_month.strftime("%Y-%m") for item in batches],
    )


@router.post("/{package_id}/audit", response_model=PackageAuditResponse)
async def run_package_audit(
    package_id: int,
    db: AsyncSession = Depends(get_db),
):
    package = await db.get(ReimbursementPackage, package_id)
    if not package:
        raise HTTPException(status_code=404, detail="提交包不存在")
    result = await db.execute(
        select(ProcessingBatch.status).where(
            ProcessingBatch.package_id == package_id
        )
    )
    statuses = list(result.scalars().all())
    if not statuses:
        raise HTTPException(status_code=400, detail="提交包没有可审核的月份批次")
    if any(status.value not in {"COMPLETED", "FAILED"} for status in statuses):
        raise HTTPException(status_code=400, detail="请等待各月识别完成后再审核")
    audited = await audit_package(db, package_id)
    return PackageAuditResponse(
        package_id=audited.id,
        status=audited.status,
        claim_count=len(audited.parsed_claims or []),
        audited_at=audited.audited_at,
        message="总表审核已完成",
    )


@router.get("/{package_id}/export/claims")
async def export_package_claims(
    package_id: int,
    db: AsyncSession = Depends(get_db),
):
    package = await db.get(ReimbursementPackage, package_id)
    if not package:
        raise HTTPException(status_code=404, detail="提交包不存在")
    if not package.audit_results:
        raise HTTPException(status_code=400, detail="尚未完成总表审核，无法导出")
    payload = export_audited_workbook(package)
    filename = quote(f"{package.employee_name}-报销总表-系统审核.xlsx")
    return StreamingResponse(
        BytesIO(payload),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
        },
    )
