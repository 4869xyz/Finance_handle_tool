from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()

engine = create_async_engine(settings.database_url, echo=settings.debug)

async_session_maker = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


class Base(DeclarativeBase):
    pass


SOURCE_DOCUMENT_DISPLAY_COLUMNS = (
    ("item_name", "VARCHAR(1000)"),
    ("order_number", "VARCHAR(150)"),
    ("trip_origin", "VARCHAR(1000)"),
    ("trip_destination", "VARCHAR(1000)"),
    ("trip_time_text", "VARCHAR(1000)"),
)


async def ensure_source_document_display_columns(connection) -> None:
    """Add nullable display columns on existing Postgres databases."""
    for column_name, column_type in SOURCE_DOCUMENT_DISPLAY_COLUMNS:
        await connection.execute(
            text(
                f"ALTER TABLE source_documents "
                f"ADD COLUMN IF NOT EXISTS {column_name} {column_type}"
            )
        )


async def ensure_processing_batch_package_id(connection) -> None:
    """Add nullable package_id on existing processing_batches tables."""
    await connection.execute(
        text(
            "ALTER TABLE processing_batches "
            "ADD COLUMN IF NOT EXISTS package_id INTEGER"
        )
    )


async def get_db():
    async with async_session_maker() as session:
        try:
            yield session
        finally:
            await session.close()
