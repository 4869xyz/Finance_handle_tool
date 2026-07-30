import asyncio
import re
from copy import copy
from datetime import date
from decimal import Decimal
from io import BytesIO
from urllib.parse import quote

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
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.batch import (
    BatchStatus,
    DocumentType,
    ProcessingStatus,
    ProcessingBatch,
    SourceDocument,
    SourceFolder,
)
from app.models.company_profile import CompanyProfile
from app.models.review_reference import BatchReviewReference
from app.schemas.batch import (
    BatchDetailResponse,
    BatchListResponse,
    BatchResponse,
    ReprocessRequest,
    ReprocessResponse,
    ReviewReferenceListResponse,
    ReviewReferenceResponse,
    SourceDocumentListResponse,
    SourceDocumentResponse,
)
from app.services.batch_service import (
    BatchValidationError,
    prepare_reprocess,
    register_batch,
)
from app.services.exclusion_service import FilterOutcome, filter_documents
from app.services.expense_classification_service import (
    EXPENSE_CLASSIFICATION_RULE_VERSION,
    ExpenseClassificationStatus,
    build_expense_summary_groups,
    classification_risk_reason,
    classify_expenses,
)
from app.services.review_reference_service import (
    MAX_REFERENCE_FILE_SIZE,
    ReviewReferenceValidationError,
    parse_review_reference,
)


router = APIRouter()


SOURCE_FOLDER_LABELS = {
    SourceFolder.INVOICE: "发票",
    SourceFolder.REPLACEMENT_INVOICE: "补票",
    SourceFolder.PAYMENT_EVIDENCE: "支付凭证",
}

DOCUMENT_TYPE_LABELS = {
    DocumentType.VAT_INVOICE: "增值税发票",
    DocumentType.ELECTRONIC_INVOICE: "电子/数电发票",
    DocumentType.TAXI_INVOICE: "出租车发票",
    DocumentType.ITINERARY: "行程单",
    DocumentType.TRAIN_OR_AIR_TICKET: "火车/航空票",
    DocumentType.PAYMENT_SCREENSHOT: "支付凭证",
    DocumentType.ORDER_SCREENSHOT: "订单凭证",
    DocumentType.OVERSEAS_INVOICE_OR_RECEIPT: "境外发票/收据",
    DocumentType.RECEIPT: "收据/小票",
    DocumentType.OTHER_DOCUMENT: "其他资料",
    DocumentType.UNKNOWN: "待判定",
    DocumentType.VIDEO: "视频",
}

PROCESSING_STATUS_LABELS = {
    ProcessingStatus.UPLOADED: "待识别",
    ProcessingStatus.PROCESSING: "识别中",
    ProcessingStatus.RECOGNIZED: "已识别",
    ProcessingStatus.NEEDS_MANUAL_REVIEW: "需人工复核",
    ProcessingStatus.SKIPPED: "已跳过",
    ProcessingStatus.FAILED: "处理失败",
}

STATUS_REASON_LABELS = {
    "UNSUPPORTED_VIDEO": "视频文件暂不处理，需要人工处理",
    "DUPLICATE_SUMMARY_PDF": "重复汇总PDF，已跳过",
    "IMAGE_PIXEL_LIMIT_EXCEEDED": "图片像素过大，需要人工处理",
    "MULTIPLE_TRANSACTIONS": "包含多笔交易且没有可信汇总金额，需要人工复核",
    "INVALID_MODEL_RESPONSE": "模型返回格式无效，需要人工复核",
    "LLM_UNAVAILABLE": "模型服务不可用，规则结果不足以自动通过",
    "LOW_CLASSIFICATION_CONFIDENCE": "文档类型置信度不足",
    "LOW_EXTRACTION_CONFIDENCE": "字段提取置信度不足",
    "MISSING_CRITICAL_FIELDS": "缺少关键字段",
    "UNKNOWN_DOCUMENT_TYPE": "无法可靠判断文档类型",
    "PROCESSING_ERROR": "文档处理失败",
    "PROCESS_INTERRUPTED": "服务重启导致处理任务中断，可重新处理",
    "COORDINATOR_FAILED": "批次协调任务失败",
}

