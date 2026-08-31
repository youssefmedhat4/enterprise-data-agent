"""Versioned SQL migrations for the internal knowledge database.

Deliberately small. The project already owns its PostgreSQL infrastructure and
needs ordered, once-only DDL with a recorded history — not a migration
framework's autogeneration, branching, or ORM coupling. Each file in
`infra/postgres/knowledge` is applied in filename order inside a transaction and
recorded in `knowledge.schema_migrations`.

Migrations run against the INTERNAL database. They must never be pointed at a
customer's analytics database, which this system only ever reads.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from psycopg import AsyncConnection

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "infra" / "postgres" / "knowledge"

_FILENAME = re.compile(r"^(?P<version>\d{3,})_(?P<name>[a-z0-9_]+)\.sql$")

_HISTORY_DDL = """
CREATE SCHEMA IF NOT EXISTS knowledge;
CREATE TABLE IF NOT EXISTS knowledge.schema_migrations (
    version     text PRIMARY KEY,
    name        text NOT NULL,
    checksum    text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
);
"""


class MigrationError(RuntimeError):
    """Raised when migrations cannot be applied safely."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: str
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def discover_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    if not directory.is_dir():
        raise MigrationError(f"Migration directory {directory} does not exist.")
    migrations: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        match = _FILENAME.match(path.name)
        if match is None:
            raise MigrationError(
                f"Migration {path.name!r} must be named <version>_<name>.sql, "
                "for example 001_semantic_registry.sql."
            )
        migrations.append(
            Migration(
                version=match["version"],
                name=match["name"],
                sql=path.read_text(encoding="utf-8"),
            )
        )
    versions = [migration.version for migration in migrations]
    duplicates = {version for version in versions if versions.count(version) > 1}
    if duplicates:
        raise MigrationError(f"Duplicate migration versions: {sorted(duplicates)}.")
    return migrations


async def apply_migrations(
    connection: AsyncConnection[object],
    *,
    directory: Path = MIGRATIONS_DIR,
) -> list[str]:
    """Apply pending migrations in order. Returns the versions applied."""
    migrations = discover_migrations(directory)
    async with connection.cursor() as cursor:
        await cursor.execute(_HISTORY_DDL)
        await cursor.execute(
            "SELECT version, checksum FROM knowledge.schema_migrations",
        )
        rows = cast(list[tuple[str, str]], await cursor.fetchall())
        applied = {str(version): str(checksum) for version, checksum in rows}

    newly_applied: list[str] = []
    for migration in migrations:
        recorded = applied.get(migration.version)
        if recorded is not None:
            if recorded != migration.checksum:
                raise MigrationError(
                    f"Migration {migration.version} was modified after it was "
                    "applied. Add a new migration instead of editing history."
                )
            continue
        async with connection.transaction(), connection.cursor() as cursor:
            await cursor.execute(cast(Any, migration.sql))
            await cursor.execute(
                "INSERT INTO knowledge.schema_migrations (version, name, checksum) "
                "VALUES (%s, %s, %s)",
                (migration.version, migration.name, migration.checksum),
            )
        # Version and name only: migration SQL is not logged.
        logger.info(
            "applied knowledge migration version=%s name=%s",
            migration.version,
            migration.name,
        )
        newly_applied.append(migration.version)
    return newly_applied
