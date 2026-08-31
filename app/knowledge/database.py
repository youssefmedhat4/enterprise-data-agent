"""Connection pool for the internal knowledge database.

One pool per process, opened at startup and closed at shutdown. Graph nodes and
stores receive it; none of them open connections of their own, so connection
count stays bounded and a slow query cannot exhaust the process by accident.

There is deliberately no in-memory fallback here. When the knowledge database is
configured and cannot be reached, construction fails loudly. A silent fallback
would be worse than an outage: learning state would diverge per worker, an
approved metric would vanish on restart, and a reviewer's decision would appear
to have been recorded when it was not.
"""

from __future__ import annotations

import logging
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import Settings
from app.knowledge.migrations import apply_migrations

logger = logging.getLogger(__name__)


class KnowledgeDatabaseError(RuntimeError):
    """Raised when the knowledge database cannot be initialized."""


class KnowledgeDatabase:
    """Owns the pool and the schema for the internal knowledge database."""

    def __init__(
        self,
        *,
        connection_string: str,
        min_size: int,
        max_size: int,
        connect_timeout_seconds: float,
    ) -> None:
        self._pool: AsyncConnectionPool[Any] = AsyncConnectionPool(
            connection_string,
            min_size=min_size,
            max_size=max_size,
            open=False,
            timeout=connect_timeout_seconds,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
            },
        )
        self._connect_timeout_seconds = connect_timeout_seconds
        self._initialized = False

    @classmethod
    def from_settings(cls, settings: Settings) -> KnowledgeDatabase:
        if settings.checkpoint_database_url is None:
            raise KnowledgeDatabaseError(
                "CHECKPOINT_DATABASE_URL must be configured for knowledge storage."
            )
        return cls(
            connection_string=settings.checkpoint_database_url.get_secret_value(),
            min_size=settings.checkpoint_pool_min_size,
            max_size=settings.checkpoint_pool_max_size,
            connect_timeout_seconds=settings.checkpoint_connect_timeout_seconds,
        )

    @property
    def pool(self) -> AsyncConnectionPool[Any]:
        if not self._initialized:
            raise KnowledgeDatabaseError(
                "The knowledge database pool was used before initialization."
            )
        return self._pool

    async def initialize(self) -> None:
        """Open the pool and bring the schema up to date.

        Migrations run here rather than in a separate deploy step so a fresh
        environment is usable immediately. `apply_migrations` is idempotent and
        refuses an edited migration, so repeating this is safe.
        """
        if self._initialized:
            return
        try:
            await self._pool.open(wait=True, timeout=self._connect_timeout_seconds)
            async with self._pool.connection() as connection:
                applied = await apply_migrations(connection)
        except Exception as exc:
            await self._close_quietly()
            # Type only: a DSN can appear in psycopg's message text.
            raise KnowledgeDatabaseError(
                f"The knowledge database could not be initialized ({type(exc).__name__})."
            ) from exc
        if applied:
            logger.info("knowledge migrations applied: %d", len(applied))
        self._initialized = True

    async def close(self) -> None:
        if not self._initialized:
            await self._close_quietly()
            return
        self._initialized = False
        await self._close_quietly()

    async def _close_quietly(self) -> None:
        try:
            await self._pool.close()
        except Exception as exc:  # pragma: no cover - shutdown best effort
            logger.warning("knowledge pool close failed: %s", type(exc).__name__)
