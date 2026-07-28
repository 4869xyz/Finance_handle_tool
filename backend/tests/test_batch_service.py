import os
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import UploadFile
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base
from app.models.batch import (
    BatchStatus,
    DocumentType,
    ProcessingBatch,
    ProcessingStatus,
    SourceDocument,
    SourceFolder,
)
from app.services import batch_service
from app.services.batch_service import (
    BatchValidationError,
    claim_document,
    prepare_reprocess,
    recover_interrupted_batches,
    refresh_batch_stats,
    register_batch,
)
from app.services.document_service import DocumentProcessingResult


@pytest.fixture
async def session_maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


def upload(name: str, content: bytes) -> UploadFile:
    return UploadFile(file=BytesIO(content), filename=name)


def tiny_png(width=3, height=2) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), "white").save(output, format="PNG")
    return output.getvalue()


@pytest.mark.asyncio
async def test_special_files_short_circuit_and_video_binary_is_not_stored(
    session_maker,
    monkeypatch,
):
    monkeypatch.setattr(
        batch_service.get_settings(),
        "batch_image_max_pixels",
        4,
    )
    async with session_maker() as db:
        batch = await register_batch(
            db,
            employee_name="测试员工",
            expense_month=date(2025, 3, 1),
            files=[
                upload("PDF合并.pdf", b"%PDF-summary"),
                upload("证明.mp4", b"video-data"),
                upload("长图.png", tiny_png()),
            ],
            relative_paths=[
                "发票/PDF合并.pdf",
                "支付凭证/证明.mp4",
                "支付凭证/长图.png",
            ],
        )
        assert batch.total_files == 3
        assert batch.skipped_files == 1
        assert batch.manual_review_files == 2

        result = await db.execute(
            select(SourceDocument).order_by(SourceDocument.id)
        )
        documents = list(result.scalars())
        assert documents[0].status_reason == "DUPLICATE_SUMMARY_PDF"
        assert documents[1].document_type == DocumentType.VIDEO
        assert documents[1].file_data is None
        assert documents[1].file_hash
        assert documents[2].status_reason == "IMAGE_PIXEL_LIMIT_EXCEEDED"
        assert documents[2].file_data is not None


@pytest.mark.asyncio
async def test_upload_failure_rolls_back_the_whole_batch(session_maker, monkeypatch):
    monkeypatch.setattr(batch_service.get_settings(), "batch_max_file_size", 5)
    async with session_maker() as db:
        with pytest.raises(BatchValidationError, match="超过"):
            await register_batch(
                db,
                employee_name="测试员工",
                expense_month=date(2025, 3, 1),
                files=[
                    upload("PDF合并.pdf", b"x"),
                    upload("too-large.pdf", b"123456"),
                ],
                relative_paths=[
                    "发票/PDF合并.pdf",
                    "发票/too-large.pdf",
                ],
            )
        assert await db.scalar(select(func.count()).select_from(ProcessingBatch)) == 0
        assert await db.scalar(select(func.count()).select_from(SourceDocument)) == 0


@pytest.mark.asyncio
async def test_stats_claim_and_reprocess_preserve_business_invariants(session_maker):
    async with session_maker() as db:
        batch = ProcessingBatch(
            employee_name="测试员工",
            expense_month=date(2025, 3, 1),
            status=BatchStatus.PROCESSING,
        )
        db.add(batch)
        await db.flush()
        ordinary = SourceDocument(
            batch_id=batch.id,
            source_folder=SourceFolder.INVOICE,
            relative_path="发票/a.pdf",
            file_name="a.pdf",
            file_type="pdf",
            file_size=1,
            file_hash="a" * 64,
            file_data=b"x",
            document_type=DocumentType.ELECTRONIC_INVOICE,
            processing_status=ProcessingStatus.FAILED,
            status_reason="PROCESSING_ERROR",
        )
        special = SourceDocument(
            batch_id=batch.id,
            source_folder=SourceFolder.PAYMENT_EVIDENCE,
            relative_path="支付凭证/a.mp4",
            file_name="a.mp4",
            file_type="mp4",
            file_size=1,
            file_hash="b" * 64,
            file_data=None,
            document_type=DocumentType.VIDEO,
            processing_status=ProcessingStatus.NEEDS_MANUAL_REVIEW,
            status_reason="UNSUPPORTED_VIDEO",
        )
        db.add_all([ordinary, special])
        await db.commit()

        scheduled = await prepare_reprocess(db, batch.id, None)
        assert scheduled == 1
        assert ordinary.processing_status == ProcessingStatus.UPLOADED
        assert special.processing_status == ProcessingStatus.NEEDS_MANUAL_REVIEW

        assert await claim_document(db, ordinary.id) is True
        assert await claim_document(db, ordinary.id) is False
        await db.commit()
        await db.refresh(ordinary)
        assert ordinary.processing_attempts == 1

        ordinary.processing_status = ProcessingStatus.RECOGNIZED
        await refresh_batch_stats(db, batch.id)
        await db.commit()
        await db.refresh(batch)
        assert batch.status == BatchStatus.COMPLETED
        assert batch.recognized_files == 1
        assert batch.manual_review_files == 1


