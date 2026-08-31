"""Datasource registry and connection resolution.

A `connection_ref` is the *name* of a server-side secret, never the secret. It
is stored, listed, and shown to reviewers; the DSN it names is resolved only
inside the process, only when a scan actually needs it, and is never written
back into the knowledge tables, returned by an API, or logged.

Which references are permitted is server configuration, not user input. A
reviewer registering a datasource chooses from a configured allowlist, so the
API cannot be used to point the scanner at an arbitrary host or to probe which
environment variables exist.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import Settings
from app.knowledge.contracts import DataSource, DataSourceStatus

logger = logging.getLogger(__name__)


class DataSourceError(RuntimeError):
    """Raised when a datasource cannot be registered or resolved."""


class DataSourceConnectionResolver:
    """Turns a `connection_ref` into a DSN, inside the process only.

    Resolution is allowlisted. Without that, registering a datasource would let
    a reviewer name any environment variable and have its value used as a
    connection string, which turns an admin form into an environment reader.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def allowed_references(self) -> tuple[str, ...]:
        """Reference names a reviewer may choose from."""
        return self._settings.allowed_connection_refs

    def is_allowed(self, connection_ref: str) -> bool:
        return connection_ref in self.allowed_references()

    def resolve(self, connection_ref: str) -> str:
        """The DSN named by `connection_ref`.

        Raises rather than returning a partial result. Errors name the
        reference, never the value: the value is the credential.
        """
        if not self.is_allowed(connection_ref):
            raise DataSourceError(
                f"Connection reference {connection_ref!r} is not configured."
            )
        if connection_ref == "DATABASE_URL":
            url = self._settings.database_url
            if url is not None:
                return str(url)
        value = os.environ.get(connection_ref) or _from_dotenv(connection_ref)
        if value is None or not value.strip():
            raise DataSourceError(
                f"Connection reference {connection_ref!r} resolves to nothing."
            )
        return value


class PostgresDataSourceRegistry:
    """Registered datasources, stored without any credential."""

    def __init__(self, pool: AsyncConnectionPool[Any]) -> None:
        self._pool = pool

    async def register(
        self,
        *,
        name: str,
        database_type: str,
        connection_ref: str,
        allowed_schemas: tuple[str, ...] = ("analytics",),
        is_default: bool = False,
    ) -> DataSource:
        """Register a datasource.

        The contract's validator refuses anything resembling a DSN or inline
        credential, and the database CHECK constraint refuses it again, so a
        pasted connection string fails at both boundaries rather than being
        stored.
        """
        candidate = DataSource(
            id=uuid4(),
            name=name,
            database_type=database_type,
            connection_ref=connection_ref,
            allowed_schemas=allowed_schemas,
            status=DataSourceStatus.REGISTERED,
            is_default=is_default,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        async with (
            self._pool.connection() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(
                "INSERT INTO knowledge.data_sources"
                " (id, name, database_type, connection_ref, status, is_default,"
                "  allowed_schemas)"
                " VALUES (%(id)s, %(name)s, %(database_type)s, %(connection_ref)s,"
                "  %(status)s, %(is_default)s, %(allowed_schemas)s)"
                " RETURNING id, name, database_type, connection_ref, status,"
                " schema_fingerprint, is_default, allowed_schemas, created_at,"
                " updated_at, last_scanned_at",
                {
                    "id": candidate.id,
                    "name": candidate.name,
                    "database_type": candidate.database_type,
                    "connection_ref": candidate.connection_ref,
                    "status": candidate.status.value,
                    "is_default": candidate.is_default,
                    "allowed_schemas": list(candidate.allowed_schemas),
                },
            )
            row = cast("dict[str, Any] | None", await cursor.fetchone())
        if row is None:  # pragma: no cover - RETURNING always yields
            raise DataSourceError("Datasource registration returned no row.")
        logger.info("datasource registered: id=%s", row["id"])
        return _to_data_source(row)

    async def list(self) -> list[DataSource]:
        async with (
            self._pool.connection() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(
                "SELECT id, name, database_type, connection_ref, status,"
                " schema_fingerprint, is_default, allowed_schemas, created_at,"
                " updated_at, last_scanned_at FROM knowledge.data_sources"
                " ORDER BY is_default DESC, name"
            )
            rows = cast("list[dict[str, Any]]", await cursor.fetchall())
        return [_to_data_source(row) for row in rows]

    async def get(self, data_source_id: UUID) -> DataSource | None:
        async with (
            self._pool.connection() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(
                "SELECT id, name, database_type, connection_ref, status,"
                " schema_fingerprint, is_default, allowed_schemas, created_at,"
                " updated_at, last_scanned_at FROM knowledge.data_sources"
                " WHERE id = %(id)s",
                {"id": data_source_id},
            )
            row = cast("dict[str, Any] | None", await cursor.fetchone())
        return None if row is None else _to_data_source(row)

    async def ensure_default(
        self, data_source_id: UUID, *, name: str = "Company Analytics"
    ) -> DataSource:
        """Make sure the seeded demo datasource exists.

        Idempotent, so restarting does not duplicate it and does not overwrite a
        name a reviewer changed.
        """
        existing = await self.get(data_source_id)
        if existing is not None:
            return existing
        async with (
            self._pool.connection() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(
                "INSERT INTO knowledge.data_sources"
                " (id, name, database_type, connection_ref, status, is_default)"
                " VALUES (%(id)s, %(name)s, 'postgres', 'DATABASE_URL',"
                "  'READY', true)"
                " ON CONFLICT (id) DO NOTHING",
                {"id": data_source_id, "name": name},
            )
        stored = await self.get(data_source_id)
        if stored is None:  # pragma: no cover - just written
            raise DataSourceError("The default datasource could not be created.")
        return stored

    async def record_scan(
        self, data_source_id: UUID, *, schema_fingerprint: str
    ) -> None:
        async with (
            self._pool.connection() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(
                "UPDATE knowledge.data_sources"
                " SET schema_fingerprint = %(fingerprint)s,"
                "     status = 'READY', last_scanned_at = now(),"
                "     updated_at = now()"
                " WHERE id = %(id)s",
                {"id": data_source_id, "fingerprint": schema_fingerprint},
            )


def _from_dotenv(name: str) -> str | None:
    """Read one value from the same .env file Settings loads.

    Settings reads .env through pydantic-settings, which never touches
    `os.environ`. Without this a reference configured the way every other
    setting is configured would be listed as allowed and then fail to resolve,
    which is a confusing way to discover that two configuration sources exist.

    Deliberately narrow: it returns one named value and never enumerates the
    file, so it cannot be used to read configuration wholesale.
    """
    path = Path(
        Settings.model_config.get("env_file") or ".env"  # type: ignore[arg-type]
    )
    if not path.is_file():
        return None
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == name:
                return value.strip().strip("\"'")
    except OSError:
        # Configuration that cannot be read is the same as absent; the caller
        # raises a clear error naming the reference rather than the file.
        return None
    return None


def _to_data_source(row: dict[str, Any]) -> DataSource:
    return DataSource(
        id=row["id"],
        name=row["name"],
        database_type=row["database_type"],
        connection_ref=row["connection_ref"],
        status=DataSourceStatus(row["status"]),
        allowed_schemas=tuple(row.get("allowed_schemas") or ("analytics",)),
        schema_fingerprint=row["schema_fingerprint"],
        is_default=row["is_default"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_scanned_at=row["last_scanned_at"],
    )
