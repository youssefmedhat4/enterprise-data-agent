"""An isolated knowledge database for integration tests.

These tests recreate the schema from scratch, which is the only honest way to
verify a migration. Doing that against the configured database destroys real
state: a developer who has onboarded a datasource and reviewed its semantics
loses all of it the next time the suite runs, silently and without a failure to
point at.

So the tests get their own database on the same server. It is created once,
reused, and never shared with the running application, which means a suite run
and a live session can coexist.
"""

from __future__ import annotations

import contextlib
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

from app.config import Settings

#: Suffix appended to the configured database name. A separate database rather
#: than a separate schema, because every migration hard-codes `knowledge.`.
TEST_DATABASE_SUFFIX = "_pytest"


def configured_dsn() -> str:
    """The developer's real knowledge DSN, or skip."""
    settings = Settings()
    if settings.checkpoint_database_url is None:
        pytest.skip("CHECKPOINT_DATABASE_URL is not configured.")
    return settings.checkpoint_database_url.get_secret_value()


def test_dsn(source: str | None = None) -> str:
    """The DSN of the isolated test database."""
    parts = urlsplit(source or configured_dsn())
    name = parts.path.lstrip("/") or "postgres"
    return urlunsplit(parts._replace(path=f"/{name}{TEST_DATABASE_SUFFIX}"))


async def ensure_test_database() -> str:
    """Create the isolated database if it does not exist, and return its DSN.

    Connects to the configured database only to issue CREATE DATABASE; nothing
    in it is read or modified.
    """
    source = configured_dsn()
    target = test_dsn(source)
    name = urlsplit(target).path.lstrip("/")

    async with await psycopg.AsyncConnection.connect(
        source, autocommit=True
    ) as connection, connection.cursor() as cursor:
        await cursor.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (name,)
        )
        if await cursor.fetchone() is None:
            # Identifier cannot be parameterised; the name is derived from
            # configuration plus a fixed suffix, never from user input.
            with contextlib.suppress(psycopg.errors.DuplicateDatabase):
                await cursor.execute(f'CREATE DATABASE "{name}"')
    return target
