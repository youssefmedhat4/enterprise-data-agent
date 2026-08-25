from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MetricFilterOperator(StrEnum):
    EQ = "eq"
    NEQ = "neq"
    IN = "in"
    NOT_IN = "not_in"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    CONTAINS = "contains"
    STARTS_WITH = "starts_with"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"


class MetricTimeGrain(StrEnum):
    YEAR = "year"
    QUARTER = "quarter"
    MONTH = "month"
    WEEK = "week"
    DAY = "day"


class MetricOrderDirection(StrEnum):
    ASC = "asc"
    DESC = "desc"


class MetricOrder(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    member: str = Field(min_length=1)
    direction: MetricOrderDirection


class MetricFilter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dimension: str = Field(min_length=1)
    operator: MetricFilterOperator
    values: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_values(self) -> MetricFilter:
        null_operators = {
            MetricFilterOperator.IS_NULL,
            MetricFilterOperator.IS_NOT_NULL,
        }
        multiple_value_operators = {
            MetricFilterOperator.IN,
            MetricFilterOperator.NOT_IN,
        }
        if self.operator in null_operators and self.values:
            raise ValueError(f"{self.operator} does not accept values.")
        if self.operator not in null_operators and not self.values:
            raise ValueError(f"{self.operator} requires at least one value.")
        if self.operator not in null_operators | multiple_value_operators and len(self.values) != 1:
            raise ValueError(f"{self.operator} requires exactly one value.")
        return self


class MetricQuery(BaseModel):
    """Provider-independent governed metric request.

    The schema intentionally has no SQL or expression field. Callers may select
    governed members, but cannot replace a metric's certified formula. Date ranges
    are inclusive at both ends; adapters normalize provider-specific interval rules.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    metric: str = Field(min_length=1)
    dimensions: tuple[str, ...] = ()
    filters: tuple[MetricFilter, ...] = ()
    time_dimension: str | None = None
    time_grain: MetricTimeGrain | None = None
    date_range: tuple[date, date] | None = None
    order: tuple[MetricOrder, ...] = ()
    limit: int = Field(default=100, ge=1, le=100)

    @model_validator(mode="after")
    def validate_time_query(self) -> MetricQuery:
        if (self.time_grain is not None or self.date_range is not None) and not self.time_dimension:
            raise ValueError("time_dimension is required for a time grain or date range.")
        if self.date_range is not None and self.date_range[0] > self.date_range[1]:
            raise ValueError("date_range start must not be after its end.")
        if len(set(self.dimensions)) != len(self.dimensions):
            raise ValueError("dimensions must not contain duplicates.")
        return self


class MetricDimensionDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    description: str
    data_type: str
    aliases: tuple[str, ...] = ()
    allowed_operators: tuple[MetricFilterOperator, ...]


class MetricTimeDimensionDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    description: str
    data_type: str = "date"
    allowed_grains: tuple[MetricTimeGrain, ...] = tuple(MetricTimeGrain)


class MetricDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    definition_id: str
    version: str
    description: str
    aliases: tuple[str, ...]
    grain: str
    formula: str
    source_models: tuple[str, ...]
    source_tables: tuple[str, ...]
    dimensions: tuple[MetricDimensionDefinition, ...]
    time_dimensions: tuple[MetricTimeDimensionDefinition, ...] = ()
    null_behavior: str
    unit: str


class MetricResultProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_provider: str
    metric_id: str
    metric_definition_id: str
    metric_version: str
    dimensions: tuple[str, ...]
    filters: tuple[MetricFilter, ...]
    time_dimension: str | None
    time_grain: MetricTimeGrain | None
    date_range: tuple[date, date] | None
    order: tuple[MetricOrder, ...]
    source_models: tuple[str, ...]
    source_tables: tuple[str, ...]
    query_id: str
    retrieved_at: datetime
    metric_retrieval_latency_ms: float = Field(ge=0)
    metric_execution_latency_ms: float = Field(ge=0)
    generated_sql: str | None = None


class MetricResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    columns: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]
    provenance: MetricResultProvenance


class MetricGatewayError(RuntimeError):
    """Base error for governed metric providers."""


class MetricProviderUnavailableError(MetricGatewayError):
    """Raised when the selected metric provider cannot serve the request."""


class MetricQueryValidationError(MetricGatewayError):
    """Raised when a request references a non-governed metric member."""


class MetricExecutionError(MetricGatewayError):
    """Raised when a provider rejects a valid governed metric query."""


class MetricGateway(Protocol):
    async def list_metrics(self) -> tuple[MetricDefinition, ...]:
        """List governed metrics exposed by this provider."""

    async def describe_metric(self, metric_id: str) -> MetricDefinition:
        """Describe one governed metric or raise a typed validation error."""

    async def query_metric(self, query: MetricQuery) -> MetricResult:
        """Execute one structured governed metric request."""

    async def health_check(self) -> bool:
        """Return whether the configured provider is reachable."""

    async def close(self) -> None:
        """Release provider resources."""
