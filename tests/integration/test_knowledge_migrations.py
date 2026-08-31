"""Prove the knowledge migrations actually apply against real PostgreSQL.

Unit tests assert things about the migration *text*; only this exercises the
DDL, the pgvector extension, and the isolation constraints as the database
enforces them. Marked `postgres` so it skips when infrastructure is not running.
"""

from __future__ import annotations

import asyncio
import selectors
import sys
from typing import Any, cast
from uuid import uuid4

import psycopg
import pytest
from psycopg import errors

from app.config import Settings
from app.knowledge.migrations import MigrationError, apply_migrations


@pytest.mark.postgres
def test_migrations_apply_and_enforce_datasource_isolation() -> None:
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=_windows_selector_loop) as runner:
            runner.run(_exercise_migrations())
        return
    asyncio.run(_exercise_migrations())


def _windows_selector_loop() -> asyncio.AbstractEventLoop:
    return asyncio.SelectorEventLoop(selectors.SelectSelector())


async def _exercise_migrations() -> None:
    settings = Settings()
    if settings.checkpoint_database_url is None:
        pytest.skip("CHECKPOINT_DATABASE_URL is not configured.")
    dsn = settings.checkpoint_database_url.get_secret_value()

    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        # Start from a clean schema so the run is repeatable.
        async with conn.cursor() as cursor:
            await cursor.execute("DROP SCHEMA IF EXISTS knowledge CASCADE")

        applied = await apply_migrations(conn)
        assert "001" in applied

        # Re-running is a no-op, not an error.
        assert await apply_migrations(conn) == []

        await _assert_tables_exist(conn)
        await _assert_connection_ref_rejects_a_dsn(conn)
        await _assert_cross_datasource_reference_is_refused(conn)
        await _assert_embedding_dimension_must_match_vector(conn)

        async with conn.cursor() as cursor:
            await cursor.execute("DROP SCHEMA knowledge CASCADE")


async def _assert_tables_exist(conn: psycopg.AsyncConnection[object]) -> None:
    async with conn.cursor() as cursor:
        await cursor.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'knowledge' ORDER BY table_name"
        )
        fetched = cast(list[tuple[Any, ...]], await cursor.fetchall())
        names = {str(row[0]) for row in fetched}
    assert {
        "data_sources",
        "knowledge_embeddings",
        "schema_migrations",
        "semantic_attributes",
        "semantic_entities",
        "semantic_relationships",
    } <= names


async def _insert_data_source(
    conn: psycopg.AsyncConnection[object],
    name: str,
    connection_ref: str = "DATABASE_URL",
) -> str:
    async with conn.cursor() as cursor:
        await cursor.execute(
            "INSERT INTO knowledge.data_sources (name, database_type, connection_ref) "
            "VALUES (%s, 'postgres', %s) RETURNING id",
            (name, connection_ref),
        )
        row = cast(tuple[Any, ...] | None, await cursor.fetchone())
    assert row is not None
    return str(row[0])


async def _insert_entity(
    conn: psycopg.AsyncConnection[object],
    data_source_id: str,
    table: str,
) -> str:
    async with conn.cursor() as cursor:
        await cursor.execute(
            "INSERT INTO knowledge.semantic_entities "
            "(data_source_id, source_schema, source_table, entity_name) "
            "VALUES (%s, 'analytics', %s, 'Employee') RETURNING id",
            (data_source_id, table),
        )
        row = cast(tuple[Any, ...] | None, await cursor.fetchone())
    assert row is not None
    return str(row[0])


async def _assert_connection_ref_rejects_a_dsn(
    conn: psycopg.AsyncConnection[object],
) -> None:
    with pytest.raises(errors.CheckViolation):
        async with conn.transaction():
            await _insert_data_source(
                conn, "bad", "postgresql://user:secret@localhost/db"
            )


async def _assert_cross_datasource_reference_is_refused(
    conn: psycopg.AsyncConnection[object],
) -> None:
    """The composite FK must stop datasource B borrowing datasource A's entity."""
    source_a = await _insert_data_source(conn, "source-a")
    source_b = await _insert_data_source(conn, "source-b")
    entity_a = await _insert_entity(conn, source_a, "employees")

    # Same datasource: allowed.
    async with conn.cursor() as cursor:
        await cursor.execute(
            "INSERT INTO knowledge.semantic_attributes "
            "(data_source_id, entity_id, source_column, concept_name) "
            "VALUES (%s, %s, 'salary', 'Annual Base Salary')",
            (source_a, entity_a),
        )

    # Datasource B pointing at datasource A's entity: refused by the database.
    # A column name the entity does not already map, so the composite foreign
    # key is what rejects this rather than UNIQUE (entity_id, source_column).
    with pytest.raises(errors.ForeignKeyViolation):
        async with conn.transaction(), conn.cursor() as cursor:
            await cursor.execute(
                "INSERT INTO knowledge.semantic_attributes "
                "(data_source_id, entity_id, source_column, concept_name) "
                "VALUES (%s, %s, 'bonus', 'Leaked Concept')",
                (source_b, entity_a),
            )


async def _assert_embedding_dimension_must_match_vector(
    conn: psycopg.AsyncConnection[object],
) -> None:
    source = await _insert_data_source(conn, "source-embeddings")
    vector = "[" + ",".join("0.1" for _ in range(4)) + "]"

    async with conn.cursor() as cursor:
        await cursor.execute(
            "INSERT INTO knowledge.knowledge_embeddings "
            "(data_source_id, document_kind, document_id, content, "
            " embedding_provider, embedding_model, embedding_dimension, embedding) "
            "VALUES (%s, 'metric', %s, 'annual base payroll', "
            "        'fake', 'fake-embedding', 4, %s::vector)",
            (source, str(uuid4()), vector),
        )

    # Declaring a dimension the vector does not have must fail.
    with pytest.raises(errors.CheckViolation):
        async with conn.transaction(), conn.cursor() as cursor:
            await cursor.execute(
                "INSERT INTO knowledge.knowledge_embeddings "
                "(data_source_id, document_kind, document_id, content, "
                " embedding_provider, embedding_model, embedding_dimension, embedding) "
                "VALUES (%s, 'metric', %s, 'mismatched', "
                "        'fake', 'fake-embedding', 768, %s::vector)",
                (source, str(uuid4()), vector),
            )


@pytest.mark.postgres
def test_editing_an_applied_migration_is_refused() -> None:
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=_windows_selector_loop) as runner:
            runner.run(_exercise_checksum_guard())
        return
    asyncio.run(_exercise_checksum_guard())


async def _exercise_checksum_guard() -> None:
    settings = Settings()
    if settings.checkpoint_database_url is None:
        pytest.skip("CHECKPOINT_DATABASE_URL is not configured.")
    dsn = settings.checkpoint_database_url.get_secret_value()

    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("DROP SCHEMA IF EXISTS knowledge CASCADE")
        await apply_migrations(conn)

        async with conn.cursor() as cursor:
            await cursor.execute(
                "UPDATE knowledge.schema_migrations SET checksum = 'tampered' "
                "WHERE version = '001'"
            )

        with pytest.raises(MigrationError, match="modified after it was applied"):
            await apply_migrations(conn)

        async with conn.cursor() as cursor:
            await cursor.execute("DROP SCHEMA knowledge CASCADE")
