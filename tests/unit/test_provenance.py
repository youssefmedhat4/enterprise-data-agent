from datetime import UTC, datetime

from app.agent.context import AnalysisPlan, TimeRange
from app.agent.provenance import build_internal_provenance
from app.contracts.analytics import ExecutionMetadata
from app.data.gateway import DatabaseSource, ResultColumnMetadata


def test_internal_provenance_is_richer_than_default_public_view() -> None:
    executed_at = datetime.now(UTC)
    internal = build_internal_provenance(
        request_id="request-1",
        trace_id="trace-1",
        source=DatabaseSource(
            identifier="toolbox:enterprise-postgres",
            dialect="postgres",
            provider="mcp_toolbox",
        ),
        generated_sql="SELECT d.name FROM analytics.departments d",
        validated_sql="SELECT d.name FROM analytics.departments AS d LIMIT 100",
        rows=[{"name": "Engineering"}],
        analysis=AnalysisPlan(
            metric="payroll",
            dimensions=["department"],
            filters={"status": "paid"},
            time_range=TimeRange(start="2025-01-01", end="2025-12-31"),
        ),
        execution=ExecutionMetadata(
            query_id="query-1",
            status="completed",
            row_count=1,
            duration_ms=2.5,
            executed_at=executed_at,
        ),
        model_aliases=["sql-reasoner", "analytics-general"],
        selected_schema_ids=["analytics.departments"],
        semantic_definition_ids=["annual_base_salary"],
        semantic_provider="wren",
        semantic_retrieval_latency_ms=4.2,
        semantic_model_ids=["wren:enterprise_analytics:departments"],
        semantic_relationship_ids=["wren:enterprise_analytics:employees_departments"],
        semantic_measure_ids=["wren:payroll.net_amount"],
        sql_generation_provider="llm",
    )

    public = internal.public_view()
    debug = internal.public_view(include_debug=True)

    assert internal.request_id == "request-1"
    assert internal.query_id == "query-1"
    assert internal.filters == {"status": "paid"}
    assert internal.time_range == {"start": "2025-01-01", "end": "2025-12-31", "label": None}
    assert internal.tables == ["analytics.departments"]
    assert internal.model_aliases == ["sql-reasoner", "analytics-general"]
    assert internal.database_provider == "mcp_toolbox"
    assert internal.database_dialect == "postgres"
    assert public.source == "toolbox:enterprise-postgres"
    assert "database_provider" not in public.model_dump()
    assert public.debug is None
    assert "request_id" not in public.model_dump()
    assert debug.debug is not None
    assert debug.debug.validated_sql == internal.validated_sql
    assert debug.debug.selected_schema_ids == ["analytics.departments"]
    assert debug.debug.semantic_definition_ids == ["annual_base_salary"]
    assert debug.debug.semantic_provider == "wren"
    assert debug.debug.semantic_retrieval_latency_ms == 4.2
    assert debug.debug.semantic_model_ids == ["wren:enterprise_analytics:departments"]
    assert debug.debug.semantic_relationship_ids == [
        "wren:enterprise_analytics:employees_departments"
    ]
    assert debug.debug.semantic_measure_ids == ["wren:payroll.net_amount"]
    assert debug.debug.sql_generation_provider == "llm"


def test_empty_result_provenance_retains_typed_columns() -> None:
    internal = build_internal_provenance(
        request_id="empty-request",
        trace_id="empty-trace",
        source=DatabaseSource(identifier="warehouse", dialect="postgres"),
        generated_sql="SELECT id FROM analytics.projects WHERE false",
        validated_sql="SELECT id FROM analytics.projects WHERE FALSE LIMIT 100",
        rows=[],
        analysis=AnalysisPlan(),
        execution=ExecutionMetadata(
            status="empty",
            row_count=0,
            duration_ms=1,
        ),
        model_aliases=["sql-reasoner"],
        result_column_metadata=[ResultColumnMetadata(name="id", data_type="integer")],
    )

    assert internal.result.columns == ["id"]
    assert internal.result.column_types == {"id": "integer"}