@pytest.mark.asyncio
async def test_startup_recovery_marks_only_processable_work_interrupted(
    session_maker,
    monkeypatch,
):
    monkeypatch.setattr(batch_service, "async_session_maker", session_maker)
    async with session_maker() as db:
        batch = ProcessingBatch(
            employee_name="测试员工",
            expense_month=date(2025, 3, 1),
            status=BatchStatus.PROCESSING,
        )
        db.add(batch)
        await db.flush()
        db.add_all(
            [
                SourceDocument(
                    batch_id=batch.id,
                    source_folder=SourceFolder.INVOICE,
                    relative_path="发票/a.pdf",
                    file_name="a.pdf",
                    file_type="pdf",
                    file_size=1,
                    file_hash="a" * 64,
                    file_data=b"x",
                    document_type=DocumentType.UNKNOWN,
                    processing_status=ProcessingStatus.PROCESSING,
                ),
                SourceDocument(
                    batch_id=batch.id,
                    source_folder=SourceFolder.INVOICE,
                    relative_path="发票/b.pdf",
                    file_name="b.pdf",
                    file_type="pdf",
                    file_size=1,
                    file_hash="c" * 64,
                    file_data=b"y",
                    document_type=DocumentType.UNKNOWN,
                    processing_status=ProcessingStatus.UPLOADED,
                ),
                SourceDocument(
                    batch_id=batch.id,
                    source_folder=SourceFolder.PAYMENT_EVIDENCE,
                    relative_path="支付凭证/a.mp4",
                    file_name="a.mp4",
                    file_type="mp4",
                    file_size=1,
                    file_hash="b" * 64,
                    file_data=None,
                    document_type=DocumentType.VIDEO,
                    processing_status=ProcessingStatus.NEEDS_MANUAL_REVIEW,
                    status_reason="UNSUPPORTED_VIDEO",
                ),
            ]
        )
        await db.commit()
        batch_id = batch.id

    assert await recover_interrupted_batches() == 1
    async with session_maker() as db:
        batch = await db.get(ProcessingBatch, batch_id)
        result = await db.execute(
            select(SourceDocument)
            .where(SourceDocument.batch_id == batch_id)
            .order_by(SourceDocument.id)
        )
        documents = list(result.scalars())
        assert batch.status == BatchStatus.FAILED
        assert batch.failed_files == 1
        assert batch.manual_review_files == 1
        assert documents[0].status_reason == "PROCESS_INTERRUPTED"
        assert documents[0].processing_status == ProcessingStatus.FAILED
        assert documents[1].processing_status == ProcessingStatus.UPLOADED
        assert documents[1].status_reason is None
        assert documents[2].status_reason == "UNSUPPORTED_VIDEO"


