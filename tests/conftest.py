from collections.abc import Iterator

import pytest

from app.api.routes import _development_checkpoint_store
from app.config import get_settings


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-cloud",
        action="store_true",
        default=False,
        help="Allow tests marked cloud to make explicitly configured external LLM calls.",
    )
    parser.addoption(
        "--run-local-llm",
        action="store_true",
        default=False,
        help="Allow tests marked local_llm to call an explicitly configured local model.",
    )
    parser.addoption(
        "--run-postgres",
        action="store_true",
        default=False,
        help="Allow tests marked postgres to call the configured PostgreSQL database.",
    )
    parser.addoption(
        "--run-wren",
        action="store_true",
        default=False,
        help="Allow tests marked wren to call the configured local Wren service.",
    )
    parser.addoption(
        "--run-opa",
        action="store_true",
        default=False,
        help="Allow tests marked opa to call the configured OPA service.",
    )
    parser.addoption(
        "--run-cube",
        action="store_true",
        default=False,
        help="Allow tests marked cube to call the configured local Cube Core service.",
    )
    parser.addoption(
        "--run-legacy",
        action="store_true",
        default=False,
        help="Allow tests marked legacy to call the configured Legacy ERP fixture.",
    )


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    run_cloud = config.getoption("--run-cloud")
    run_local = config.getoption("--run-local-llm")
    run_postgres = config.getoption("--run-postgres")
    run_wren = config.getoption("--run-wren")
    run_opa = config.getoption("--run-opa")
    run_cube = config.getoption("--run-cube")
    run_legacy = config.getoption("--run-legacy")
    skip_cloud = pytest.mark.skip(reason="Cloud tests require explicit --run-cloud opt-in")
    skip_local = pytest.mark.skip(
        reason="Local model tests require explicit --run-local-llm opt-in"
    )
    skip_postgres = pytest.mark.skip(
        reason="PostgreSQL tests require explicit --run-postgres opt-in"
    )
    skip_wren = pytest.mark.skip(reason="Wren tests require explicit --run-wren opt-in")
    skip_opa = pytest.mark.skip(reason="OPA tests require explicit --run-opa opt-in")
    skip_cube = pytest.mark.skip(reason="Cube tests require explicit --run-cube opt-in")
    skip_legacy = pytest.mark.skip(
        reason="Legacy ERP tests require explicit --run-legacy opt-in"
    )
    for item in items:
        if not run_cloud and item.get_closest_marker("cloud") is not None:
            item.add_marker(skip_cloud)
        if not run_local and item.get_closest_marker("local_llm") is not None:
            item.add_marker(skip_local)
        if not run_postgres and item.get_closest_marker("postgres") is not None:
            item.add_marker(skip_postgres)
        if not run_wren and item.get_closest_marker("wren") is not None:
            item.add_marker(skip_wren)
        if not run_opa and item.get_closest_marker("opa") is not None:
            item.add_marker(skip_opa)
        if not run_cube and item.get_closest_marker("cube") is not None:
            item.add_marker(skip_cube)
        if not run_legacy and item.get_closest_marker("legacy") is not None:
            item.add_marker(skip_legacy)


@pytest.fixture(autouse=True)
def deterministic_test_configuration(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> Iterator[None]:
    external_model_test = (
        request.node.get_closest_marker("cloud") is not None
        or request.node.get_closest_marker("local_llm") is not None
    )
    legacy_database_test = request.node.get_closest_marker("legacy") is not None
    if not external_model_test:
        monkeypatch.setenv("LLM_PROVIDER", "fake")
        monkeypatch.setenv("LLM_MODEL_ANALYTICS_GENERAL", "fake/analytics-general")
        monkeypatch.setenv("LLM_MODEL_SQL_REASONER", "fake/sql-reasoner")
        monkeypatch.setenv("DATABASE_PROVIDER", "fake")
        monkeypatch.setenv("AUTHENTICATION_PROVIDER", "local")
        monkeypatch.setenv("AUTHORIZATION_PROVIDER", "local")
        monkeypatch.setenv("CONVERSATION_CHECKPOINT_PROVIDER", "memory")
        monkeypatch.setenv("GOVERNANCE_PROVIDER", "disabled")
        monkeypatch.setenv("OBSERVABILITY_PROVIDER", "none")
        monkeypatch.setenv("READINESS_REQUIRE_METRIC_PROVIDER", "0")
        monkeypatch.setenv("RUN_CLOUD_LLM_TESTS", "0")
        monkeypatch.setenv("RUN_LOCAL_LLM_TESTS", "0")
        monkeypatch.setenv("SEMANTIC_PROVIDER", "inmemory")
        monkeypatch.setenv("SQL_GENERATION_PROVIDER", "llm")
        monkeypatch.setenv("METRIC_PROVIDER", "cube")
        # Settings reads .env, so a developer who enabled persistent knowledge
        # locally would otherwise have the whole suite hit a real database and
        # share state between tests. Postgres-marked tests opt in explicitly.
        monkeypatch.setenv("KNOWLEDGE_STORAGE", "memory")
        monkeypatch.setenv("KNOWLEDGE_WORKER_ENABLED", "0")
        monkeypatch.setenv("QUESTION_MEMORY_ENABLED", "0")
        monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
        # Live Legacy ERP checks resolve their explicitly allowlisted secret
        # through the same Settings/.env path as registered datasources.  The
        # DSN remains private; only its reference name is made available.
        allowed_references = (
            "DATABASE_URL,LEGACY_DATABASE_URL"
            if legacy_database_test
            else "DATABASE_URL"
        )
        monkeypatch.setenv("ALLOWED_CONNECTION_REFS", allowed_references)
    get_settings.cache_clear()
    _development_checkpoint_store.cache_clear()
    yield
    get_settings.cache_clear()
    _development_checkpoint_store.cache_clear()
