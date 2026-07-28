import re
from datetime import date

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.batch import (
    BatchStatus,
    ProcessingBatch,
    SourceDocument,
)
from app.schemas.batch import (
    BatchDetailResponse,
    BatchListResponse,
    BatchResponse,
    ReprocessRequest,
    ReprocessResponse,
    SourceDocumentListResponse,
    SourceDocumentResponse,
)
from app.services.batch_service import (
    BatchValidationError,
    prepare_reprocess,
    register_batch,
)


router = APIRouter()


def _parse_expense_month(value: str) -> date:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
        raise HTTPException(status_code=422, detail="expense_month 必须为 YYYY-MM")
    year, month = (int(part) for part in value.split("-"))
    return date(year, month, 1)


@router.post("", response_model=BatchResponse, status_code=202)
async def create_batch(
    response: Response,
    employee_name: str = Form(..., min_length=1, max_length=100),
    expense_month: str = Form(...),
    files: list[UploadFile] = File(...),
    relative_paths: list[str] = Form(...),
    db: AsyncSession = Depends(get_db),
):
    cleaned_employee = employee_name.strip()
    if not cleaned_employee:
        raise HTTPException(status_code=422, detail="employee_name 不能为空")
    month = _parse_expense_month(expense_month)
    try:
        batch = await register_batch(
            db,
            employee_name=cleaned_employee,
            expense_month=month,
            files=files,
            relative_paths=relative_paths,
        )
    except BatchValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    response.headers["Location"] = f"/api/batches/{batch.id}"
    return BatchResponse.model_validate(batch)


@router.get("", response_model=BatchListResponse)
async def list_batches(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    employee_name: str | None = Query(None),
    expense_month: str | None = Query(None),
    status: BatchStatus | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    query = select(ProcessingBatch)
    if employee_name:
        query = query.where(
            ProcessingBatch.employee_name.ilike(f"%{employee_name.strip()}%")
        )
    if expense_month:
        query = query.where(
            ProcessingBatch.expense_month == _parse_expense_month(expense_month)
        )
    if status:
        query = query.where(ProcessingBatch.status == status)

    total = await db.scalar(select(func.count()).select_from(query.subquery())) or 0
    result = await db.execute(
        query.order_by(ProcessingBatch.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = result.scalars().all()
    return BatchListResponse(
        items=[BatchResponse.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{batch_id}", response_model=BatchDetailResponse)
async def get_batch(
    batch_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ProcessingBatch).where(ProcessingBatch.id == batch_id)
    )
    batch = result.scalar_one_or_none()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")

    documents_result = await db.execute(
        select(
            SourceDocument.source_folder,
            SourceDocument.processing_status,
        ).where(SourceDocument.batch_id == batch_id)
    )
    source_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    for source_folder, processing_status in documents_result.all():
        source_counts[source_folder.value] = source_counts.get(source_folder.value, 0) + 1
        status_counts[processing_status.value] = (
            status_counts.get(processing_status.value, 0) + 1
        )
    # Use mode='python' so expense_month stays as date; JSON mode yields "YYYY-MM"
    # which fails when re-validating BatchDetailResponse.
    payload = BatchResponse.model_validate(batch).model_dump(mode="python")
    return BatchDetailResponse(
        **payload,
        source_counts=source_counts,
        status_counts=status_counts,
    )


@router.get("/{batch_id}/documents", response_model=SourceDocumentListResponse)
async def list_batch_documents(
    batch_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    exists = await db.scalar(
        select(func.count()).select_from(ProcessingBatch).where(
            ProcessingBatch.id == batch_id
        )
    )
    if not exists:
        raise HTTPException(status_code=404, detail="批次不存在")

    base_query = select(SourceDocument).where(SourceDocument.batch_id == batch_id)
    total = (
        await db.scalar(select(func.count()).select_from(base_query.subquery())) or 0
    )
    result = await db.execute(
        base_query.order_by(SourceDocument.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    documents = result.scalars().all()
    return SourceDocumentListResponse(
        items=[
            SourceDocumentResponse.model_validate(document)
            for document in documents
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/{batch_id}/reprocess",
    response_model=ReprocessResponse,
    status_code=202,
)
async def reprocess_batch(
    batch_id: int,
    payload: ReprocessRequest | None = Body(default=None),
    db: AsyncSession = Depends(get_db),
):
    exists = await db.scalar(
        select(func.count()).select_from(ProcessingBatch).where(
            ProcessingBatch.id == batch_id
        )
    )
    if not exists:
        raise HTTPException(status_code=404, detail="批次不存在")
    scheduled_count = await prepare_reprocess(
        db,
        batch_id,
        payload.document_ids if payload else None,
    )
    return ReprocessResponse(
        batch_id=batch_id,
        scheduled_count=scheduled_count,
        message=(
            f"已安排 {scheduled_count} 个文档重新处理"
            if scheduled_count
            else "没有符合重新处理条件的文档"
        ),
    )
