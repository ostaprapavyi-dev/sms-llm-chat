"""Async engine and session lifecycle.

The rest of the application never imports the engine directly -- it depends on
:class:`~app.db.repository.ConversationRepository`, which is what keeps the storage
backend swappable (SQLite here, PostgreSQL by changing DATABASE_URL).
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.db.models import Base

logger = logging.getLogger(__name__)


def _ensure_sqlite_directory(url: str) -> None:
    """SQLite will not create missing parent directories for the database file."""
    if not url.startswith("sqlite"):
        return
    _, _, path = url.partition(":///")
    if not path or path == ":memory:":
        return
    parent = Path(path).expanduser().parent
    if parent and not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)


class Database:
    """Owns the engine and hands out sessions."""

    def __init__(self, url: str, *, echo: bool = False) -> None:
        _ensure_sqlite_directory(url)
        self.url = url
        self.engine: AsyncEngine = create_async_engine(url, echo=echo, future=True)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create_schema(self) -> None:
        """Create tables if they do not exist (Alembic would own this in production)."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("database schema ready", extra={"url": self.url})

    async def dispose(self) -> None:
        await self.engine.dispose()
