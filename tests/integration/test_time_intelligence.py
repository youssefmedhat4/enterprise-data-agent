"""Time intelligence through the real request path.

The failure worth preventing is quiet: someone asks for revenue this month, the
model writes valid SQL with no date filter, and the answer covers every invoice
ever raised. It passes every other guardrail and the number looks like a number.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.routes import (
    get_authorization_gateway,
    get_database_gateway,
    get_knowledge_runtime,
    get_llm_gateway,
    get_settings,
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
from app.knowledge.contracts import ApprovalStatus, DataSource, DataSourceStatus
from app.knowledge.execution import DataSourceRuntimeProvider
from app.knowledge.runtime import _in_memory_runtime
from app.llm.gateway import AnswerGeneration, LLMGateway, ResponseModelT, SQLGeneration
from app.main import app
from app.timeintel.dimensions import (
    TemporalDimension,
    TemporalRole,
    TemporalStorage,
)
from app.timeintel.policy import FiscalYearLabel, PolicyStatus, TimePolicy, WeekStart

SOURCE = uuid4()
ATTRIBUTE = uuid4()
SECOND_ATTRIBUTE = uuid4()

#: Every relative period below resolves against this instant.
ANCHOR = datetime(2026, 9, 1, 12, 0, tzinfo=ZoneInfo("Africa/Cairo"))


def _source() -> DataSource:
    now = datetime.now(UTC)
    return DataSource(
        id=SOURCE,
        name="Timed",
        database_type="postgres",
        connection_ref="TIME_URL",
        allowed_schemas=("erp",),
        status=DataSourceStatus.READY,
        created_at=now,
        updated_at=now,
    )


class _Registry:
    async def get(self, data_source_id: UUID) -> DataSource | None:
        return _source() if data_source_id == SOURCE else None


class _Gateway(DatabaseGateway):
    def __init__(self) -> None:
        self.executed: list[str] = []

    def source(self) -> DatabaseSource:
        return DatabaseSource(identifier="postgres:timed", dialect="postgres")

    async def health_check(self) -> bool:
        return True

    async def search_schema(self, question: str) -> list[TableMetadata]:
        del question
        return [
            TableMetadata(
                schema_name="erp",
                table_name="ar_inv_hdr",
                columns=["inv_no", "amount", "inv_dt_chr"],
                description="invoices",
                column_metadata=[
                    ColumnMetadata(name="inv_no", data_type="integer", nullable=False),
                    ColumnMetadata(name="amount", data_type="numeric", nullable=False),
                    ColumnMetadata(name="inv_dt_chr", data_type="char", nullable=False),
                ],
            )
        ]

    async def execute_readonly(
        self, sql: str, parameters: Sequence[Any] = ()
    ) -> DatabaseQueryResult:
        del parameters
        self.executed.append(sql)
        return DatabaseQueryResult(
            rows=[{"invoiced": 1000}],
            columns=[ResultColumnMetadata(name="invoiced", data_type="numeric")],
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
    """Writes the SQL it is told to, and records the prompts it was given."""

    def __init__(self, *sql: str) -> None:
        self._sql = list(sql)
        self.prompts: list[str] = []

    async def generate_structured(
        self,
        *,
        model_alias: str,
        system: str,
        user: str,
        response_model: type[ResponseModelT],
    ) -> ResponseModelT:
        del model_alias, system
        self.prompts.append(user)
        if response_model.__name__ == "MetricSelection":
            return response_model.model_validate(
                {"intent": "adhoc", "confidence": 0.9, "reason": "no metric"}
            )
        if response_model is SQLGeneration or response_model.__name__ == "SQLRepair":
            statement = self._sql.pop(0) if len(self._sql) > 1 else self._sql[0]
            field = (
                "sql" if response_model is SQLGeneration else "repaired_sql"
            )
            payload: dict[str, Any] = {field: statement}
            if response_model is SQLGeneration:
                payload |= {"action": "execute", "analysis": {"intent": "revenue"}}
            return response_model.model_validate(payload)
        if response_model is AnswerGeneration:
            return response_model.model_validate(
                {
                    "answer": "Invoiced revenue was 1000.",
                    "claims": [
                        GroundedClaim(
                            claim="1000 invoiced",
                            evidence=[
                                ClaimEvidence(row_index=0, field="invoiced", value=1000)
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


def _dimension(
    attribute_id: UUID = ATTRIBUTE,
    *,
    column: str = "inv_dt_chr",
    concept: str = "Invoice Date",
    default: bool = True,
    role: TemporalRole = TemporalRole.EVENT_TIME,
) -> TemporalDimension:
    return TemporalDimension(
        data_source_id=SOURCE,
        semantic_attribute_id=attribute_id,
        role=role,
        storage=TemporalStorage.YYYYMMDD_TEXT,
        schema_name="erp",
        table_name="ar_inv_hdr",
        column_name=column,
        concept_name=concept,
        entity_name="Invoice",
        is_default_for_entity=default,
        status=ApprovalStatus.CONFIRMED,
    )


def _policy() -> TimePolicy:
    return TimePolicy(
        data_source_id=SOURCE,
        timezone="Africa/Cairo",
        week_start=WeekStart.SUNDAY,
        fiscal_year_start_month=7,
        fiscal_year_start_day=1,
        fiscal_year_label=FiscalYearLabel.END_YEAR,
        status=PolicyStatus.CONFIRMED,
    )


def _debug_settings() -> Settings:
    return Settings(API_DEBUG_PROVENANCE_ENABLED=True)  # type: ignore[call-arg]


async def _runtime(
    settings: Settings, database: DatabaseGateway, dimensions: list[TemporalDimension]
) -> Any:
    runtime = await _in_memory_runtime(settings, SOURCE)
    runtime.execution = DataSourceRuntimeProvider(
        settings,
        registry=_Registry(),
        gateway_factory=lambda _settings, **_kwargs: database,
    )
    store = runtime.time_intelligence
    assert store is not None
    await store.save_policy(_policy())
    for dimension in dimensions:
        await store.save_dimension(dimension)
    return runtime


async def _ask(
    question: str,
    *,
    llm: _LLM,
    database: _Gateway,
    dimensions: list[TemporalDimension],
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    monkeypatch.setenv("ALLOWED_CONNECTION_REFS", "TIME_URL")
    monkeypatch.setenv("TIME_URL", "postgresql://ignored/timed")
    settings = _debug_settings()
    runtime = await _runtime(settings, database, dimensions)
    app.dependency_overrides[get_knowledge_runtime] = lambda: runtime
    app.dependency_overrides[get_database_gateway] = lambda: FakeDatabaseGateway()
    app.dependency_overrides[get_llm_gateway] = lambda: llm
    app.dependency_overrides[get_authorization_gateway] = lambda: _AllowAll()
    app.dependency_overrides[get_settings] = _debug_settings
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/analytics/query",
                json={
                    "question": question,
                    "data_source_id": str(SOURCE),
                    "include_debug": True,
                    # A fixed anchor, so "year to date" means the same thing
                    # every time this test runs.
                    "as_of": ANCHOR.isoformat(),
                },
            )
    finally:
        app.dependency_overrides.clear()
        await runtime.close()
    return {"status": response.status_code, "body": response.json()}


_FILTERED = (
    "SELECT sum(amount) AS invoiced FROM erp.ar_inv_hdr "
    "WHERE to_timestamp(inv_dt_chr, 'YYYYMMDD') >= '2026-01-01' "
    "AND to_timestamp(inv_dt_chr, 'YYYYMMDD') < '2026-09-01'"
)
_UNFILTERED = "SELECT sum(amount) AS invoiced FROM erp.ar_inv_hdr"


@pytest.mark.asyncio
async def test_the_resolved_period_reaches_the_model_and_the_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _Gateway()
    llm = _LLM(_FILTERED)

    result = await _ask(
        "Show invoiced revenue year to date",
        llm=llm,
        database=database,
        dimensions=[_dimension()],
        monkeypatch=monkeypatch,
    )

    assert result["status"] == 200, result["body"]
    prompt = next(p for p in llm.prompts if "Resolved time period" in p)
    # Exact instants, computed from this datasource's calendar -- not the
    # phrase handed over for the model to interpret.
    assert "2025-12-31T22:00:00+00:00" in prompt
    assert "2026-09-01T09:00:00+00:00" in prompt
    assert "Africa/Cairo" in prompt

    trace = result["body"]["trace"]["time"]
    assert trace["label"] == "year to date"
    assert trace["timezone"] == "Africa/Cairo"
    assert trace["start"] == "2025-12-31T22:00:00+00:00"
    assert trace["end"] == "2026-09-01T09:00:00+00:00"
    # The business name, not the physical column.
    assert trace["time_dimension"] == "Invoice Date"


@pytest.mark.asyncio
async def test_a_query_that_dropped_the_period_is_repaired_not_returned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Valid SQL over all of history looks exactly like a right answer."""
    database = _Gateway()
    llm = _LLM(_UNFILTERED, _FILTERED)

    result = await _ask(
        "Show invoiced revenue year to date",
        llm=llm,
        database=database,
        dimensions=[_dimension()],
        monkeypatch=monkeypatch,
    )

    assert result["status"] == 200, result["body"]
    # The unfiltered statement never reached the database.
    assert database.executed
    assert all("inv_dt_chr" in statement for statement in database.executed)
    repairs = [p for p in llm.prompts if "Required correction" in p]
    assert repairs, f"no repair prompt; prompts={[p[:80] for p in llm.prompts]}"
    assert "does not restrict inv_dt_chr" in repairs[0]


