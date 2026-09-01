"""The evaluation loop, through the real API.

The thing worth proving is that a run asks the product rather than a shortcut
around it: the whole value of a benchmark is that it exercises authorization,
routing, SQL validation and execution the way a request does.
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

SOURCE = uuid4()


def _source() -> DataSource:
    now = datetime.now(UTC)
    return DataSource(
        id=SOURCE,
        name="Evaluated",
        database_type="postgres",
        connection_ref="EVAL_URL",
        allowed_schemas=("erp",),
        status=DataSourceStatus.READY,
        schema_fingerprint="fp-1",
        created_at=now,
        updated_at=now,
    )


class _Registry:
    async def get(self, data_source_id: UUID) -> DataSource | None:
        return _source() if data_source_id == SOURCE else None


class _Gateway(DatabaseGateway):
    """Answers 42, and remembers that it was asked."""

    def __init__(self) -> None:
        self.executed: list[str] = []

    def source(self) -> DatabaseSource:
        return DatabaseSource(identifier="postgres:evaluated", dialect="postgres")

    async def health_check(self) -> bool:
        return True

    async def search_schema(self, question: str) -> list[TableMetadata]:
        del question
        return [
            TableMetadata(
                schema_name="erp",
                table_name="emp_mst",
                columns=["emp_no", "stat_cd"],
                description="employees",
                column_metadata=[
                    ColumnMetadata(name="emp_no", data_type="integer", nullable=False),
                    ColumnMetadata(name="stat_cd", data_type="char", nullable=False),
                ],
            )
        ]

    async def execute_readonly(
        self, sql: str, parameters: Sequence[Any] = ()
    ) -> DatabaseQueryResult:
        del parameters
        self.executed.append(sql)
        return DatabaseQueryResult(
            rows=[{"active_employee_count": 42}],
            columns=[
                ResultColumnMetadata(name="active_employee_count", data_type="integer")
            ],
            metadata=DatabaseExecutionMetadata(
                duration_ms=1,
                executed_at=datetime.now(UTC),
                row_count=1,
                result_bytes=8,
                truncated=False,
                live=True,
            ),
        )

    async def close(self) -> None:
        return None


class _LLM(LLMGateway):
    async def generate_structured(
        self,
        *,
        model_alias: str,
        system: str,
        user: str,
        response_model: type[ResponseModelT],
    ) -> ResponseModelT:
        del model_alias, system, user
        if response_model.__name__ == "MetricSelection":
            # No governed metric here; the question is answered from SQL.
            return response_model.model_validate(
                {"intent": "adhoc", "confidence": 0.9, "reason": "no metric fits"}
            )
        if response_model is SQLGeneration:
            return response_model.model_validate(
                {
                    "action": "execute",
                    "sql": "SELECT count(emp_no) AS active_employee_count"
                    " FROM erp.emp_mst WHERE stat_cd = 'A'",
                    "analysis": {"intent": "headcount"},
                }
            )
        if response_model is AnswerGeneration:
            return response_model.model_validate(
                {
                    "answer": "There are 42 active employees.",
                    "claims": [
                        GroundedClaim(
                            claim="42 active employees",
                            evidence=[
                                ClaimEvidence(
                                    row_index=0,
                                    field="active_employee_count",
                                    value=42,
                                )
                            ],
                        ).model_dump()
                    ],
                }
            )
        raise AssertionError(f"unexpected response model {response_model.__name__}")


class _AllowAll(AuthorizationGateway):
    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        return AuthorizationDecision(
            allowed=True,
            provider="test",
            table_columns={table.identifier: table.columns for table in request.tables},
            allowed_schemas=tuple(
                sorted({table.schema_name for table in request.tables})
            ),
            allowed_metrics=request.metrics,
            knowledge_review_allowed=True,
            debug_allowed=True,
        )

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_a_run_asks_the_product_and_classifies_a_regression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALLOWED_CONNECTION_REFS", "EVAL_URL")
    monkeypatch.setenv("EVAL_URL", "postgresql://ignored/evaluated")
    settings = Settings()
    database = _Gateway()
    runtime = await _in_memory_runtime(settings, SOURCE)
    runtime.execution = DataSourceRuntimeProvider(
        settings,
        registry=_Registry(),
        gateway_factory=lambda _settings, **_kwargs: database,
    )
    app.dependency_overrides[get_knowledge_runtime] = lambda: runtime
    app.dependency_overrides[get_database_gateway] = lambda: FakeDatabaseGateway()
    app.dependency_overrides[get_llm_gateway] = lambda: _LLM()
    app.dependency_overrides[get_authorization_gateway] = lambda: _AllowAll()
    base = f"/knowledge/data-sources/{SOURCE}"
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            created = await client.post(
                f"{base}/evaluation-cases",
                json={
                    "name": "Active headcount",
                    "question": "How many active employees do we have?",
                    "expectation": "SCALAR",
                    "expected": {"value": "42"},
                },
            )
            assert created.status_code == 201, created.text

            first = await client.post(f"{base}/evaluation-runs")
            assert first.status_code == 201, first.text
            first_body = first.json()

            # A case whose right answer changed: the same question, now wrong.
            wrong = await client.post(
                f"{base}/evaluation-cases",
                json={
                    "name": "Payroll",
                    "question": "What is our current annual payroll?",
                    "expectation": "SCALAR",
                    "expected": {"value": "6345000"},
                },
            )
            assert wrong.status_code == 201, wrong.text

            second = await client.post(f"{base}/evaluation-runs")
            assert second.status_code == 201, second.text
            second_body = second.json()
    finally:
        app.dependency_overrides.clear()
        await runtime.close()

    # The run went through the real request path and reached the datasource's
    # own gateway -- not a private benchmark shortcut.
    assert database.executed, "the evaluation never reached the database"
    assert all("erp.emp_mst" in statement for statement in database.executed)

    assert first_body["case_count"] == 1
    assert first_body["passed"] == 1
    assert first_body["results"][0]["movement"] == "NEW"
    assert first_body["results"][0]["actual"] == "42"

    outcomes = {item["name"]: item for item in second_body["results"]}
    assert outcomes["Active headcount"]["movement"] == "UNCHANGED_PASS"
    assert outcomes["Payroll"]["outcome"] == "FAIL", (
        "a wrong number was reported as correct"
    )
    assert outcomes["Payroll"]["movement"] == "NEW"
    assert second_body["failed"] == 1
    assert second_body["regressions"] == 0


@pytest.mark.asyncio
async def test_an_uncomparable_expectation_is_refused_at_the_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALLOWED_CONNECTION_REFS", "EVAL_URL")
    settings = Settings()
    runtime = await _in_memory_runtime(settings, SOURCE)
    app.dependency_overrides[get_knowledge_runtime] = lambda: runtime
    app.dependency_overrides[get_database_gateway] = lambda: FakeDatabaseGateway()
    app.dependency_overrides[get_authorization_gateway] = lambda: _AllowAll()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/knowledge/data-sources/{SOURCE}/evaluation-cases",
                json={
                    "name": "Nothing to compare",
                    "question": "anything",
                    "expectation": "SCALAR",
                    "expected": {},
                },
            )
    finally:
        app.dependency_overrides.clear()
        await runtime.close()

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_a_stale_table_warns_only_the_answers_that_read_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A correct query over stale data still produces a misleading answer.

    The figures are untouched -- the query was right -- but the answer says
    plainly that the table behind it stopped loading. And an assertion about a
    table this answer never read attaches nothing, because a page that warns
    about everything is a page people stop reading.
    """
    from app.knowledge.quality import (
        AssertionType,
        QualityAssertion,
        QualityCheckResult,
        QualityStatus,
    )

    monkeypatch.setenv("ALLOWED_CONNECTION_REFS", "EVAL_URL")
    monkeypatch.setenv("EVAL_URL", "postgresql://ignored/evaluated")
    settings = Settings()
    database = _Gateway()
    runtime = await _in_memory_runtime(settings, SOURCE)
    runtime.execution = DataSourceRuntimeProvider(
        settings,
        registry=_Registry(),
        gateway_factory=lambda _settings, **_kwargs: database,
    )
    read = QualityAssertion(
        data_source_id=SOURCE,
        name="Employee master freshness",
        assertion_type=AssertionType.FRESHNESS,
        schema_name="erp",
        table_name="emp_mst",
        column_name="loaded_at",
        configuration={"max_age_minutes": 120},
    )
    unread = QualityAssertion(
        data_source_id=SOURCE,
        name="Invoice freshness",
        assertion_type=AssertionType.FRESHNESS,
        schema_name="erp",
        table_name="ar_inv_hdr",
        column_name="loaded_at",
        configuration={"max_age_minutes": 120},
    )
    quality = runtime.quality
    assert quality is not None
    for assertion in (read, unread):
        await quality.upsert(assertion)
        await quality.record(
            QualityCheckResult(
                assertion_id=assertion.id,
                data_source_id=SOURCE,
                status=QualityStatus.STALE,
                detail=f"{assertion.table_name} is 3.0 days old.",
            )
        )

    app.dependency_overrides[get_knowledge_runtime] = lambda: runtime
    app.dependency_overrides[get_database_gateway] = lambda: FakeDatabaseGateway()
    app.dependency_overrides[get_llm_gateway] = lambda: _LLM()
    app.dependency_overrides[get_authorization_gateway] = lambda: _AllowAll()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/analytics/query",
                json={
                    "question": "How many active employees do we have?",
                    "data_source_id": str(SOURCE),
                },
            )
    finally:
        app.dependency_overrides.clear()
        await runtime.close()

    assert response.status_code == 200, response.text
    body = response.json()
    # The number is untouched: the query was correct.
    assert body["rows"] == [{"active_employee_count": 42}]
    warnings = body["data_quality"]
    assert [item["table"] for item in warnings] == ["erp.emp_mst"], (
        "an unrelated table's warning was attached, or a relevant one was not"
    )
    assert warnings[0]["status"] == "STALE"
    assert "3.0 days" in warnings[0]["message"]