@pytest.mark.asyncio
async def test_worker_queue_never_calls_recognition_for_special_files(
    session_maker,
    monkeypatch,
):
    monkeypatch.setattr(batch_service, "async_session_maker", session_maker)
    calls = []

    def fake_process_document_content(**kwargs):
        calls.append(kwargs["file_data"])
        return DocumentProcessingResult(
            document_type=DocumentType.OTHER_DOCUMENT,
            processing_status=ProcessingStatus.RECOGNIZED,
        )

    monkeypatch.setattr(
        "app.services.document_service.process_document_content",
        fake_process_document_content,
    )
    async with session_maker() as db:
        batch = ProcessingBatch(
            employee_name="短路测试",
            expense_month=date(2025, 3, 1),
            status=BatchStatus.UPLOADED,
        )
        db.add(batch)
        await db.flush()
        common = {
            "batch_id": batch.id,
            "source_folder": SourceFolder.PAYMENT_EVIDENCE,
            "file_size": 1,
        }
        db.add_all(
            [
                SourceDocument(
                    **common,
                    relative_path="支付凭证/video.mp4",
                    file_name="video.mp4",
                    file_type="mp4",
                    file_hash="1" * 64,
                    file_data=None,
                    document_type=DocumentType.VIDEO,
                    processing_status=ProcessingStatus.NEEDS_MANUAL_REVIEW,
                    status_reason="UNSUPPORTED_VIDEO",
                ),
                SourceDocument(
                    **common,
                    relative_path="支付凭证/PDF合并.pdf",
                    file_name="PDF合并.pdf",
                    file_type="pdf",
                    file_hash="2" * 64,
                    file_data=b"s",
                    document_type=DocumentType.UNKNOWN,
                    processing_status=ProcessingStatus.SKIPPED,
                    status_reason="DUPLICATE_SUMMARY_PDF",
                ),
                SourceDocument(
                    **common,
                    relative_path="支付凭证/long.png",
                    file_name="long.png",
                    file_type="png",
                    file_hash="3" * 64,
                    file_data=b"l",
                    document_type=DocumentType.UNKNOWN,
                    processing_status=ProcessingStatus.NEEDS_MANUAL_REVIEW,
                    status_reason="IMAGE_PIXEL_LIMIT_EXCEEDED",
                ),
                SourceDocument(
                    **common,
                    relative_path="支付凭证/ordinary.pdf",
                    file_name="ordinary.pdf",
                    file_type="pdf",
                    file_hash="4" * 64,
                    file_data=b"ordinary",
                    document_type=DocumentType.UNKNOWN,
                    processing_status=ProcessingStatus.UPLOADED,
                ),
            ]
        )
        await db.commit()
        batch_id = batch.id

    assert await batch_service.process_next_queued_document() is True
    assert await batch_service.process_next_queued_document() is False
    assert calls == [b"ordinary"]
    async with session_maker() as db:
        batch = await db.get(ProcessingBatch, batch_id)
        assert batch.status == BatchStatus.COMPLETED
        assert batch.recognized_files == 1
        assert batch.manual_review_files == 2
        assert batch.skipped_files == 1


