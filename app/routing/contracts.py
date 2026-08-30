from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.metrics.gateway import MetricQuery


class QueryRoute(StrEnum):
    GOVERNED_METRIC = "governed_metric"
    ADHOC_ANALYTICS = "adhoc_analytics"
    CLARIFY = "clarify"
    BLOCK = "block"


class RouteReasonCode(StrEnum):
    WRITE_INTENT = "write_intent"
    FOLLOWUP_REFERENCE = "followup_reference"
    FOLLOWUP_METRIC_SWITCH = "followup_metric_switch"
    FOLLOWUP_WITHOUT_CONTEXT = "followup_without_context"
    METRIC_EXACT_MATCH = "metric_exact_match"
    METRIC_SEMANTIC_MATCH = "metric_semantic_match"
    MULTIPLE_METRICS_UNSUPPORTED = "multiple_metrics_unsupported"
    COMPOSITE_METRIC_REQUEST = "composite_metric_request"
    INSUFFICIENT_INFORMATION = "insufficient_information"
    UNAUTHORIZED_METRIC = "unauthorized_metric"
    ROW_LEVEL_LOOKUP = "row_level_lookup"
    ADHOC_DEFAULT = "adhoc_default"


class RouteDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    route: QueryRoute
    confidence: float = Field(ge=0, le=1)
    reason_code: RouteReasonCode
    metric_candidates: tuple[str, ...] = ()
    requires_prior_context: bool = False
    clarification_reason: str | None = None
    clarification_question: str | None = None
    block_reason: str | None = None


class MetricRequestPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: MetricQuery
    planning_latency_ms: float = Field(ge=0)
    used_prior_context: bool = False


class QueryRouterError(RuntimeError):
    """Raised when routing cannot produce a safe typed decision."""


class MetricPlanningError(RuntimeError):
    """Raised when a governed request cannot be represented safely."""
