from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.authorization.gateway import AuthorizedScopeSummary

type Scalar = str | int | float | bool | None


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class AnalyticsRequest(StrictContract):
    question: str = Field(min_length=1)
    thread_id: str | None = Field(default=None, min_length=1)
    include_debug: bool = False


class ClaimEvidence(StrictContract):
    row_index: int = Field(ge=0)
    field: str = Field(min_length=1)
    value: Scalar


class GroundedClaim(StrictContract):
    claim: str = Field(min_length=1)
    evidence: list[ClaimEvidence] = Field(min_length=1)


class ChartSpec(StrictContract):
    chart_type: Literal["bar", "line", "pie", "donut"] = Field(alias="type")
    title: str = Field(min_length=1, max_length=200)
    x: str = Field(min_length=1)
    y: str = Field(min_length=1)
    series: str | None = None


class Freshness(StrictContract):
    status: Literal["known", "unknown"] = "unknown"
    as_of: datetime | None = None


class ResultMetadata(StrictContract):
    row_count: int = Field(ge=0)
    columns: list[str]
    column_types: dict[str, str] = Field(default_factory=dict)
    result_bytes: int = Field(default=0, ge=0)
    truncated: bool = False
    live: bool = False


class DebugProvenance(StrictContract):
    generated_sql: str | None = None
    validated_sql: str | None = None
    selected_schema_ids: list[str] = Field(default_factory=list)
    semantic_definition_ids: list[str] = Field(default_factory=list)
    semantic_provider: str = "inmemory"
    semantic_retrieval_latency_ms: float = Field(default=0, ge=0)
    semantic_model_ids: list[str] = Field(default_factory=list)
    semantic_relationship_ids: list[str] = Field(default_factory=list)
    semantic_measure_ids: list[str] = Field(default_factory=list)
    sql_generation_provider: str = "llm"
    route: str = "adhoc_analytics"
    route_reason_code: str = "adhoc_default"
    route_confidence: float = Field(default=0, ge=0, le=1)
    metric_id: str | None = None
    metric_definition_version: str | None = None
    metric_dimensions: list[str] = Field(default_factory=list)
    metric_filters: list[dict[str, Any]] = Field(default_factory=list)
    metric_provider: str | None = None
    execution_source: str = "database"
    routing_latency_ms: float = Field(default=0, ge=0)
    metric_planning_latency_ms: float = Field(default=0, ge=0)
    metric_retrieval_latency_ms: float = Field(default=0, ge=0)
    metric_execution_latency_ms: float = Field(default=0, ge=0)
    sql_validation_attempts: int = Field(default=0, ge=0)
    sql_repair_attempted: bool = False
    sql_repair_succeeded: bool = False
    initial_validation_error_code: str | None = None
    final_validation_status: str = "not_applicable"
    repair_latency_ms: float = Field(default=0, ge=0)
    sql_parse_latency_ms: float = Field(default=0, ge=0)
    sql_schema_validation_latency_ms: float = Field(default=0, ge=0)
    original_candidate_sql: str | None = None
    repaired_candidate_sql: str | None = None


class Provenance(StrictContract):
    source: str
    tables: list[str]
    columns: list[str]
    result: ResultMetadata
    executed_at: datetime | None = None
    freshness: Freshness = Field(default_factory=Freshness)
    debug: DebugProvenance | None = None


