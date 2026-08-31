import re
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.config import Settings
from app.embeddings.factory import build_embedding_gateway
from app.embeddings.fake import FakeEmbeddingGateway
from app.embeddings.gateway import EmbeddingError, EmbeddingVector
from app.knowledge.contracts import ApprovalStatus, DataSource, DataSourceStatus
from app.knowledge.migrations import MigrationError, discover_migrations


def data_source(**overrides: object) -> DataSource:
    base: dict[str, object] = {
        "id": uuid4(),
        "name": "primary",
        "database_type": "postgres",
        "connection_ref": "DATABASE_URL",
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    return DataSource.model_validate(base | overrides)


# --------------------------------------------------------------------------
# Secrets never enter the registry
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "connection_ref",
    [
        "postgresql://user:secret@localhost:5432/analytics",
        "postgres://localhost/db",
        "host=localhost password=hunter2",
        "MY_PASSWORD_VAR",
    ],
)
def test_connection_ref_rejects_dsns_and_inline_credentials(connection_ref: str) -> None:
    with pytest.raises(ValueError, match="secret reference"):
        data_source(connection_ref=connection_ref)


def test_connection_ref_accepts_a_secret_reference() -> None:
    assert data_source(connection_ref="ANALYTICS_B_URL").connection_ref == "ANALYTICS_B_URL"


def test_data_source_serialization_carries_no_secret() -> None:
    dumped = data_source().model_dump_json()

    assert "DATABASE_URL" in dumped  # the reference is safe to show
    assert "://" not in dumped
    assert "password" not in dumped.lower()


# --------------------------------------------------------------------------
# Migrations
# --------------------------------------------------------------------------


def test_migrations_are_discovered_in_order_and_well_named() -> None:
    migrations = discover_migrations()

    assert migrations, "expected at least the semantic registry migration"
    assert [m.version for m in migrations] == sorted(m.version for m in migrations)
    assert migrations[0].version == "001"
    assert migrations[0].name == "semantic_registry"


def test_migration_checksum_changes_when_sql_changes() -> None:
    migrations = discover_migrations()
    first = migrations[0]

    assert first.checksum != type(first)(
        version=first.version, name=first.name, sql=first.sql + "\n-- edit"
    ).checksum


def test_missing_migration_directory_is_an_error(tmp_path: object) -> None:
    from pathlib import Path

    with pytest.raises(MigrationError, match="does not exist"):
        discover_migrations(Path(str(tmp_path)) / "absent")


def test_badly_named_migration_is_rejected(tmp_path: object) -> None:
    from pathlib import Path

    directory = Path(str(tmp_path))
    (directory / "add-stuff.sql").write_text("SELECT 1;", encoding="utf-8")

    with pytest.raises(MigrationError, match="must be named"):
        discover_migrations(directory)


def test_registry_migration_scopes_every_knowledge_table_by_datasource() -> None:
    """Isolation is a schema property; every knowledge table must carry the key."""
    sql = discover_migrations()[0].sql
    tables = set(re.findall(r"CREATE TABLE knowledge\.(\w+)", sql))

    assert tables == {
        "data_sources",
        "semantic_entities",
        "semantic_attributes",
        "semantic_relationships",
        "knowledge_embeddings",
    }
    for table in tables - {"data_sources"}:
        body = sql.split(f"CREATE TABLE knowledge.{table}")[1].split(");")[0]
        assert "data_source_id" in body, f"{table} is not datasource-scoped"


def test_child_rows_are_pinned_to_the_parent_datasource() -> None:
    """Composite FKs stop an attribute or join referencing another datasource."""
    sql = discover_migrations()[0].sql

    assert sql.count("REFERENCES knowledge.semantic_entities (id, data_source_id)") == 3
    assert "UNIQUE (id, data_source_id)" in sql


def test_embeddings_record_their_provenance_and_cannot_mix_dimensions() -> None:
    sql = discover_migrations()[0].sql
    body = sql.split("CREATE TABLE knowledge.knowledge_embeddings")[1]

    for column in ("embedding_provider", "embedding_model", "embedding_dimension"):
        assert column in body
    assert "vector_dims(embedding) = embedding_dimension" in body


# --------------------------------------------------------------------------
# Embeddings
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_fake_embeddings_are_deterministic_and_normalized() -> None:
    gateway = FakeEmbeddingGateway(dimension=768)

    first = await gateway.embed(["annual base payroll"])
    second = await gateway.embed(["annual base payroll"])

    assert first[0].values == second[0].values
    assert first[0].dimension == 768
    assert abs(sum(v * v for v in first[0].values) ** 0.5 - 1.0) < 1e-9


@pytest.mark.anyio
async def test_different_texts_get_different_vectors_and_order_is_preserved() -> None:
    gateway = FakeEmbeddingGateway(dimension=64)

    vectors = await gateway.embed(["payroll", "headcount", "payroll"])

    assert vectors[0].values == vectors[2].values
    assert vectors[0].values != vectors[1].values


def test_embedding_vector_rejects_a_dimension_mismatch() -> None:
    with pytest.raises(EmbeddingError, match="dimension 3"):
        EmbeddingVector(provider="fake", model="m", dimension=3, values=(0.1, 0.2))


def test_embeddings_from_different_models_refuse_comparison() -> None:
    left = EmbeddingVector(provider="fake", model="a", dimension=2, values=(1.0, 0.0))
    right = EmbeddingVector(provider="fake", model="b", dimension=2, values=(1.0, 0.0))

    left.assert_comparable(left)
    with pytest.raises(EmbeddingError, match="different models"):
        left.assert_comparable(right)


# --------------------------------------------------------------------------
# Cloud guard: embeddings must not be a loophole
# --------------------------------------------------------------------------


def test_cloud_embeddings_are_blocked_when_cloud_data_approval_is_off() -> None:
    settings = Settings(
        DATABASE_PROVIDER="postgres",
        LLM_PROVIDER="litellm",
        ALLOW_CLOUD_DATABASE_DATA=False,
        EMBEDDING_PROVIDER="gemini",
        GEMINI_API_KEY="test-key-not-real",
        VERTEXAI_PROJECT="test-project",
    )

    with pytest.raises(ValueError, match="ALLOW_CLOUD_DATABASE_DATA") as excinfo:
        build_embedding_gateway(settings)

    assert "test-key-not-real" not in str(excinfo.value)


def test_cloud_embeddings_require_an_api_key() -> None:
    settings = Settings(
        DATABASE_PROVIDER="postgres",
        LLM_PROVIDER="litellm",
        ALLOW_CLOUD_DATABASE_DATA=True,
        EMBEDDING_PROVIDER="gemini",
        VERTEXAI_PROJECT="test-project",
    )

    with pytest.raises(EmbeddingError, match="GEMINI_API_KEY is required"):
        build_embedding_gateway(settings)


def test_fake_embedding_provider_needs_no_cloud_approval() -> None:
    settings = Settings(
        DATABASE_PROVIDER="postgres",
        LLM_PROVIDER="litellm",
        ALLOW_CLOUD_DATABASE_DATA=False,
        EMBEDDING_PROVIDER="fake",
        EMBEDDING_DIMENSION=768,
    )

    gateway = build_embedding_gateway(settings)

    assert gateway.provider == "fake"
    assert gateway.dimension == 768


def test_approval_status_lifecycle_values() -> None:
    assert {status.value for status in ApprovalStatus} == {
        "PROPOSED",
        "CONFIRMED",
        "REJECTED",
        "STALE",
    }
    assert DataSourceStatus.READY.value == "READY"
