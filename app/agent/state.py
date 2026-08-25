from operator import add
from typing import Annotated, Any, TypedDict

from app.agent.context import AnalysisPlan, AnalyticalContext, ConversationTurn
from app.authentication.gateway import UserIdentity
from app.authorization.gateway import AuthorizationDecision, AuthorizedScopeSummary
from app.contracts.analytics import (
    AnalyticalResult,
    ChartSpec,
    ExecutionMetadata,
    GroundedClaim,
    InternalProvenance,
)
from app.data.gateway import DatabaseQueryResult, TableMetadata
from app.governance.gateway import GovernanceSnapshot
from app.metrics.gateway import MetricQuery, MetricResult
from app.routing.contracts import RouteDecision
from app.security.sql_validation import SQLValidationResult
from app.semantic.gateway import SemanticDefinition, SemanticMeasure


class AgentState(TypedDict, total=False):
    request_id: str
    trace_id: str
    thread_id: str | None
    question: str
    user_identity: UserIdentity
    authorization_decision: AuthorizationDecision
    authorized_scope: AuthorizedScopeSummary
    authorized_metric_ids: frozenset[str]
    authorization_latency_ms: float
    resolved_question: str
    retrieved_metadata: list[TableMetadata]
    discovered_metadata: list[TableMetadata]
    available_metadata: list[TableMetadata]
    selected_schema_ids: list[str]
    semantic_definitions: list[SemanticDefinition]
    semantic_definition_ids: list[str]
    semantic_measures: list[SemanticMeasure]
    semantic_measure_ids: list[str]
    semantic_provider: str
    semantic_retrieval_latency_ms: float
    semantic_model_ids: list[str]
    semantic_relationship_ids: list[str]
    semantic_selection_reasons: dict[str, tuple[str, ...]]
    semantic_context_size_chars: int
    governance_snapshot: GovernanceSnapshot
    sql_generation_provider: str
    model_action: str
    generated_sql: str | None
    needs_clarification: bool
    clarification_question: str | None
    block_reason: str | None
    validated_sql: str | None
    query_result: list[dict[str, Any]]
    database_result: DatabaseQueryResult
    final_answer: str
    claims: list[GroundedClaim]
    chart_spec: ChartSpec | None
    internal_provenance: InternalProvenance
    warnings: list[str]
    execution_metadata: ExecutionMetadata
    analysis_plan: AnalysisPlan
    analytical_context: AnalyticalContext
    conversation_turns: Annotated[list[ConversationTurn], add]
    query_id: str | None
    errors: list[str]
    model_route: str
    route_decision: RouteDecision
    execution_route: str
    routing_latency_ms: float
    metric_query: MetricQuery
    metric_result: MetricResult
    metric_planning_latency_ms: float
    analytical_result: AnalyticalResult
    sql_validation_result: SQLValidationResult
    sql_validation_attempts: int
    sql_repair_attempted: bool
    sql_repair_succeeded: bool
    initial_validation_error_code: str | None
    final_validation_status: str
    repair_latency_ms: float
    sql_parse_latency_ms: float
    sql_schema_validation_latency_ms: float
    original_candidate_sql: str | None
    repaired_candidate_sql: str | None