@pytest.mark.asyncio
async def test_a_query_that_keeps_dropping_the_period_fails_rather_than_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One bounded repair, then an honest failure. Never a retry loop."""
    database = _Gateway()
    llm = _LLM(_UNFILTERED)

    result = await _ask(
        "Show invoiced revenue year to date",
        llm=llm,
        database=database,
        dimensions=[_dimension()],
        monkeypatch=monkeypatch,
    )

    assert result["status"] >= 400
    assert database.executed == [], "an unfiltered query reached the database"


@pytest.mark.asyncio
async def test_several_candidate_dates_ask_rather_than_guess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"Invoices last year" over a posting date and an invoice date is two
    different questions, and picking one answers neither."""
    database = _Gateway()
    llm = _LLM(_FILTERED)

    result = await _ask(
        "Show invoiced revenue last year",
        llm=llm,
        database=database,
        dimensions=[
            _dimension(default=False),
            _dimension(
                SECOND_ATTRIBUTE,
                column="post_dt_chr",
                concept="Posting Date",
                default=False,
                role=TemporalRole.EVENT_TIME,
            ),
        ],
        monkeypatch=monkeypatch,
    )

    body = result["body"]
    assert body["clarification_required"] is True
    assert "Invoice Date" in body["clarification_question"]
    assert "Posting Date" in body["clarification_question"]
    assert database.executed == []


