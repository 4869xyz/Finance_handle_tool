from datetime import date
from io import BytesIO

from fastapi import UploadFile
from httpx import AsyncClient
from openpyxl import Workbook
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.main import app
from app.models.batch import ProcessingBatch
from app.services.package_ingest_service import register_package


def _png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (4, 4), "white").save(output, format="PNG")
    return output.getvalue()


def _workbook() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["时间", "申请人员", "支付主体", "费用类型", "事项", "金额", "专票/普票", "发票号码", "备注"])
    sheet.append([date(2025, 4, 1), "马耿宾", "德纳智谷", "交通费", "打车", 21.9, None, None, None])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _upload(name: str, content: bytes) -> UploadFile:
    return UploadFile(file=BytesIO(content), filename=name)


async def test_register_package_splits_months_and_stores_claims():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    png = _png()
    files = [
        _upload("马耿宾-报销总表.xlsx", _workbook()),
        _upload("a.png", png),
        _upload("b.png", png),
        _upload("报销使用事项.txt", b"ignore"),
    ]
    paths = [
        "马耿宾-报销/马耿宾-报销总表.xlsx",
        "马耿宾-报销/马耿宾2025年4月报销发票/发票/a.png",
        "马耿宾-报销/姓名-2026年1月-报销/支付凭证/b.png",
        "马耿宾-报销/报销使用事项.txt",
    ]
    async with maker() as db:
        try:
            await register_package(db, files=files, relative_paths=paths)
            assert False, "mixed employee names should fail"
        except Exception as exc:
            assert "员工姓名必须一致" in str(exc)

    files = [
        _upload("马耿宾-报销总表.xlsx", _workbook()),
        _upload("a.png", png),
        _upload("b.png", png),
    ]
    paths = [
        "马耿宾-报销/马耿宾-报销总表.xlsx",
        "马耿宾-报销/马耿宾2025年4月报销发票/发票/a.png",
        "马耿宾-报销/马耿宾2025年5月报销发票/支付凭证/b.png",
    ]
    async with maker() as db:
        package = await register_package(db, files=files, relative_paths=paths)
        assert package.employee_name == "马耿宾"
        assert len(package.parsed_claims) == 1
        result = await db.execute(
            select(ProcessingBatch).where(ProcessingBatch.package_id == package.id)
        )
        batches = result.scalars().all()
        assert len(batches) == 2
    await engine.dispose()


async def test_create_package_api_rejects_missing_workbook():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    png = _png()
    try:
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/api/packages",
                files=[("files", ("a.png", png, "image/png"))],
                data={"relative_paths": "马耿宾-报销/马耿宾2025年4月报销发票/发票/a.png"},
            )
        assert response.status_code == 400
        assert "报销总表" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
