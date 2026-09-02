from datetime import datetime

from sqlglot import expressions as exp
from sqlglot import parse_one

from app.agent.context import AnalysisPlan
from app.authorization.gateway import AuthorizedScopeSummary
from app.contracts.analytics import (
    ExecutionMetadata,
    Freshness,
    InternalProvenance,
    ResultMetadata,
)
from app.data.gateway import DatabaseSource, ResultColumnMetadata


def build_internal_provenance(
    *,
    request_id: str,
    trace_id: str,
    source: DatabaseSource,
    generated_sql: str | None,
    validated_sql: str | None,
    rows: list[dict[str, object]],
    analysis: AnalysisPlan,
    execution: ExecutionMetadata,
    model_aliases: list[str],
    authenticated_subject_id: str | None = None,
    authentication_provider: str | None = None,
    authorization_provider: str | None = None,
    authorization_decision_id: str | None = None,
    authorized_scope: AuthorizedScopeSummary | None = None,
    authorization_latency_ms: float = 0,
    governance_provider: str = "disabled",
    governance_source_ids: list[str] | None = None,
    governance_owner_names: list[str] | None = None,
    governance_catalog_freshness_at: datetime | None = None,
    governance_retrieval_latency_ms: float = 0,
    result_column_metadata: list[ResultColumnMetadata] | None = None,
    selected_schema_ids: list[str] | None = None,
    semantic_definition_ids: list[str] | None = None,
    semantic_provider: str = "inmemory",
    semantic_retrieval_latency_ms: float = 0,
    semantic_model_ids: list[str] | None = None,
    semantic_relationship_ids: list[str] | None = None,
    semantic_measure_ids: list[str] | None = None,
    applied_instruction_ids: list[str] | None = None,
    applied_instruction_titles: list[str] | None = None,
    applied_example_ids: list[str] | None = None,
    sql_generation_provider: str = "llm",
    route: str = "adhoc_analytics",
    route_reason_code: str = "adhoc_default",
    route_confidence: float = 0,
    metric_id: str | None = None,
    metric_definition_version: str | None = None,
    metric_dimensions: list[str] | None = None,
    metric_filters: list[dict[str, object]] | None = None,
    metric_provider: str | None = None,
    execution_source: str = "database",
    routing_latency_ms: float = 0,
    metric_planning_latency_ms: float = 0,
    metric_retrieval_latency_ms: float = 0,
    metric_execution_latency_ms: float = 0,
    source_tables: list[str] | None = None,
    sql_validation_attempts: int = 0,
    sql_repair_attempted: bool = False,
    sql_repair_succeeded: bool = False,
    initial_validation_error_code: str | None = None,
    final_validation_status: str = "not_applicable",
    repair_latency_ms: float = 0,
    sql_parse_latency_ms: float = 0,
    sql_schema_validation_latency_ms: float = 0,
    original_candidate_sql: str | None = None,
    repaired_candidate_sql: str | None = None,
) -> InternalProvenance:
    result_columns = (
        list(rows[0])
        if rows
        else [column.name for column in (result_column_metadata or [])]
    )
    tables, sql_columns = _sql_sources(validated_sql)
    if source_tables is not None:
        tables = sorted(source_tables)
    time_range = analysis.time_range.model_dump() if analysis.time_range is not None else None
    return InternalProvenance(
        request_id=request_id,
        trace_id=trace_id,
        query_id=execution.query_id,
        source=source.identifier,
        database_provider=source.provider,
        database_dialect=source.dialect,
        tables=tables,
        columns=sorted(set(result_columns) | sql_columns),
        generated_sql=generated_sql,
        validated_sql=validated_sql,
        filters=analysis.filters,
        time_range=time_range,
        result=ResultMetadata(
            row_count=len(rows),
            columns=result_columns,
            column_types={
                column.name: column.data_type for column in (result_column_metadata or [])
            },
            result_bytes=execution.result_bytes,
            truncated=execution.truncated,
            live=execution.live,
        ),
        executed_at=execution.executed_at,
        freshness=Freshness(
            status="known" if source.freshness_as_of is not None else "unknown",
            as_of=source.freshness_as_of,
        ),
        model_aliases=model_aliases,
        authenticated_subject_id=authenticated_subject_id,
        authentication_provider=authentication_provider,
        authorization_provider=authorization_provider,
        authorization_decision_id=authorization_decision_id,
        authorized_scope=authorized_scope or AuthorizedScopeSummary(),
        authorization_latency_ms=authorization_latency_ms,
        governance_provider=governance_provider,
        governance_source_ids=governance_source_ids or [],
        governance_owner_names=governance_owner_names or [],
        governance_catalog_freshness_at=governance_catalog_freshness_at,
        governance_retrieval_latency_ms=governance_retrieval_latency_ms,
        selected_schema_ids=selected_schema_ids or [],
        semantic_definition_ids=semantic_definition_ids or [],
        semantic_provider=semantic_provider,
        semantic_retrieval_latency_ms=semantic_retrieval_latency_ms,
        semantic_model_ids=semantic_model_ids or [],
        semantic_relationship_ids=semantic_relationship_ids or [],
        semantic_measure_ids=semantic_measure_ids or [],
        applied_instruction_ids=applied_instruction_ids or [],
        applied_instruction_titles=applied_instruction_titles or [],
        applied_example_ids=applied_example_ids or [],
        sql_generation_provider=sql_generation_provider,
        route=route,
        route_reason_code=route_reason_code,
        route_confidence=route_confidence,
        metric_id=metric_id,
        metric_definition_version=metric_definition_version,
        metric_dimensions=metric_dimensions or [],
        metric_filters=metric_filters or [],
        metric_provider=metric_provider,
        execution_source=execution_source,
        routing_latency_ms=routing_latency_ms,
        metric_planning_latency_ms=metric_planning_latency_ms,
        metric_retrieval_latency_ms=metric_retrieval_latency_ms,
        metric_execution_latency_ms=metric_execution_latency_ms,
        sql_validation_attempts=sql_validation_attempts,
        sql_repair_attempted=sql_repair_attempted,
        sql_repair_succeeded=sql_repair_succeeded,
        initial_validation_error_code=initial_validation_error_code,
        final_validation_status=final_validation_status,
        repair_latency_ms=repair_latency_ms,
        sql_parse_latency_ms=sql_parse_latency_ms,
        sql_schema_validation_latency_ms=sql_schema_validation_latency_ms,
        original_candidate_sql=original_candidate_sql,
        repaired_candidate_sql=repaired_candidate_sql,
    )


def _sql_sources(sql: str | None) -> tuple[list[str], set[str]]:
    if not sql:
        return [], set()
    statement = parse_one(sql, read="postgres")
    cte_names = {cte.alias_or_name for cte in statement.find_all(exp.CTE)}
    tables = sorted(
        {
            f"{table.db}.{table.name}"
            for table in statement.find_all(exp.Table)
            if table.db and table.name not in cte_names
        }
    )
    columns = {column.name for column in statement.find_all(exp.Column)}
    return tables, columns