@pytest.mark.asyncio
async def test_queue_is_fifo_and_skips_uploaded_work_in_failed_batches(
    session_maker,
):
    base_time = datetime(2025, 3, 1, 8, 0, 0)

    def queued_document(batch_id: int, suffix: str) -> SourceDocument:
        return SourceDocument(
            batch_id=batch_id,
            source_folder=SourceFolder.INVOICE,
            relative_path=f"发票/{suffix}.pdf",
            file_name=f"{suffix}.pdf",
            file_type="pdf",
            file_size=1,
            file_hash=(suffix[0] * 64)[:64],
            file_data=suffix.encode(),
            document_type=DocumentType.UNKNOWN,
            processing_status=ProcessingStatus.UPLOADED,
        )

    async with session_maker() as db:
        late_batch = ProcessingBatch(
            employee_name="晚批次",
            expense_month=date(2025, 3, 1),
            status=BatchStatus.UPLOADED,
            created_at=base_time + timedelta(hours=1),
        )
        early_batch_a = ProcessingBatch(
            employee_name="早批次 A",
            expense_month=date(2025, 3, 1),
            status=BatchStatus.UPLOADED,
            created_at=base_time,
        )
        early_batch_b = ProcessingBatch(
            employee_name="早批次 B",
            expense_month=date(2025, 3, 1),
            status=BatchStatus.UPLOADED,
            created_at=base_time,
        )
        failed_batch = ProcessingBatch(
            employee_name="失败批次",
            expense_month=date(2025, 3, 1),
            status=BatchStatus.FAILED,
            created_at=base_time - timedelta(hours=1),
        )
        db.add_all([late_batch, early_batch_a, early_batch_b, failed_batch])
        await db.flush()

        late_document = queued_document(late_batch.id, "late")
        early_a_first = queued_document(early_batch_a.id, "alpha")
        early_a_second = queued_document(early_batch_a.id, "beta")
        early_b_document = queued_document(early_batch_b.id, "charlie")
        failed_document = queued_document(failed_batch.id, "failed")
        db.add_all(
            [
                late_document,
                early_a_first,
                early_a_second,
                early_b_document,
                failed_document,
            ]
        )
        await db.commit()

        expected = [
            early_a_first.id,
            early_a_second.id,
            early_b_document.id,
            late_document.id,
        ]
        for expected_id in expected:
            assert await batch_service.next_queued_document_id(db) == expected_id
            assert await claim_document(db, expected_id) is True
            await db.commit()

        assert await batch_service.next_queued_document_id(db) is None
        await db.refresh(failed_document)
        assert failed_document.processing_status == ProcessingStatus.UPLOADED


@pytest.mark.asyncio
async def test_processing_exception_fails_only_one_document_and_queue_continues(
    session_maker,
    monkeypatch,
):
    monkeypatch.setattr(batch_service, "async_session_maker", session_maker)
    calls = []

    def fake_process_document_content(**kwargs):
        calls.append(kwargs["file_data"])
        if kwargs["file_data"] == b"bad":
            raise RuntimeError("expected test failure")
        return DocumentProcessingResult(
            document_type=DocumentType.OTHER_DOCUMENT,
            processing_status=ProcessingStatus.RECOGNIZED,
        )

    monkeypatch.setattr(
        "app.services.document_service.process_document_content",
        fake_process_document_content,
    )
    async with session_maker() as db:
        batch = ProcessingBatch(
            employee_name="异常隔离测试",
            expense_month=date(2025, 3, 1),
            status=BatchStatus.UPLOADED,
        )
        db.add(batch)
        await db.flush()
        db.add_all(
            [
                SourceDocument(
                    batch_id=batch.id,
                    source_folder=SourceFolder.INVOICE,
                    relative_path="发票/bad.pdf",
                    file_name="bad.pdf",
                    file_type="pdf",
                    file_size=3,
                    file_hash="d" * 64,
                    file_data=b"bad",
                    processing_status=ProcessingStatus.UPLOADED,
                ),
                SourceDocument(
                    batch_id=batch.id,
                    source_folder=SourceFolder.INVOICE,
                    relative_path="发票/good.pdf",
                    file_name="good.pdf",
                    file_type="pdf",
                    file_size=4,
                    file_hash="e" * 64,
                    file_data=b"good",
                    processing_status=ProcessingStatus.UPLOADED,
                ),
            ]
        )
        await db.commit()
        batch_id = batch.id

    assert await batch_service.process_next_queued_document() is True
    assert await batch_service.process_next_queued_document() is True
    assert await batch_service.process_next_queued_document() is False
    assert calls == [b"bad", b"good"]

    async with session_maker() as db:
        result = await db.execute(
            select(SourceDocument)
            .where(SourceDocument.batch_id == batch_id)
            .order_by(SourceDocument.id)
        )
        documents = list(result.scalars())
        assert documents[0].processing_status == ProcessingStatus.FAILED
        assert documents[0].status_reason == "PROCESSING_ERROR"
        assert documents[1].processing_status == ProcessingStatus.RECOGNIZED
        batch = await db.get(ProcessingBatch, batch_id)
        assert batch.status == BatchStatus.COMPLETED
        assert batch.failed_files == 1
        assert batch.recognized_files == 1


