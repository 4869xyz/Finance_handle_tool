import asyncio
import hashlib
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO
from pathlib import PurePosixPath, PureWindowsPath

from fastapi import UploadFile
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import async_session_maker
from app.models.batch import (
    BatchStatus,
    DocumentType,
    ProcessingBatch,
    ProcessingStatus,
    SourceDocument,
    SourceFolder,
)


SOURCE_FOLDER_NAMES = {
    "发票": SourceFolder.INVOICE,
    "补票": SourceFolder.REPLACEMENT_INVOICE,
    "支付凭证": SourceFolder.PAYMENT_EVIDENCE,
}
VIDEO_EXTENSIONS = {"mp4", "mov", "avi", "mkv"}
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png"}
SPECIAL_REASONS = {
    "UNSUPPORTED_VIDEO",
    "DUPLICATE_SUMMARY_PDF",
    "IMAGE_PIXEL_LIMIT_EXCEEDED",
}


class BatchValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedUploadPath:
    source_folder: SourceFolder
    relative_path: str
    file_name: str
    file_type: str
    mode: str
    opaque_root: str | None


def _normalized_name(value: str) -> str:
    return unicodedata.normalize("NFKC", value)


def validate_upload_paths(
    files: list[UploadFile],
    relative_paths: list[str],
) -> list[ValidatedUploadPath]:
    settings = get_settings()
    if not files:
        raise BatchValidationError("至少需要上传一个文件")
    if len(files) != len(relative_paths):
        raise BatchValidationError("files 与 relative_paths 数量必须完全一致")
    if len(files) > settings.batch_max_files:
        raise BatchValidationError(
            f"单批最多允许 {settings.batch_max_files} 个文件"
        )

    validated: list[ValidatedUploadPath] = []
    expected_mode: str | None = None
    expected_root: str | None = None
    seen_paths: set[str] = set()

    for upload, raw_path in zip(files, relative_paths, strict=True):
        if not raw_path or "\x00" in raw_path:
            raise BatchValidationError("相对路径为空或包含非法字符")
        normalized = _normalized_name(raw_path.replace("\\", "/").strip())
        if (
            PurePosixPath(normalized).is_absolute()
            or PureWindowsPath(normalized).is_absolute()
            or PureWindowsPath(normalized).drive
            or normalized.startswith("//")
        ):
            raise BatchValidationError(f"不允许绝对路径：{raw_path}")

        parts = normalized.split("/")
        if any(not part or part in {".", ".."} or ":" in part for part in parts):
            raise BatchValidationError(f"路径包含非法层级：{raw_path}")

        if parts[0] in SOURCE_FOLDER_NAMES:
            mode = "direct"
            opaque_root = None
            source_index = 0
        elif len(parts) >= 3 and parts[1] in SOURCE_FOLDER_NAMES:
            mode = "rooted"
            opaque_root = parts[0]
            source_index = 1
        else:
            raise BatchValidationError(
                f"路径必须位于发票、补票或支付凭证目录下：{raw_path}"
            )
        if len(parts) <= source_index + 1:
            raise BatchValidationError(f"来源目录下缺少文件名：{raw_path}")

        if expected_mode is None:
            expected_mode = mode
            expected_root = opaque_root
        elif mode != expected_mode or opaque_root != expected_root:
            raise BatchValidationError("同一批次的路径必须使用一致的根目录模式")

        file_name = parts[-1]
        if len(file_name) > 255:
            raise BatchValidationError(f"文件名超过 255 个字符：{file_name[:40]}...")
        upload_name = _normalized_name(
            (upload.filename or "").replace("\\", "/").split("/")[-1]
        )
        if not upload_name or upload_name != file_name:
            raise BatchValidationError(
                f"文件名与相对路径不匹配：{upload.filename or 'unknown'}"
            )
        if "." not in file_name:
            raise BatchValidationError(f"文件缺少扩展名：{file_name}")
        file_type = file_name.rsplit(".", 1)[1].lower()
        if file_type not in settings.batch_allowed_extensions:
            raise BatchValidationError(f"不支持的文件类型：{file_type}")

        stored_parts = parts[source_index:]
        stored_path = "/".join(stored_parts)
        if len(stored_path) > 1000:
            raise BatchValidationError("相对路径超过 1000 个字符")
        path_key = stored_path.casefold()
        if path_key in seen_paths:
            raise BatchValidationError(f"批次内存在重复路径：{stored_path}")
        seen_paths.add(path_key)
        validated.append(
            ValidatedUploadPath(
                source_folder=SOURCE_FOLDER_NAMES[parts[source_index]],
                relative_path=stored_path,
                file_name=file_name,
                file_type=file_type,
                mode=mode,
                opaque_root=opaque_root,
            )
        )
    return validated