class InternalProvenance(StrictContract):
    request_id: str
    trace_id: str
    query_id: str | None = None
    source: str
    database_provider: str = "direct"
    database_dialect: str = "unknown"
    tables: list[str]
    columns: list[str]
    generated_sql: str | None = None
    validated_sql: str | None = None
    filters: dict[str, Scalar] = Field(default_factory=dict)
    time_range: dict[str, str | None] | None = None
    result: ResultMetadata
    executed_at: datetime | None = None
    freshness: Freshness = Field(default_factory=Freshness)
    model_aliases: list[str] = Field(default_factory=list)
    llm_provider: str | None = None
    llm_models: list[str] = Field(default_factory=list)
    llm_call_count: int = Field(default=0, ge=0)
    llm_prompt_tokens: int = Field(default=0, ge=0)
    llm_completion_tokens: int = Field(default=0, ge=0)
    llm_total_tokens: int = Field(default=0, ge=0)
    authenticated_subject_id: str | None = None
    authentication_provider: str | None = None
    authorization_provider: str | None = None
    authorization_decision_id: str | None = None
    authorized_scope: AuthorizedScopeSummary = Field(default_factory=AuthorizedScopeSummary)
    authorization_latency_ms: float = Field(default=0, ge=0)
    governance_provider: str = "disabled"
    governance_source_ids: list[str] = Field(default_factory=list)
    governance_owner_names: list[str] = Field(default_factory=list)
    governance_catalog_freshness_at: datetime | None = None
    governance_retrieval_latency_ms: float = Field(default=0, ge=0)
    selected_schema_ids: list[str] = Field(default_factory=list)
    semantic_definition_ids: list[str] = Field(default_factory=list)
    semantic_provider: str = "inmemory"
    semantic_retrieval_latency_ms: float = Field(default=0, ge=0)
    semantic_model_ids: list[str] = Field(default_factory=list)
    semantic_relationship_ids: list[str] = Field(default_factory=list)
    semantic_measure_ids: list[str] = Field(default_factory=list)
    sql_generation_provider: str = "llm"
    route: str = "adhoc_analytics"
    route_reason_code: str = "adhoc_default"
    route_confidence: float = Field(default=0, ge=0, le=1)
    metric_id: str | None = None
    metric_definition_version: str | None = None
    metric_dimensions: list[str] = Field(default_factory=list)
    metric_filters: list[dict[str, Any]] = Field(default_factory=list)
    metric_provider: str | None = None
    execution_source: str = "database"
    routing_latency_ms: float = Field(default=0, ge=0)
    metric_planning_latency_ms: float = Field(default=0, ge=0)
    metric_retrieval_latency_ms: float = Field(default=0, ge=0)
    metric_execution_latency_ms: float = Field(default=0, ge=0)
    sql_validation_attempts: int = Field(default=0, ge=0)
    sql_repair_attempted: bool = False
    sql_repair_succeeded: bool = False
    initial_validation_error_code: str | None = None
    final_validation_status: str = "not_applicable"
    repair_latency_ms: float = Field(default=0, ge=0)
    sql_parse_latency_ms: float = Field(default=0, ge=0)
    sql_schema_validation_latency_ms: float = Field(default=0, ge=0)
    original_candidate_sql: str | None = None
    repaired_candidate_sql: str | None = None

    def public_view(self, *, include_debug: bool = False) -> Provenance:
        debug = None
        if include_debug:
            debug = DebugProvenance(
                generated_sql=self.generated_sql,
                validated_sql=self.validated_sql,
                selected_schema_ids=self.selected_schema_ids,
                semantic_definition_ids=self.semantic_definition_ids,
                semantic_provider=self.semantic_provider,
                semantic_retrieval_latency_ms=self.semantic_retrieval_latency_ms,
                semantic_model_ids=self.semantic_model_ids,
                semantic_relationship_ids=self.semantic_relationship_ids,
                semantic_measure_ids=self.semantic_measure_ids,
                sql_generation_provider=self.sql_generation_provider,
                route=self.route,
                route_reason_code=self.route_reason_code,
                route_confidence=self.route_confidence,
                metric_id=self.metric_id,
                metric_definition_version=self.metric_definition_version,
                metric_dimensions=self.metric_dimensions,
                metric_filters=self.metric_filters,
                metric_provider=self.metric_provider,
                execution_source=self.execution_source,
                routing_latency_ms=self.routing_latency_ms,
                metric_planning_latency_ms=self.metric_planning_latency_ms,
                metric_retrieval_latency_ms=self.metric_retrieval_latency_ms,
                metric_execution_latency_ms=self.metric_execution_latency_ms,
                sql_validation_attempts=self.sql_validation_attempts,
                sql_repair_attempted=self.sql_repair_attempted,
                sql_repair_succeeded=self.sql_repair_succeeded,
                initial_validation_error_code=self.initial_validation_error_code,
                final_validation_status=self.final_validation_status,
                repair_latency_ms=self.repair_latency_ms,
                sql_parse_latency_ms=self.sql_parse_latency_ms,
                sql_schema_validation_latency_ms=self.sql_schema_validation_latency_ms,
                original_candidate_sql=self.original_candidate_sql,
                repaired_candidate_sql=self.repaired_candidate_sql,
            )
        return Provenance(
            source=self.source,
            tables=self.tables,
            columns=self.columns,
            result=self.result,
            executed_at=self.executed_at,
            freshness=self.freshness,
            debug=debug,
        )


class ExecutionMetadata(StrictContract):
    query_id: str | None = None
    status: Literal["completed", "clarification_required", "blocked", "empty"]
    row_count: int = Field(ge=0)
    duration_ms: float = Field(ge=0)
    executed_at: datetime | None = None
    result_bytes: int = Field(default=0, ge=0)
    truncated: bool = False
    live: bool = False


class AnalyticalResult(StrictContract):
    columns: list[str]
    rows: list[dict[str, Any]]
    column_types: dict[str, str] = Field(default_factory=dict)
    source_type: Literal["governed_metric", "adhoc_sql"]
    source_identifiers: list[str] = Field(default_factory=list)
    truncated: bool = False
    warnings: list[str] = Field(default_factory=list)
    execution: ExecutionMetadata


class AnalyticsResponse(StrictContract):
    schema_version: Literal["1.0"] = "1.0"
    request_id: str
    thread_id: str
    status: Literal["completed", "clarification_required", "blocked", "empty"]
    answer: str
    columns: list[str]
    rows: list[dict[str, Any]]
    chart: ChartSpec | None
    sources: list[str] = Field(default_factory=list)
    provenance: Provenance
    freshness: Freshness = Field(default_factory=Freshness)
    clarification_required: bool = False
    clarification_question: str | None = None
    warnings: list[str] = Field(default_factory=list)
    execution: ExecutionMetadata


class HealthResponse(StrictContract):
    status: Literal["ok", "ready"]
    checks: dict[str, Literal["ok", "skipped"]] = Field(default_factory=dict)
