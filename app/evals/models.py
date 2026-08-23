from typing import Literal

from pydantic import BaseModel, Field, model_validator

type Scalar = str | int | float | bool | None
type MetricValue = bool | None


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


class PerformanceMetrics(BaseModel):
    llm_latency_ms: float = Field(ge=0)
    database_latency_ms: float = Field(ge=0)
    total_latency_ms: float = Field(ge=0)
    retry_count: int | None = Field(default=None, ge=0)


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


class MetricAggregate(BaseModel):
    applicable: int
    passed: int
    accuracy: float | None


class PerformanceSummary(BaseModel):
    average_llm_latency_ms: float
    average_database_latency_ms: float
    average_total_latency_ms: float
    total_retries: int | None


class DimensionSummary(BaseModel):
    total: int
    passed: int
    accuracy: float


class EvaluationSummary(BaseModel):
    backend: Literal["fake", "duckdb", "postgres"]
    total_cases: int
    passed_cases: int
    failed_cases: int
    pass_rate: float
    workflow: dict[str, MetricAggregate]
    sql: dict[str, MetricAggregate]
    answer: dict[str, MetricAggregate | int]
    security: dict[str, MetricAggregate]
    performance: PerformanceSummary
    by_category: dict[str, DimensionSummary]
    by_difficulty: dict[str, DimensionSummary]
    by_language: dict[str, DimensionSummary]
    results: list[EvaluationResult]