@pytest.mark.asyncio
async def test_the_answer_trace_is_derived_and_carries_no_connection_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Everything in the trace is read off what was recorded.

    And nothing in it names a host, a role, a DSN or a connection reference --
    the trace is the part of the system a curious user reads most closely.
    """
    monkeypatch.setenv("ALLOWED_CONNECTION_REFS", "EVAL_URL")
    monkeypatch.setenv("EVAL_URL", "postgresql://secret:hunter2@db.internal/evaluated")
    settings = Settings()
    database = _Gateway()
    runtime = await _in_memory_runtime(settings, SOURCE)
    runtime.execution = DataSourceRuntimeProvider(
        settings,
        registry=_Registry(),
        gateway_factory=lambda _settings, **_kwargs: database,
    )
    app.dependency_overrides[get_knowledge_runtime] = lambda: runtime
    app.dependency_overrides[get_database_gateway] = lambda: FakeDatabaseGateway()
    app.dependency_overrides[get_llm_gateway] = lambda: _LLM()
    app.dependency_overrides[get_authorization_gateway] = lambda: _AllowAll()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/analytics/query",
                json={
                    "question": "How many active employees do we have?",
                    "data_source_id": str(SOURCE),
                },
            )
    finally:
        app.dependency_overrides.clear()
        await runtime.close()

    assert response.status_code == 200, response.text
    trace = response.json()["trace"]
    assert trace is not None
    assert trace["data_source"] == "postgres:evaluated"
    assert trace["route"] == "adhoc_analytics"
    assert trace["grounded"] is True
    # Read out of the statement that actually ran.
    assert [table["table"] for table in trace["tables"]] == ["erp.emp_mst"]
    assert trace["tables"][0]["columns"] == ["emp_no", "stat_cd"]
    assert trace["column_level"] is True

    body = response.text
    for secret in ("hunter2", "db.internal", "EVAL_URL", "postgresql://"):
        assert secret not in body, f"the response carried {secret!r}"
