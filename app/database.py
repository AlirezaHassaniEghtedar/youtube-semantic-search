from collections.abc import AsyncGenerator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True,
)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


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
        "sync_jobs": [
            ("videos_in_window", "INTEGER"),
        ],
        "videos": [
            ("live_status", "TEXT"),
            ("scheduled_start_at", "DATETIME"),
            ("video_type", "TEXT"),
            ("source_type", "TEXT DEFAULT 'youtube'"),
            ("local_file_path", "TEXT"),
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
    
    # Bootstrap new chat tables if they don't exist
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS chat_conversations (
                id TEXT PRIMARY KEY,
                title TEXT,
                channel_id TEXT,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE SET NULL
            )
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                sources TEXT,
                created_at DATETIME NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES chat_conversations(id) ON DELETE CASCADE
            )
        """))