@pytest.mark.asyncio
async def test_a_fiscal_question_uses_the_datasources_own_calendar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _Gateway()
    llm = _LLM(
        "SELECT sum(amount) AS invoiced FROM erp.ar_inv_hdr "
        "WHERE to_timestamp(inv_dt_chr, 'YYYYMMDD') >= '2026-07-01' "
        "AND to_timestamp(inv_dt_chr, 'YYYYMMDD') < '2026-09-01'"
    )

    result = await _ask(
        "Show invoiced revenue fiscal YTD",
        llm=llm,
        database=database,
        dimensions=[_dimension()],
        monkeypatch=monkeypatch,
    )

    trace = result["body"]["trace"]["time"]
    # July, because this datasource's fiscal year starts in July -- not January.
    assert trace["start"] == "2026-06-30T21:00:00+00:00"
    assert trace["label"] == "fiscal year to date"
    assert trace["fiscal"] is True


@pytest.mark.asyncio
async def test_a_datasource_without_temporal_mappings_answers_as_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nobody has reviewed this database's dates, so it has not opted in.

    Refusing here would break every time-flavoured question on a datasource
    that was answering them perfectly well.
    """
    database = _Gateway()
    llm = _LLM(_UNFILTERED)

    result = await _ask(
        "Show invoiced revenue last year",
        llm=llm,
        database=database,
        dimensions=[],
        monkeypatch=monkeypatch,
    )

    assert result["status"] == 200, result["body"]
    assert result["body"]["trace"]["time"] is None
    assert len(database.executed) == 1
    assert "inv_dt_chr" not in database.executed[0]


@pytest.mark.asyncio
async def test_a_comparison_carries_both_windows_into_the_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _Gateway()
    llm = _LLM(_FILTERED)

    result = await _ask(
        "Show invoiced revenue year to date vs last year",
        llm=llm,
        database=database,
        dimensions=[_dimension()],
        monkeypatch=monkeypatch,
    )

    trace = result["body"]["trace"]["time"]
    assert trace["comparison_label"] == "same period last year"
    assert trace["comparison_start"] == "2024-12-31T22:00:00+00:00"
    # The equivalent elapsed stretch, not the whole of last year.
    assert trace["comparison_end"] == "2025-09-01T09:00:00+00:00"


@pytest.mark.asyncio
async def test_the_trace_carries_no_connection_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _Gateway()
    llm = _LLM(_FILTERED)

    result = await _ask(
        "Show invoiced revenue year to date",
        llm=llm,
        database=database,
        dimensions=[_dimension()],
        monkeypatch=monkeypatch,
    )

    body = str(result["body"])
    for secret in ("TIME_URL", "postgresql://", "ignored/timed"):
        assert secret not in body
