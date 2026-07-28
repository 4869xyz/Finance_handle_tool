from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from httpx import AsyncClient

from app.database import Base, get_db
from app.main import app
from app.models.batch import (
    BatchStatus,
    ProcessingBatch,
    ProcessingStatus,
    SourceDocument,
)


async def test_batch_api_create_query_and_atomic_mismatch(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_db():
        async with maker() as session:
            yield session

    processing_calls = []

    def unexpected_processing_call(**kwargs):
        processing_calls.append(kwargs)
        raise AssertionError("批次创建和重新处理请求不得直接执行文档识别")

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(
        "app.services.document_service.process_document_content",
        unexpected_processing_call,
    )
    try:
        async with AsyncClient(app=app, base_url="http://test") as client:
            created = await client.post(
                "/api/batches",
                data={
                    "employee_name": "张三",
                    "expense_month": "2025-03",
                    "relative_paths": "月度目录/发票/ordinary.pdf",
                },
                files=[
                    (
                        "files",
                        ("ordinary.pdf", b"%PDF-ordinary", "application/pdf"),
                    )
                ],
            )
            assert created.status_code == 202
            assert created.headers["location"] == "/api/batches/1"
            assert created.json()["expense_month"] == "2025-03"
            assert created.json()["status"] == "UPLOADED"

            batches = await client.get("/api/batches")
            assert batches.status_code == 200
            assert batches.json()["total"] == 1

            documents = await client.get("/api/batches/1/documents")
            assert documents.status_code == 200
            assert documents.json()["items"][0]["processing_status"] == "UPLOADED"
            assert processing_calls == []

            async with maker() as db:
                batch = await db.get(ProcessingBatch, 1)
                document = await db.scalar(
                    select(SourceDocument).where(SourceDocument.batch_id == 1)
                )
                batch.status = BatchStatus.FAILED
                document.processing_status = ProcessingStatus.FAILED
                document.status_reason = "PROCESSING_ERROR"
                await db.commit()

            reprocessed = await client.post("/api/batches/1/reprocess")
            assert reprocessed.status_code == 202
            assert reprocessed.json()["scheduled_count"] == 1

            documents = await client.get("/api/batches/1/documents")
            assert documents.json()["items"][0]["processing_status"] == "UPLOADED"
            assert processing_calls == []

            mismatch = await client.post(
                "/api/batches",
                data={
                    "employee_name": "李四",
                    "expense_month": "2025-03",
                    "relative_paths": "发票/a.pdf",
                },
                files=[
                    ("files", ("a.pdf", b"a", "application/pdf")),
                    ("files", ("b.pdf", b"b", "application/pdf")),
                ],
            )
            assert mismatch.status_code == 400

        async with maker() as db:
            assert (
                await db.scalar(select(func.count()).select_from(ProcessingBatch))
                == 1
            )
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
