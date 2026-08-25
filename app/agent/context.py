from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.contracts.analytics import Scalar
from app.metrics.gateway import MetricQuery


class TimeRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: str | None = None
    end: str | None = None
    label: str | None = None


class AnalysisPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str = "analytics_query"
    metric: str | None = None
    dimensions: list[str] = Field(default_factory=list)
    filters: dict[str, Scalar] = Field(default_factory=dict)
    time_range: TimeRange | None = None
    entities: list[str] = Field(default_factory=list)


class AnalyticalContext(AnalysisPlan):
    previous_question: str
    resolved_question: str
    previous_query_id: str | None = None
    previous_result_columns: list[str] = Field(default_factory=list)
    clarification_state: Literal["none", "required", "blocked"] = "none"
    execution_route: Literal["governed_metric", "adhoc_analytics"] | None = None
    metric_query: MetricQuery | None = None


class ConversationTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    question: str
    answer: str
    query_id: str | None = None
