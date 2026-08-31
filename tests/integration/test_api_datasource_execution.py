"""API-level datasource selection regressions.

These deliberately use the real FastAPI dependency path and
``DataSourceRuntimeProvider``.  Calling a prebuilt graph would miss the bug
where a selected datasource changed its knowledge namespace but execution kept
using the process-default database.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.routes import (
    get_authorization_gateway,
    get_database_gateway,
    get_knowledge_runtime,
    get_llm_gateway,
)
from app.authorization.gateway import (
    AuthorizationDecision,
    AuthorizationGateway,
    AuthorizationRequest,
)
from app.config import Settings
from app.contracts.analytics import ClaimEvidence, GroundedClaim
from app.data.fake import FakeDatabaseGateway
from app.data.gateway import (
    ColumnMetadata,
    DatabaseExecutionMetadata,
    DatabaseGateway,
    DatabaseQueryResult,
    DatabaseSource,
    ResultColumnMetadata,
    TableMetadata,
)
from app.knowledge.contracts import DataSource, DataSourceStatus
from app.knowledge.execution import DataSourceRuntimeProvider
from app.knowledge.runtime import _in_memory_runtime
from app.llm.gateway import AnswerGeneration, LLMGateway, ResponseModelT, SQLGeneration
from app.main import app

SOURCE_A = uuid4()
SOURCE_B = uuid4()
SOURCE_OFFLINE = uuid4()


def _source(source_id: UUID, name: str, reference: str, schema: str) -> DataSource:
    now = datetime.now(UTC)
    return DataSource(
        id=source_id,
        name=name,
        database_type="postgres",
        connection_ref=reference,
        allowed_schemas=(schema,),
        status=DataSourceStatus.READY,
        created_at=now,
        updated_at=now,
    )


class _Registry:
    def __init__(self, sources: dict[UUID, DataSource]) -> None:
        self._sources = sources

    async def get(self, data_source_id: UUID) -> DataSource | None:
        return self._sources.get(data_source_id)


class _SentinelGateway(DatabaseGateway):
    def __init__(self, schema: str, sentinel: int) -> None:
        self.schema = schema
        self.sentinel = sentinel
        self.executed_sql: list[str] = []
        self.searches = 0

    def source(self) -> DatabaseSource:
        return DatabaseSource(
            identifier=f"sentinel:{self.schema}", dialect="postgres", provider="test"
        )

    async def health_check(self) -> bool:
        return True

    async def search_schema(self, question: str) -> list[TableMetadata]:
        del question
        self.searches += 1
        return [
            TableMetadata(
                schema_name=self.schema,
                table_name="sentinel_value",
                columns=["value"],
                description="test sentinel",
                column_metadata=[
                    ColumnMetadata(
                        name="value", data_type="integer", nullable=False
                    )
                ],
            )
        ]

    async def execute_readonly(
        self, sql: str, parameters: Sequence[Any] = ()
    ) -> DatabaseQueryResult:
        del parameters
        self.executed_sql.append(sql)
        return DatabaseQueryResult(
            rows=[{"value": self.sentinel}],
            columns=[ResultColumnMetadata(name="value", data_type="integer")],
            metadata=DatabaseExecutionMetadata(
                duration_ms=1,
                executed_at=datetime.now(UTC),
                row_count=1,
                result_bytes=8,
                truncated=False,
                live=False,
            ),
        )

    async def close(self) -> None:
        return None


class _SchemaAwareLLM(LLMGateway):
    async def generate_structured(
        self,
        *,
        model_alias: str,
        system: str,
        user: str,
        response_model: type[ResponseModelT],
    ) -> ResponseModelT:
        del model_alias, system
        if response_model.__name__ == "MetricSelection":
            return response_model.model_validate({"intent": "adhoc"})
        if response_model is SQLGeneration:
            schema = "source_a" if "source_a.sentinel_value" in user else "source_b"
            return response_model.model_validate(
                {
                    "action": "execute",
                    "sql": f"SELECT value FROM {schema}.sentinel_value LIMIT 100",
                    "analysis": {"intent": "sentinel"},
                }
            )
        if response_model is AnswerGeneration:
            value = 111 if '"value": 111' in user else 222
            return response_model.model_validate(
                {
                    "answer": f"The selected sentinel is {value}.",
                    "claims": [
                        GroundedClaim(
                            claim="Sentinel result.",
                            evidence=[ClaimEvidence(row_index=0, field="value", value=value)],
                        )
                    ],
                }
            )
        # SQL-only integration: the graph finalizer does not ask for prose.
        raise AssertionError(f"unexpected response model {response_model.__name__}")


class _CrossSourceLLM(_SchemaAwareLLM):
    """A malicious/incorrect model that names a table in the other source."""

    def __init__(self, target_schema: str) -> None:
        self._target_schema = target_schema

    async def generate_structured(
        self,
        *,
        model_alias: str,
        system: str,
        user: str,
        response_model: type[ResponseModelT],
    ) -> ResponseModelT:
        if response_model is SQLGeneration:
            return response_model.model_validate(
                {
                    "action": "execute",
                    "sql": (
                        f"SELECT value FROM {self._target_schema}.sentinel_value LIMIT 100"
                    ),
                    "analysis": {"intent": "cross_source"},
                }
            )
        return await super().generate_structured(
            model_alias=model_alias,
            system=system,
            user=user,
            response_model=response_model,
        )


class _AllowAll(AuthorizationGateway):
    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        return AuthorizationDecision(
            allowed=True,
            provider="test",
            table_columns={table.identifier: table.columns for table in request.tables},
            allowed_schemas=tuple(sorted({table.schema_name for table in request.tables})),
            allowed_metrics=request.metrics,
        )

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_api_uses_the_selected_datasource_for_schema_and_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALLOWED_CONNECTION_REFS", "SOURCE_A_URL,SOURCE_B_URL")
    monkeypatch.setenv("SOURCE_A_URL", "postgresql://ignored/a")
    monkeypatch.setenv("SOURCE_B_URL", "postgresql://ignored/b")
    settings = Settings()
    gateways = {
        "SOURCE_A_URL": _SentinelGateway("source_a", 111),
        "SOURCE_B_URL": _SentinelGateway("source_b", 222),
    }
    sources = {
        SOURCE_A: _source(SOURCE_A, "A", "SOURCE_A_URL", "source_a"),
        SOURCE_B: _source(SOURCE_B, "B", "SOURCE_B_URL", "source_b"),
    }

    def factory(_: Settings, *, database_url: str, **__: Any) -> DatabaseGateway:
        return gateways["SOURCE_A_URL" if database_url.endswith("/a") else "SOURCE_B_URL"]

    runtime = await _in_memory_runtime(settings, SOURCE_A)
    runtime.execution = DataSourceRuntimeProvider(
        settings, registry=_Registry(sources), gateway_factory=factory
    )
    app.dependency_overrides[get_knowledge_runtime] = lambda: runtime
    app.dependency_overrides[get_database_gateway] = lambda: FakeDatabaseGateway()
    app.dependency_overrides[get_llm_gateway] = lambda: _SchemaAwareLLM()
    app.dependency_overrides[get_authorization_gateway] = lambda: _AllowAll()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first = await client.post(
                "/analytics/query",
                json={"question": "Show the sentinel", "data_source_id": str(SOURCE_A)},
            )
            second = await client.post(
                "/analytics/query",
                json={"question": "Show the sentinel", "data_source_id": str(SOURCE_B)},
            )
    finally:
        app.dependency_overrides.clear()
        await runtime.close()

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["rows"] == [{"value": 111}]
    assert second.json()["rows"] == [{"value": 222}]
    assert gateways["SOURCE_A_URL"].executed_sql == [
        "SELECT value FROM source_a.sentinel_value LIMIT 100"
    ]
    assert gateways["SOURCE_B_URL"].executed_sql == [
        "SELECT value FROM source_b.sentinel_value LIMIT 100"
    ]


@pytest.mark.asyncio
async def test_api_reports_an_unavailable_selected_datasource_without_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALLOWED_CONNECTION_REFS", "OFFLINE_SOURCE_URL")
    monkeypatch.delenv("OFFLINE_SOURCE_URL", raising=False)
    settings = Settings()
    runtime = await _in_memory_runtime(settings, SOURCE_OFFLINE)
    runtime.execution = DataSourceRuntimeProvider(
        settings,
        registry=_Registry(
            {
                SOURCE_OFFLINE: _source(
                    SOURCE_OFFLINE, "Offline", "OFFLINE_SOURCE_URL", "offline"
                )
            }
        ),
    )
    default_database = FakeDatabaseGateway()
    app.dependency_overrides[get_knowledge_runtime] = lambda: runtime
    app.dependency_overrides[get_database_gateway] = lambda: default_database
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/analytics/query",
                json={"question": "Show the sentinel", "data_source_id": str(SOURCE_OFFLINE)},
            )
    finally:
        app.dependency_overrides.clear()
        await runtime.close()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"
    assert default_database.executed_sql == []
    assert "OFFLINE_SOURCE_URL" not in response.text
    assert "postgresql" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("selected_id", "selected_schema", "other_schema"),
    [
        (SOURCE_A, "source_a", "source_b"),
        (SOURCE_B, "source_b", "source_a"),
    ],
)
async def test_api_rejects_another_datasources_table_before_selected_execution(
    monkeypatch: pytest.MonkeyPatch,
    selected_id: UUID,
    selected_schema: str,
    other_schema: str,
) -> None:
    monkeypatch.setenv("ALLOWED_CONNECTION_REFS", "SOURCE_A_URL,SOURCE_B_URL")
    monkeypatch.setenv("SOURCE_A_URL", "postgresql://ignored/a")
    monkeypatch.setenv("SOURCE_B_URL", "postgresql://ignored/b")
    settings = Settings()
    selected = _SentinelGateway(selected_schema, 111)
    runtime = await _in_memory_runtime(settings, selected_id)
    runtime.execution = DataSourceRuntimeProvider(
        settings,
        registry=_Registry(
            {
                selected_id: _source(
                    selected_id,
                    "Selected",
                    "SOURCE_A_URL" if selected_schema == "source_a" else "SOURCE_B_URL",
                    selected_schema,
                )
            }
        ),
        gateway_factory=lambda _settings, **_kwargs: selected,
    )
    app.dependency_overrides[get_knowledge_runtime] = lambda: runtime
    app.dependency_overrides[get_database_gateway] = lambda: FakeDatabaseGateway()
    app.dependency_overrides[get_llm_gateway] = lambda: _CrossSourceLLM(other_schema)
    app.dependency_overrides[get_authorization_gateway] = lambda: _AllowAll()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/analytics/query",
                json={"question": "Show the sentinel", "data_source_id": str(selected_id)},
            )
    finally:
        app.dependency_overrides.clear()
        await runtime.close()

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsafe_sql"
    assert selected.executed_sql == []