def _special_document_values(
    path: ValidatedUploadPath,
    content: bytes | None,
) -> dict:
    settings = get_settings()
    if path.file_type in VIDEO_EXTENSIONS:
        return {
            "document_type": DocumentType.VIDEO,
            "processing_status": ProcessingStatus.NEEDS_MANUAL_REVIEW,
            "status_reason": "UNSUPPORTED_VIDEO",
            "status_detail": "视频文件暂不处理，需要人工处理",
        }
    if _normalized_name(path.file_name) == "PDF合并.pdf":
        return {
            "document_type": DocumentType.UNKNOWN,
            "processing_status": ProcessingStatus.SKIPPED,
            "status_reason": "DUPLICATE_SUMMARY_PDF",
            "status_detail": "重复汇总 PDF，已跳过",
        }
    if path.file_type in IMAGE_EXTENSIONS and content is not None:
        try:
            with Image.open(BytesIO(content)) as image:
                pixels = image.width * image.height
            if pixels > settings.batch_image_max_pixels:
                return {
                    "document_type": DocumentType.UNKNOWN,
                    "processing_status": ProcessingStatus.NEEDS_MANUAL_REVIEW,
                    "status_reason": "IMAGE_PIXEL_LIMIT_EXCEEDED",
                    "status_detail": (
                        f"图片像素数 {pixels} 超过限制 "
                        f"{settings.batch_image_max_pixels}"
                    ),
                }
        except (UnidentifiedImageError, OSError) as exc:
            raise BatchValidationError(
                f"无法读取图片文件头：{path.relative_path}"
            ) from exc
    return {
        "document_type": DocumentType.UNKNOWN,
        "processing_status": ProcessingStatus.UPLOADED,
        "status_reason": None,
        "status_detail": None,
    }


async def _read_upload(
    upload: UploadFile,
    *,
    keep_content: bool,
    total_before: int,
) -> tuple[bytes | None, int, str]:
    settings = get_settings()
    file_size = 0
    digest = hashlib.sha256()
    content = bytearray() if keep_content else None

    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        file_size += len(chunk)
        if file_size > settings.batch_max_file_size:
            raise BatchValidationError(
                f"文件 {upload.filename or 'unknown'} 超过 "
                f"{settings.batch_max_file_size // 1024 // 1024} MiB"
            )
        if total_before + file_size > settings.batch_max_total_size:
            raise BatchValidationError(
                f"批次总大小超过 {settings.batch_max_total_size // 1024 // 1024} MiB"
            )
        digest.update(chunk)
        if content is not None:
            content.extend(chunk)
    if file_size == 0:
        raise BatchValidationError(f"不允许空文件：{upload.filename or 'unknown'}")
    return bytes(content) if content is not None else None, file_size, digest.hexdigest()


async def register_batch(
    db: AsyncSession,
    *,
    employee_name: str,
    expense_month: date,
    files: list[UploadFile],
    relative_paths: list[str],
) -> ProcessingBatch:
    paths = validate_upload_paths(files, relative_paths)
    batch = ProcessingBatch(
        employee_name=employee_name.strip(),
        expense_month=expense_month,
        status=BatchStatus.UPLOADED,
    )
    db.add(batch)
    await db.flush()

    total_size = 0
    try:
        for upload, path in zip(files, paths, strict=True):
            keep_content = path.file_type not in VIDEO_EXTENSIONS
            content, file_size, file_hash = await _read_upload(
                upload,
                keep_content=keep_content,
                total_before=total_size,
            )
            total_size += file_size
            special = _special_document_values(path, content)
            document = SourceDocument(
                batch_id=batch.id,
                source_folder=path.source_folder,
                relative_path=path.relative_path,
                file_name=path.file_name,
                file_type=path.file_type,
                file_size=file_size,
                file_hash=file_hash,
                file_data=content,
                **special,
            )
            db.add(document)
            await db.flush()
            db.expunge(document)

        await refresh_batch_stats(db, batch.id)
        await db.commit()
        await db.refresh(batch)
        return batch
    except Exception:
        await db.rollback()
        raise
    finally:
        for upload in files:
            await upload.close()


