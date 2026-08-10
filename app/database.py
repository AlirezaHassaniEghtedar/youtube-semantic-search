from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def ensure_columns() -> None:
    """Apply safe, additive SQLite schema updates for existing installations."""
    column_specs = {
        "channels": [
            ("last_synced_item_count", "INTEGER"),
            ("synced_all", "BOOLEAN DEFAULT 0"),
            ("youtube_channel_id", "TEXT"),
        ],
    }
    async with engine.begin() as conn:
        for table, columns in column_specs.items():
            result = await conn.execute(text(f"PRAGMA table_info({table})"))
            existing = {row[1] for row in result.fetchall()}
            for column_name, column_type in columns:
                if column_name not in existing:
                    await conn.execute(
                        text(f"ALTER TABLE {table} ADD COLUMN {column_name} {column_type}")
                    )
