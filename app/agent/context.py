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


class EntityChoice(BaseModel):
    """One canonical option offered to the user during clarification."""

    model_config = ConfigDict(extra="forbid")

    canonical_key: str
    display_value: str
    canonical_column: str = ""
    display_column: str = ""


class PendingEntityChoice(BaseModel):
    """What a clarification turn was waiting to be told.

    Recorded so the next turn can tell a *reply* from a new question without
    guessing. A reply names one of these options; anything else is a new
    request and must not inherit the previous one. Without this the answer
    "OU2100" arrived as a question of its own and the original request was
    silently abandoned.
    """

    model_config = ConfigDict(extra="forbid")

    entity_name: str
    original_question: str
    choices: list[EntityChoice] = Field(default_factory=list)


class AnalyticalContext(AnalysisPlan):
    previous_question: str
    resolved_question: str
    previous_query_id: str | None = None
    previous_result_columns: list[str] = Field(default_factory=list)
    clarification_state: Literal["none", "required", "blocked"] = "none"
    execution_route: Literal["governed_metric", "adhoc_analytics"] | None = None
    metric_query: MetricQuery | None = None
    pending_entity_choice: PendingEntityChoice | None = None


class ConversationTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    question: str
    answer: str
    query_id: str | None = None