EXPORT_HEADERS = [
    "序号",
    "员工姓名",
    "报销月份",
    "批次号",
    "来源",
    "文件名",
    "相对路径",
    "资料类型",
    "处理状态",
    "状态原因",
    "详细说明",
    "资料日期",
    "对方",
    "摘要",
    "金额",
    "币种",
    "发票号码",
    "交易/订单号",
    "类型置信度",
    "提取置信度",
    "处理次数",
    "更新时间",
]

FILTER_HEADERS = [
    "筛选结论",
    "原因代码",
    "原因说明",
    "判定依据",
    "购买方名称",
    "购买方税号",
    "筛选置信度",
    "公司档案版本",
    "规则版本",
    "辅助表版本",
    "辅助匹配依据",
]

CLASSIFICATION_HEADERS = [
    "行类型",
    "序号",
    "资料日期",
    "员工姓名",
    "来源",
    "费用类型",
    "标准子类",
    "事项",
    "招待对象",
    "金额",
    "币种",
    "对方",
    "资料类型",
    "发票号码",
    "交易/订单号",
    "文件名",
    "筛选结论",
    "风险说明",
    "分类状态",
    "分类置信度",
    "分类依据",
    "辅助表版本",
    "辅助匹配依据",
    "核对键",
    "发票补票单据数",
    "发票补票金额",
    "支付凭证单据数",
    "支付凭证金额",
    "差额（发票-支付）",
    "核对状态",
    "分类规则版本",
]


def _safe_export_filename(employee_name: str, expense_month: date) -> str:
    safe_employee = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", employee_name).strip(" .")
    if not safe_employee:
        safe_employee = "员工"
    return f"{safe_employee}_{expense_month.strftime('%Y-%m')}_识别结果.xlsx"


def _safe_filtered_export_filename(employee_name: str, expense_month: date) -> str:
    safe_employee = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", employee_name).strip(" .")
    if not safe_employee:
        safe_employee = "员工"
    return f"{safe_employee}_{expense_month.strftime('%Y-%m')}_剔除结果.xlsx"


def _document_export_row(batch: ProcessingBatch, document: SourceDocument, index: int):
    return [
        index,
        batch.employee_name,
        batch.expense_month.strftime("%Y-%m"),
        batch.id,
        SOURCE_FOLDER_LABELS.get(document.source_folder, document.source_folder.value),
        document.file_name,
        document.relative_path,
        DOCUMENT_TYPE_LABELS.get(document.document_type, document.document_type.value),
        PROCESSING_STATUS_LABELS.get(
            document.processing_status,
            document.processing_status.value,
        ),
        STATUS_REASON_LABELS.get(document.status_reason, document.status_reason or ""),
        document.status_detail or "",
        document.document_date,
        document.counterparty or "",
        document.description or "",
        float(document.amount) if document.amount is not None else None,
        document.currency or "",
        document.invoice_number or "",
        document.transaction_number or "",
        (
            float(document.classification_confidence)
            if document.classification_confidence is not None
            else None
        ),
        (
            float(document.extraction_confidence)
            if document.extraction_confidence is not None
            else None
        ),
        document.processing_attempts,
        document.updated_at,
    ]


def _column_index(column: str) -> int:
    result = 0
    for character in column:
        result = result * 26 + ord(character) - ord("A") + 1
    return result