async def refresh_batch_stats(
    db: AsyncSession,
    batch_id: int,
    *,
    complete_when_terminal: bool = True,
) -> ProcessingBatch | None:
    result = await db.execute(
        select(ProcessingBatch)
        .where(ProcessingBatch.id == batch_id)
        .with_for_update()
    )
    batch = result.scalar_one_or_none()
    if not batch:
        return None

    status_result = await db.execute(
        select(SourceDocument.processing_status).where(
            SourceDocument.batch_id == batch_id
        )
    )
    counts = Counter(status_result.scalars().all())
    batch.total_files = sum(counts.values())
    batch.recognized_files = counts[ProcessingStatus.RECOGNIZED]
    batch.manual_review_files = counts[ProcessingStatus.NEEDS_MANUAL_REVIEW]
    batch.skipped_files = counts[ProcessingStatus.SKIPPED]
    batch.failed_files = counts[ProcessingStatus.FAILED]
    active = counts[ProcessingStatus.UPLOADED] + counts[ProcessingStatus.PROCESSING]
    if complete_when_terminal and active == 0 and batch.status != BatchStatus.FAILED:
        batch.status = BatchStatus.COMPLETED
        batch.status_reason = None
    return batch


async def claim_document(db: AsyncSession, document_id: int) -> bool:
    result = await db.execute(
        update(SourceDocument)
        .where(
            SourceDocument.id == document_id,
            SourceDocument.processing_status == ProcessingStatus.UPLOADED,
        )
        .values(
            processing_status=ProcessingStatus.PROCESSING,
            processing_started_at=datetime.utcnow(),
            processing_attempts=SourceDocument.processing_attempts + 1,
            status_reason=None,
            status_detail=None,
        )
    )
    return bool(result.rowcount)


async def _mark_document_failed(document_id: int, detail: str) -> None:
    async with async_session_maker() as db:
        await db.execute(
            update(SourceDocument)
            .where(
                SourceDocument.id == document_id,
                SourceDocument.processing_status == ProcessingStatus.PROCESSING,
            )
            .values(
                processing_status=ProcessingStatus.FAILED,
                status_reason="PROCESSING_ERROR",
                status_detail=detail[:500],
            )
        )
        batch_id = await db.scalar(
            select(SourceDocument.batch_id).where(SourceDocument.id == document_id)
        )
        if batch_id is not None:
            await refresh_batch_stats(db, batch_id)
        await db.commit()


async def process_document_id(document_id: int) -> bool:
    async with async_session_maker() as db:
        claimed = await claim_document(db, document_id)
        if not claimed:
            await db.rollback()
            return False
        batch_id = await db.scalar(
            select(SourceDocument.batch_id).where(SourceDocument.id == document_id)
        )
        if batch_id is not None:
            await db.execute(
                update(ProcessingBatch)
                .where(
                    ProcessingBatch.id == batch_id,
                    ProcessingBatch.status.in_(
                        [BatchStatus.UPLOADED, BatchStatus.PROCESSING]
                    ),
                )
                .values(status=BatchStatus.PROCESSING, status_reason=None)
            )
        await db.commit()

    try:
        async with async_session_maker() as db:
            result = await db.execute(
                select(SourceDocument).where(SourceDocument.id == document_id)
            )
            document = result.scalar_one_or_none()
            if not document or document.file_data is None:
                raise RuntimeError("可处理文档缺少文件数据")
            file_data = document.file_data
            file_type = document.file_type
            source_folder = document.source_folder

        from app.services.document_service import process_document_content

        processed = await asyncio.to_thread(
            process_document_content,
            file_data=file_data,
            file_type=file_type,
            source_folder=source_folder,
        )
        async with async_session_maker() as db:
            await db.execute(
                update(SourceDocument)
                .where(
                    SourceDocument.id == document_id,
                    SourceDocument.processing_status
                    == ProcessingStatus.PROCESSING,
                )
                .values(**processed.as_update_values())
            )
            await refresh_batch_stats(db, batch_id)
            await db.commit()
    except Exception as exc:
        safe_detail = f"文档处理异常：{exc.__class__.__name__}"
        await _mark_document_failed(document_id, safe_detail)
    return True


