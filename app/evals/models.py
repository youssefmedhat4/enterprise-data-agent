from typing import Literal

from pydantic import BaseModel, Field, model_validator

type Scalar = str | int | float | bool | None
type MetricValue = bool | None
type EvaluationMode = Literal["full", "sql"]
type FailureType = Literal["model", "infrastructure", "expectation"]
type InfrastructureErrorType = Literal[
    "authentication_failed",
    "permission_denied",
    "quota_exceeded",
    "payment_required",
    "connection_failed",
    "model_unavailable",
    "out_of_memory",
    "provider_unavailable",
    "structured_output_failed",
    "timeout",
    "tool_use_failed",
    "database_unavailable",
    "semantic_provider_unavailable",
    "rate_limited",
    "unknown",
]


class ResultAssertion(BaseModel):
    row_index: int = Field(ge=0)
    field: str = Field(min_length=1)
    operator: Literal["eq", "approx", "contains"] = "eq"
    expected: Scalar


class EvaluationCase(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]+$")
    question: str = Field(min_length=1)
    category: str = Field(min_length=1)
    language: Literal["en", "ar", "mixed"]
    difficulty: Literal["easy", "medium", "hard"]
    relevant_tables: list[str]
    reference_sql: str | None = None
    expected_row_count: int | None = Field(default=None, ge=0)
    assertions: list[ResultAssertion] = Field(default_factory=list)
    required_answer_terms: list[str] = Field(default_factory=list)
    numeric_tolerance: float = Field(default=0.0, ge=0)
    expected_security_behavior: Literal["allow", "block", "clarify"]

    @model_validator(mode="after")
    def validate_expectations(self) -> "EvaluationCase":
        if self.expected_security_behavior in {"allow", "block"} and not self.reference_sql:
            raise ValueError("Allowed and blocked cases require reference SQL.")
        if self.expected_security_behavior == "allow" and self.expected_row_count is None:
            raise ValueError("Allowed cases require an expected row count.")
        return self


class WorkflowMetrics(BaseModel):
    graph_completion: MetricValue
    structured_output_validity: MetricValue


class SQLMetrics(BaseModel):
    parse_validity: MetricValue
    relevant_tables: MetricValue
    safety_validation: MetricValue
    execution_success: MetricValue
    result_accuracy: MetricValue


class AnswerMetrics(BaseModel):
    answer_accuracy: MetricValue
    numeric_grounding: MetricValue
    provenance_completeness: MetricValue
    unsupported_claim_failures: int = Field(ge=0)


class SecurityMetrics(BaseModel):
    blocked_mutation_attempts: MetricValue
    adversarial_case_outcomes: MetricValue
    clarification_behavior: MetricValue