@pytest.mark.asyncio
async def test_actual_example_directory_registers_expected_91_documents(
    session_maker,
):
    project_root = Path(__file__).resolve().parents[2]
    sample_root = project_root / "example_file" / "马耿宾2025年3月报销发票"
    if not sample_root.exists():
        pytest.skip("实际示例目录不存在")

    file_paths = sorted(path for path in sample_root.rglob("*") if path.is_file())
    assert len(file_paths) == 91
    uploads = [
        UploadFile(file=path.open("rb"), filename=path.name)
        for path in file_paths
    ]
    relative_paths = [
        path.relative_to(sample_root.parent).as_posix()
        for path in file_paths
    ]
    async with session_maker() as db:
        batch = await register_batch(
            db,
            employee_name="马耿宾",
            expense_month=date(2025, 3, 1),
            files=uploads,
            relative_paths=relative_paths,
        )
        assert batch.total_files == 91
        assert batch.skipped_files == 3
        assert batch.manual_review_files == 2

        status_result = await db.execute(
            select(SourceDocument.processing_status).where(
                SourceDocument.batch_id == batch.id
            )
        )
        statuses = list(status_result.scalars())
        assert statuses.count(ProcessingStatus.UPLOADED) == 86
        assert statuses.count(ProcessingStatus.SKIPPED) == 3
        assert statuses.count(ProcessingStatus.NEEDS_MANUAL_REVIEW) == 2


@pytest.mark.postgres_integration
@pytest.mark.asyncio
async def test_postgres_concurrent_claim_when_configured():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL 未配置")
    engine = create_async_engine(database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with maker() as db:
        batch = ProcessingBatch(
            employee_name="并发领取测试",
            expense_month=date(2099, 1, 1),
        )
        db.add(batch)
        await db.flush()
        document = SourceDocument(
            batch_id=batch.id,
            source_folder=SourceFolder.INVOICE,
            relative_path=f"发票/postgres-claim-{batch.id}.pdf",
            file_name="postgres-claim.pdf",
            file_type="pdf",
            file_size=1,
            file_hash="c" * 64,
            file_data=b"x",
        )
        db.add(document)
        await db.commit()
        document_id = document.id

    async def claim_once():
        async with maker() as db:
            claimed = await claim_document(db, document_id)
            await db.commit()
            return claimed

    first, second = await __import__("asyncio").gather(claim_once(), claim_once())
    assert sorted([first, second]) == [False, True]
    async with maker() as db:
        batch = await db.get(ProcessingBatch, batch.id)
        await db.delete(batch)
        await db.commit()
    await engine.dispose()


@pytest.mark.postgres_integration
@pytest.mark.asyncio
async def test_postgres_concurrent_reprocess_schedules_document_once():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL 未配置")
    engine = create_async_engine(database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    batch_id = None
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with maker() as db:
            batch = ProcessingBatch(
                employee_name="并发重处理测试",
                expense_month=date(2099, 2, 1),
                status=BatchStatus.FAILED,
            )
            db.add(batch)
            await db.flush()
            document = SourceDocument(
                batch_id=batch.id,
                source_folder=SourceFolder.INVOICE,
                relative_path=f"发票/postgres-reprocess-{batch.id}.pdf",
                file_name="postgres-reprocess.pdf",
                file_type="pdf",
                file_size=1,
                file_hash="r" * 64,
                file_data=b"x",
                processing_status=ProcessingStatus.FAILED,
                status_reason="PROCESSING_ERROR",
            )
            db.add(document)
            await db.commit()
            batch_id = batch.id

        async def reprocess_once() -> int:
            async with maker() as db:
                return await prepare_reprocess(db, batch_id, None)

        first, second = await __import__("asyncio").gather(
            reprocess_once(),
            reprocess_once(),
        )
        assert sorted([first, second]) == [0, 1]
        async with maker() as db:
            document = await db.scalar(
                select(SourceDocument).where(SourceDocument.batch_id == batch_id)
            )
            assert document.processing_status == ProcessingStatus.UPLOADED
            assert document.processing_attempts == 0
    finally:
        if batch_id is not None:
            async with maker() as db:
                batch = await db.get(ProcessingBatch, batch_id)
                if batch is not None:
                    await db.delete(batch)
                    await db.commit()
        await engine.dispose()