async def next_queued_document_id(db: AsyncSession) -> int | None:
    result = await db.execute(
        select(SourceDocument.id)
        .join(
            ProcessingBatch,
            SourceDocument.batch_id == ProcessingBatch.id,
        )
        .where(
            SourceDocument.processing_status == ProcessingStatus.UPLOADED,
            ProcessingBatch.status.in_(
                [BatchStatus.UPLOADED, BatchStatus.PROCESSING]
            )
        )
        .order_by(
            ProcessingBatch.created_at,
            ProcessingBatch.id,
            SourceDocument.id,
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def process_next_queued_document() -> bool:
    async with async_session_maker() as db:
        document_id = await next_queued_document_id(db)
    if document_id is None:
        return False
    await process_document_id(document_id)
    return True


async def prepare_reprocess(
    db: AsyncSession,
    batch_id: int,
    document_ids: list[int] | None,
) -> int:
    document_query = (
        select(SourceDocument)
        .where(
            SourceDocument.batch_id == batch_id,
            SourceDocument.processing_status.in_(
                [
                    ProcessingStatus.NEEDS_MANUAL_REVIEW,
                    ProcessingStatus.FAILED,
                ]
            ),
        )
    )
    if document_ids is not None:
        document_query = document_query.where(SourceDocument.id.in_(document_ids))
    result = await db.execute(
        document_query.order_by(SourceDocument.id).with_for_update()
    )
    documents = list(result.scalars().all())

    processable = [
        document
        for document in documents
        if document.status_reason not in SPECIAL_REASONS
        and document.document_type != DocumentType.VIDEO
        and document.file_data is not None
    ]
    for document in processable:
        document.processing_status = ProcessingStatus.UPLOADED
        document.status_reason = None
        document.status_detail = None
        document.raw_text = None
        document.classification_confidence = None
        document.extraction_confidence = None
        document.document_date = None
        document.counterparty = None
        document.description = None
        document.amount = None
        document.currency = None
        document.invoice_number = None
        document.transaction_number = None
        document.extracted_fields_json = None
        document.classification_raw_response = None
        document.extraction_raw_response = None
        document.model_provider = None
        document.model_name = None
        document.processing_started_at = None

    if processable:
        batch_result = await db.execute(
            select(ProcessingBatch).where(ProcessingBatch.id == batch_id)
        )
        batch = batch_result.scalar_one_or_none()
        if batch:
            batch.status = BatchStatus.UPLOADED
            batch.status_reason = None
        await refresh_batch_stats(db, batch_id, complete_when_terminal=False)
        await db.commit()
    return len(processable)


async def recover_interrupted_batches() -> int:
    async with async_session_maker() as db:
        result = await db.execute(
            select(SourceDocument.batch_id)
            .where(
                SourceDocument.processing_status == ProcessingStatus.PROCESSING
            )
            .distinct()
        )
        batch_ids = list(result.scalars().all())
        if not batch_ids:
            return 0

        await db.execute(
            update(SourceDocument)
            .where(
                SourceDocument.batch_id.in_(batch_ids),
                SourceDocument.processing_status == ProcessingStatus.PROCESSING,
            )
            .values(
                processing_status=ProcessingStatus.FAILED,
                status_reason="PROCESS_INTERRUPTED",
                status_detail="OCR worker 重启导致处理任务中断，可发起重新处理",
            )
        )
        batches_result = await db.execute(
            select(ProcessingBatch).where(ProcessingBatch.id.in_(batch_ids))
        )
        for batch in batches_result.scalars():
            batch.status = BatchStatus.FAILED
            batch.status_reason = "PROCESS_INTERRUPTED"
            await refresh_batch_stats(db, batch.id, complete_when_terminal=False)
        await db.commit()
        return len(batch_ids)