def _configure_export_worksheet(worksheet, *, header_fill_color: str = "1677FF"):
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    worksheet.sheet_view.showGridLines = False
    worksheet.freeze_panes = "A2"
    header_fill = PatternFill("solid", fgColor=header_fill_color)
    header_font = Font(bold=True, color="FFFFFF")
    for cell in worksheet[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
    worksheet.row_dimensions[1].height = 26
    worksheet.auto_filter.ref = (
        f"A1:{get_column_letter(worksheet.max_column)}{max(1, worksheet.max_row)}"
    )

    column_widths = {
        "A": 8, "B": 14, "C": 12, "D": 10, "E": 12, "F": 28,
        "G": 42, "H": 18, "I": 14, "J": 34, "K": 42, "L": 13,
        "M": 24, "N": 36, "O": 14, "P": 10, "Q": 20, "R": 22,
        "S": 14, "T": 14, "U": 12, "V": 20, "W": 12, "X": 32,
        "Y": 34, "Z": 42, "AA": 24, "AB": 22, "AC": 14,
        "AD": 14, "AE": 12, "AF": 12, "AG": 52,
    }
    for column, width in column_widths.items():
        if worksheet.max_column >= _column_index(column):
            worksheet.column_dimensions[column].width = width

    for row in worksheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top")
        for column_index in (6, 9, 10, 13, 24, 25, 32):
            if column_index < len(row):
                row[column_index].alignment = Alignment(vertical="top", wrap_text=True)
        row[11].number_format = "yyyy-mm-dd"
        row[14].number_format = "#,##0.00"
        row[18].number_format = "0.0%"
        row[19].number_format = "0.0%"
        row[21].number_format = "yyyy-mm-dd hh:mm:ss"
        if len(row) >= 29:
            row[28].number_format = "0.0%"


def _append_filter_row(worksheet, base_row: list, decision):
    worksheet.append(base_row + [
        decision.outcome.value,
        decision.reason_code,
        decision.reason,
        decision.evidence,
        decision.buyer_name or "",
        decision.buyer_tax_id or "",
        decision.confidence,
        decision.company_profile_version,
        decision.rule_version,
        decision.reference_version,
        decision.reference_evidence,
    ])


def _safe_excel_text(value):
    if not isinstance(value, str):
        return value
    if value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _configure_classification_worksheet(worksheet):
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    worksheet.sheet_view.showGridLines = False
    worksheet.freeze_panes = "A2"
    worksheet.sheet_properties.outlinePr.summaryBelow = True
    worksheet.auto_filter.ref = (
        f"A1:{get_column_letter(worksheet.max_column)}{max(1, worksheet.max_row)}"
    )

    header_fill = PatternFill("solid", fgColor="531DAB")
    header_font = Font(bold=True, color="FFFFFF")
    for cell in worksheet[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
    worksheet.row_dimensions[1].height = 28

    widths = {
        "A": 10, "B": 8, "C": 13, "D": 14, "E": 10, "F": 14,
        "G": 14, "H": 34, "I": 20, "J": 14, "K": 12, "L": 24,
        "M": 18, "N": 20, "O": 22, "P": 30, "Q": 12, "R": 42,
        "S": 16, "T": 14, "U": 42, "V": 12, "W": 52, "X": 38,
        "Y": 16, "Z": 16, "AA": 18, "AB": 18, "AC": 18,
        "AD": 16, "AE": 14,
    }
    for column, width in widths.items():
        worksheet.column_dimensions[column].width = width

    for row in worksheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top")
        for index in (7, 8, 11, 15, 17, 20, 22, 23):
            row[index].alignment = Alignment(vertical="top", wrap_text=True)
        row[2].number_format = "yyyy-mm-dd"
        for index in (9, 25, 27, 28):
            row[index].number_format = "#,##0.00"
        row[19].number_format = "0.0%"


def _append_classification_worksheet(
    worksheet,
    *,
    batch: ProcessingBatch,
    documents: list[SourceDocument],
    decision_by_document: dict,
    classification_by_document: dict,
    groups: list,
    source_folder: SourceFolder,
):
    from openpyxl.styles import Font, PatternFill

    document_by_id = {document.id: document for document in documents}
    detail_count = 0
    detail_index = 1
    subtotal_fill = PatternFill("solid", fgColor="E6F4FF")
    exception_fill = PatternFill("solid", fgColor="FCE8E6")
    total_fill = PatternFill("solid", fgColor="D3ADF7")

    for group in groups:
        document_ids = (
            group.payment_document_ids
            if source_folder == SourceFolder.PAYMENT_EVIDENCE
            else group.invoice_document_ids
        )
        group_documents = sorted(
            (document_by_id[document_id] for document_id in document_ids),
            key=lambda document: (
                document.document_date or date.min,
                document.id,
            ),
        )
        detail_start = worksheet.max_row + 1
        for document in group_documents:
            decision = decision_by_document[document.id]
            classification = classification_by_document[document.id]
            risk_reason = classification_risk_reason(
                document,
                decision,
                classification,
            )
            worksheet.append([
                "明细",
                detail_index,
                document.document_date,
                batch.employee_name,
                SOURCE_FOLDER_LABELS.get(document.source_folder, document.source_folder.value),
                classification.expense_type,
                classification.standard_subcategory,
                classification.expense_item,
                classification.hospitality_target,
                float(document.amount) if document.amount is not None else None,
                document.currency or "币种待确认",
                document.counterparty or "",
                DOCUMENT_TYPE_LABELS.get(document.document_type, document.document_type.value),
                document.invoice_number or "",
                document.transaction_number or "",
                document.file_name,
                decision.outcome.value,
                risk_reason,
                classification.status.value,
                classification.confidence,
                classification.evidence,
                classification.reference_version,
                classification.reference_evidence or decision.reference_evidence,
                classification.reconciliation_key,
                None,
                None,
                None,
                None,
                None,
                None,
                classification.rule_version,
            ])
            detail_row = worksheet.max_row
            worksheet.row_dimensions[detail_row].outlineLevel = 1
            if risk_reason:
                for cell in worksheet[detail_row]:
                    red_font = copy(cell.font)
                    red_font.color = "C00000"
                    cell.font = red_font
            for cell in worksheet[detail_row]:
                cell.value = _safe_excel_text(cell.value)
            detail_count += 1
            detail_index += 1

        worksheet.append([
            "小计",
            None,
            None,
            batch.employee_name,
            "发票补票/支付凭证",
            group.expense_type,
            group.standard_subcategory,
            group.expense_item,
            group.hospitality_target,
            None,
            group.currency,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            len(group.invoice_document_ids),
            float(group.invoice_amount),
            len(group.payment_document_ids),
            float(group.payment_amount),
            float(group.difference),
            group.reconciliation_status,
            EXPENSE_CLASSIFICATION_RULE_VERSION,
        ])
        subtotal_row = worksheet.max_row
        for cell in worksheet[subtotal_row]:
            cell.fill = (
                subtotal_fill
                if group.reconciliation_status == "金额一致"
                else exception_fill
            )
            cell.font = Font(
                bold=True,
                color=(
                    "389E0D"
                    if group.reconciliation_status == "金额一致"
                    else "C00000"
                ),
            )
            cell.value = _safe_excel_text(cell.value)
        if group_documents:
            worksheet.row_dimensions.group(
                detail_start,
                subtotal_row - 1,
                outline_level=1,
                hidden=False,
            )

    currencies = sorted({group.currency for group in groups})
    for currency in currencies:
        currency_groups = [group for group in groups if group.currency == currency]
        invoice_ids = [
            document_id
            for group in currency_groups
            for document_id in group.invoice_document_ids
        ]
        payment_ids = [
            document_id
            for group in currency_groups
            for document_id in group.payment_document_ids
        ]
        invoice_amount = sum(
            (group.invoice_amount for group in currency_groups),
            Decimal("0.00"),
        )
        payment_amount = sum(
            (group.payment_amount for group in currency_groups),
            Decimal("0.00"),
        )
        missing_amount = any(
            group.invoice_missing_amount or group.payment_missing_amount
            for group in currency_groups
        )
        if currency == "币种待确认" or missing_amount:
            status = "金额待确认"
        elif not invoice_ids:
            status = "缺发票补票"
        elif not payment_ids:
            status = "缺支付凭证"
        elif invoice_amount == payment_amount:
            status = "金额一致"
        else:
            status = "金额不一致"
        worksheet.append([
            "总计", None, None, batch.employee_name, "发票补票/支付凭证",
            "按币种总计", "", "", "", None, currency,
            "", "", "", "", "", "", "", "", None, "", None, "", "",
            len(invoice_ids), float(invoice_amount), len(payment_ids),
            float(payment_amount), float(invoice_amount - payment_amount), status,
            EXPENSE_CLASSIFICATION_RULE_VERSION,
        ])
        for cell in worksheet[worksheet.max_row]:
            cell.fill = total_fill
            cell.font = Font(bold=True, color="531DAB")

    _configure_classification_worksheet(worksheet)
    return detail_count


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
    "/{batch_id}/review-references",
    response_model=ReviewReferenceResponse,
    status_code=201,
)
async def upload_review_reference(
    batch_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    batch = await db.get(ProcessingBatch, batch_id, with_for_update=True)
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    filename = (file.filename or "").strip()
    file_data = await file.read(MAX_REFERENCE_FILE_SIZE + 1)
    try:
        parsed = parse_review_reference(file_data, filename)
    except ReviewReferenceValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    latest_version = await db.scalar(
        select(func.max(BatchReviewReference.version)).where(
            BatchReviewReference.batch_id == batch_id
        )
    )
    reference = BatchReviewReference(
        batch_id=batch_id,
        version=(latest_version or 0) + 1,
        original_filename=filename,
        file_hash=parsed.file_hash,
        file_size=parsed.file_size,
        file_data=file_data,
        sheet_names=parsed.sheet_names,
        parsed_rows=parsed.rows,
        row_count=len(parsed.rows),
    )
    db.add(reference)
    await db.commit()
    await db.refresh(reference)
    return ReviewReferenceResponse.model_validate(reference).model_copy(
        update={"is_current": True}
    )


@router.get(
    "/{batch_id}/review-references",
    response_model=ReviewReferenceListResponse,
)
async def list_review_references(
    batch_id: int,
    db: AsyncSession = Depends(get_db),
):
    if not await db.get(ProcessingBatch, batch_id):
        raise HTTPException(status_code=404, detail="批次不存在")
    result = await db.execute(
        select(BatchReviewReference)
        .where(BatchReviewReference.batch_id == batch_id)
        .order_by(BatchReviewReference.version.desc())
    )
    references = result.scalars().all()
    return ReviewReferenceListResponse(
        items=[
            ReviewReferenceResponse.model_validate(reference).model_copy(
                update={"is_current": index == 0}
            )
            for index, reference in enumerate(references)
        ]
    )


@router.get("/{batch_id}/review-references/{version}/download")
async def download_review_reference(
    batch_id: int,
    version: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(BatchReviewReference).where(
            BatchReviewReference.batch_id == batch_id,
            BatchReviewReference.version == version,
        )
    )
    reference = result.scalar_one_or_none()
    if not reference:
        raise HTTPException(status_code=404, detail="报销明细参考表版本不存在")
    filename = quote(reference.original_filename)
    return StreamingResponse(
        BytesIO(reference.file_data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
        },
    )


@router.get("/{batch_id}/export/excel")
async def export_batch_documents_excel(
    batch_id: int,
    db: AsyncSession = Depends(get_db),
):
    batch = await db.get(ProcessingBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")

    result = await db.execute(
        select(SourceDocument)
        .where(SourceDocument.batch_id == batch_id)
        .order_by(SourceDocument.id)
    )
    documents = result.scalars().all()

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "识别结果"
    worksheet.sheet_view.showGridLines = False
    worksheet.freeze_panes = "A2"
    worksheet.append(EXPORT_HEADERS)

    header_fill = PatternFill("solid", fgColor="1677FF")
    header_font = Font(bold=True, color="FFFFFF")
    for cell in worksheet[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
    worksheet.row_dimensions[1].height = 26

    for index, document in enumerate(documents, start=1):
        worksheet.append([
            index,
            batch.employee_name,
            batch.expense_month.strftime("%Y-%m"),
            batch.id,
            SOURCE_FOLDER_LABELS.get(document.source_folder, document.source_folder.value),
            document.file_name,
            document.relative_path,
            DOCUMENT_TYPE_LABELS.get(document.document_type, document.document_type.value),
            PROCESSING_STATUS_LABELS.get(
                document.processing_status,
                document.processing_status.value,
            ),
            STATUS_REASON_LABELS.get(document.status_reason, document.status_reason or ""),
            document.status_detail or "",
            document.document_date,
            document.counterparty or "",
            document.description or "",
            float(document.amount) if document.amount is not None else None,
            document.currency or "",
            document.invoice_number or "",
            document.transaction_number or "",
            (
                float(document.classification_confidence)
                if document.classification_confidence is not None
                else None
            ),
            (
                float(document.extraction_confidence)
                if document.extraction_confidence is not None
                else None
            ),
            document.processing_attempts,
            document.updated_at,
        ])

    worksheet.auto_filter.ref = f"A1:V{max(1, worksheet.max_row)}"
    column_widths = {
        "A": 8,
        "B": 14,
        "C": 12,
        "D": 10,
        "E": 12,
        "F": 28,
        "G": 42,
        "H": 18,
        "I": 14,
        "J": 34,
        "K": 42,
        "L": 13,
        "M": 24,
        "N": 36,
        "O": 14,
        "P": 10,
        "Q": 20,
        "R": 22,
        "S": 14,
        "T": 14,
        "U": 12,
        "V": 20,
    }
    for column, width in column_widths.items():
        worksheet.column_dimensions[column].width = width

    for row in worksheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top")
        row[6].alignment = Alignment(vertical="top", wrap_text=True)
        row[9].alignment = Alignment(vertical="top", wrap_text=True)
        row[10].alignment = Alignment(vertical="top", wrap_text=True)
        row[13].alignment = Alignment(vertical="top", wrap_text=True)
        row[11].number_format = "yyyy-mm-dd"
        row[14].number_format = "#,##0.00"
        row[18].number_format = "0.0%"
        row[19].number_format = "0.0%"
        row[21].number_format = "yyyy-mm-dd hh:mm:ss"

    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    filename = quote(_safe_export_filename(batch.employee_name, batch.expense_month))
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
        },
    )


@router.post("/{batch_id}/export/filtered")
async def export_filtered_batch_documents_excel(
    batch_id: int,
    db: AsyncSession = Depends(get_db),
):
    batch = await db.get(ProcessingBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    if batch.status not in {BatchStatus.COMPLETED, BatchStatus.FAILED}:
        raise HTTPException(status_code=409, detail="请等待批次处理结束后再生成剔除表格")

    result = await db.execute(
        select(SourceDocument)
        .where(SourceDocument.batch_id == batch_id)
        .order_by(SourceDocument.id)
    )
    documents = list(result.scalars().all())
    profile_result = await db.execute(
        select(CompanyProfile).order_by(CompanyProfile.version.desc()).limit(1)
    )
    profile = profile_result.scalar_one_or_none()
    reference_result = await db.execute(
        select(BatchReviewReference)
        .where(BatchReviewReference.batch_id == batch_id)
        .order_by(BatchReviewReference.version.desc())
        .limit(1)
    )
    reference = reference_result.scalar_one_or_none()

    decisions = await asyncio.to_thread(
        filter_documents,
        documents,
        profile,
        reference,
    )
    decision_by_document = {decision.document_id: decision for decision in decisions}
    if len(decision_by_document) != len(documents):
        raise HTTPException(status_code=500, detail="筛选结果与原始资料数量不一致")
    classification_by_document = await asyncio.to_thread(
        classify_expenses,
        documents,
        reference,
    )
    if len(classification_by_document) != len(documents):
        raise HTTPException(status_code=500, detail="分类结果与原始资料数量不一致")
    summary_groups = build_expense_summary_groups(
        documents,
        classification_by_document,
    )

    from openpyxl import Workbook
    from openpyxl.styles import PatternFill

    workbook = Workbook()
    original_sheet = workbook.active
    original_sheet.title = "识别结果"
    original_sheet.append(EXPORT_HEADERS)

    invoice_sheet = workbook.create_sheet("发票补票_剔除后")
    invoice_sheet.append(EXPORT_HEADERS + FILTER_HEADERS)
    payment_sheet = workbook.create_sheet("支付凭证_剔除后")
    payment_sheet.append(EXPORT_HEADERS + FILTER_HEADERS)
    exception_sheet = workbook.create_sheet("排除与待复核")
    exception_sheet.append(EXPORT_HEADERS + FILTER_HEADERS)
    invoice_classification_sheet = workbook.create_sheet("发票补票_分类汇总")
    invoice_classification_sheet.append(CLASSIFICATION_HEADERS)
    payment_classification_sheet = workbook.create_sheet("支付凭证_分类汇总")
    payment_classification_sheet.append(CLASSIFICATION_HEADERS)

    for index, document in enumerate(documents, start=1):
        base_row = _document_export_row(batch, document, index)
        original_sheet.append(base_row)
        decision = decision_by_document[document.id]
        target = (
            payment_sheet
            if document.source_folder == SourceFolder.PAYMENT_EVIDENCE
            else invoice_sheet
        )
        _append_filter_row(target, base_row, decision)
        if decision.outcome != FilterOutcome.KEEP:
            for cell in target[target.max_row]:
                red_font = copy(cell.font)
                red_font.color = "C00000"
                cell.font = red_font
            _append_filter_row(exception_sheet, base_row, decision)

    _configure_export_worksheet(original_sheet)
    _configure_export_worksheet(invoice_sheet, header_fill_color="389E0D")
    _configure_export_worksheet(payment_sheet, header_fill_color="389E0D")
    _configure_export_worksheet(exception_sheet, header_fill_color="D46B08")

    exclude_fill = PatternFill("solid", fgColor="FCE8E6")
    review_fill = PatternFill("solid", fgColor="FFF4CC")
    for row in exception_sheet.iter_rows(min_row=2):
        fill = exclude_fill if row[22].value == FilterOutcome.EXCLUDE.value else review_fill
        for cell in row:
            cell.fill = fill

    source_rows = invoice_sheet.max_row - 1 + payment_sheet.max_row - 1
    if source_rows != len(documents):
        raise HTTPException(status_code=500, detail="来源工作表行数对账失败")
    expected_exception_rows = sum(
        decision.outcome != FilterOutcome.KEEP for decision in decisions
    )
    if exception_sheet.max_row - 1 != expected_exception_rows:
        raise HTTPException(status_code=500, detail="异常工作表行数对账失败")

    invoice_classification_rows = _append_classification_worksheet(
        invoice_classification_sheet,
        batch=batch,
        documents=documents,
        decision_by_document=decision_by_document,
        classification_by_document=classification_by_document,
        groups=summary_groups,
        source_folder=SourceFolder.INVOICE,
    )
    payment_classification_rows = _append_classification_worksheet(
        payment_classification_sheet,
        batch=batch,
        documents=documents,
        decision_by_document=decision_by_document,
        classification_by_document=classification_by_document,
        groups=summary_groups,
        source_folder=SourceFolder.PAYMENT_EVIDENCE,
    )
    expected_invoice_rows = sum(
        document.source_folder
        in {SourceFolder.INVOICE, SourceFolder.REPLACEMENT_INVOICE}
        for document in documents
    )
    expected_payment_rows = sum(
        document.source_folder == SourceFolder.PAYMENT_EVIDENCE
        for document in documents
    )
    if invoice_classification_rows != expected_invoice_rows:
        raise HTTPException(status_code=500, detail="发票补票分类明细行数对账失败")
    if payment_classification_rows != expected_payment_rows:
        raise HTTPException(status_code=500, detail="支付凭证分类明细行数对账失败")

    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    filename = quote(
        _safe_filtered_export_filename(batch.employee_name, batch.expense_month)
    )
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
        },
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
