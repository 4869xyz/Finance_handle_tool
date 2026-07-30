from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from httpx import AsyncClient

from app.database import Base, get_db
from app.main import app


async def test_company_profile_versions_are_append_only():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        async with AsyncClient(app=app, base_url="http://test") as client:
            empty = await client.get("/api/settings/company-profile")
            assert empty.status_code == 200
            assert empty.json() is None

            first = await client.put(
                "/api/settings/company-profile",
                json={
                    "identities": [
                        {
                            "company_name": " 示例科技有限公司 ",
                            "tax_id": " 9130 0000 0000 0000 01 ",
                        },
                        {
                            "company_name": "示例文化有限公司",
                            "tax_id": "913000000000000002",
                        },
                    ],
                },
            )
            assert first.status_code == 200
            assert first.json()["version"] == 1
            assert first.json()["identities"] == [
                {
                    "company_name": "示例科技有限公司",
                    "tax_id": "913000000000000001",
                },
                {
                    "company_name": "示例文化有限公司",
                    "tax_id": "913000000000000002",
                },
            ]

            second = await client.put(
                "/api/settings/company-profile",
                json={
                    "identities": [{
                        "company_name": "示例科技股份有限公司",
                        "tax_id": "913000000000000003",
                    }],
                },
            )
            assert second.status_code == 200
            assert second.json()["version"] == 2

            current = await client.get("/api/settings/company-profile")
            assert current.json()["version"] == 2
            assert current.json()["identities"] == [{
                "company_name": "示例科技股份有限公司",
                "tax_id": "913000000000000003",
            }]

            duplicate_name = await client.put(
                "/api/settings/company-profile",
                json={
                    "identities": [
                        {"company_name": "示例科技有限公司", "tax_id": "913000000000000004"},
                        {"company_name": " 示例科技有限公司 ", "tax_id": "913000000000000005"},
                    ],
                },
            )
            assert duplicate_name.status_code == 422
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