class PerformanceMetrics(BaseModel):
    llm_latency_ms: float = Field(ge=0)
    database_latency_ms: float = Field(ge=0)
    total_latency_ms: float = Field(ge=0)
    retry_count: int | None = Field(default=None, ge=0)
    llm_call_count: int = Field(default=0, ge=0)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    usage_available_calls: int = Field(default=0, ge=0)
    cached_tokens: int | None = Field(default=None, ge=0)
    cached_tokens_available_calls: int = Field(default=0, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    cost_available_calls: int = Field(default=0, ge=0)
    model_calls: dict[str, int] = Field(default_factory=dict)
    provider_calls: dict[str, int] = Field(default_factory=dict)


class ProviderErrorDiagnostic(BaseModel):
    exception_type: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    http_status: int | None = Field(default=None, ge=100, le=599)
    provider_code: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    category: InfrastructureErrorType


class ResultComparisonDiagnostic(BaseModel):
    passed: bool
    reason: str
    ordering_required: bool
    normalized_actual: list[dict[str, Scalar]] = Field(default_factory=list)
    normalized_expected: list[dict[str, Scalar]] = Field(default_factory=list)


class EvaluationResult(BaseModel):
    case_id: str
    category: str
    difficulty: str
    language: str
    expected_security_behavior: str
    passed: bool
    workflow: WorkflowMetrics
    sql: SQLMetrics
    answer: AnswerMetrics
    security: SecurityMetrics
    performance: PerformanceMetrics
    generated_sql: str | None = None
    error: str | None = None
    failure_type: FailureType | None = None
    infrastructure_error: InfrastructureErrorType | None = None
    provider_error: ProviderErrorDiagnostic | None = None
    failed_metrics: list[str] = Field(default_factory=list)
    structured_action: Literal["execute", "clarify", "block"] | None = None
    selected_schema_ids: list[str] = Field(default_factory=list)
    semantic_provider: str = "inmemory"
    semantic_model_ids: list[str] = Field(default_factory=list)
    semantic_relationship_ids: list[str] = Field(default_factory=list)
    semantic_definition_ids: list[str] = Field(default_factory=list)
    semantic_measure_ids: list[str] = Field(default_factory=list)
    semantic_selection_reasons: dict[str, list[str]] = Field(default_factory=dict)
    semantic_retrieval_latency_ms: float = Field(default=0, ge=0)
    semantic_context_size_chars: int = Field(default=0, ge=0)
    missing_required_context: list[str] = Field(default_factory=list)
    irrelevant_context: list[str] = Field(default_factory=list)
    result_comparison: ResultComparisonDiagnostic | None = None
    sanitized_structured_output: dict[str, Scalar] | None = None
    actual_provider: str | None = None
    actual_model: str | None = None


class MetricAggregate(BaseModel):
    applicable: int
    passed: int
    accuracy: float | None


class PerformanceSummary(BaseModel):
    average_llm_latency_ms: float
    p50_llm_latency_ms: float
    p95_llm_latency_ms: float
    average_database_latency_ms: float
    average_total_latency_ms: float
    p50_total_latency_ms: float
    p95_total_latency_ms: float
    total_retries: int | None
    llm_call_count: int
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    usage_available_calls: int
    cached_tokens: int | None
    cached_tokens_available_calls: int
    total_cost_usd: float | None
    cost_available_calls: int
    model_calls: dict[str, int]
    provider_calls: dict[str, int]


class DimensionSummary(BaseModel):
    total: int
    passed: int
    accuracy: float


class SemanticContextSummary(BaseModel):
    average_selected_tables: float = Field(default=0, ge=0)
    average_selected_models: float = Field(default=0, ge=0)
    average_relationships: float = Field(default=0, ge=0)
    average_definitions: float = Field(default=0, ge=0)
    average_measures: float = Field(default=0, ge=0)
    average_context_size_chars: float = Field(default=0, ge=0)
    average_retrieval_latency_ms: float = Field(default=0, ge=0)
    p50_retrieval_latency_ms: float = Field(default=0, ge=0)
    p95_retrieval_latency_ms: float = Field(default=0, ge=0)
    missing_required_context_cases: int = Field(default=0, ge=0)
    irrelevant_context_cases: int = Field(default=0, ge=0)


class EvaluationSummary(BaseModel):
    evaluator_version: str = "1.0"
    backend: Literal["fake", "duckdb", "postgres"]
    llm_backend: Literal["deterministic", "configured"]
    semantic_provider: Literal["inmemory", "wren"] = "inmemory"
    evaluation_mode: EvaluationMode = "full"
    dataset_sha256: str | None = None
    configured_models: dict[str, str] = Field(default_factory=dict)
    total_cases: int
    passed_cases: int
    failed_cases: int
    scored_cases: int
    infrastructure_failures: int
    model_failures: int
    infrastructure_errors: dict[str, int] = Field(default_factory=dict)
    pass_rate: float
    workflow: dict[str, MetricAggregate]
    sql: dict[str, MetricAggregate]
    answer: dict[str, MetricAggregate | int]
    security: dict[str, MetricAggregate]
    performance: PerformanceSummary
    semantic: SemanticContextSummary = Field(default_factory=SemanticContextSummary)
    by_category: dict[str, DimensionSummary]
    by_difficulty: dict[str, DimensionSummary]
    by_language: dict[str, DimensionSummary]
    results: list[EvaluationResult]
