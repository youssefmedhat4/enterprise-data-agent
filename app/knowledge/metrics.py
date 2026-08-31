"""Datasource-scoped metric registry.

Replaces the module-level `GOVERNED_METRICS` tuple as the runtime source of
truth. That tuple described exactly one database; a registry keyed by datasource
lets each database carry its own certified definitions, and lets a definition be
proposed, versioned, certified, and deprecated rather than only existing or not.

Only CERTIFIED metrics are visible to governed runtime. A metric Gemini proposed
is PROPOSED and stays invisible until a human approves it and validation passes.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import Field

from app.knowledge.contracts import StrictContract


class MetricStatus(StrEnum):
    PROPOSED = "PROPOSED"
    CERTIFIED = "CERTIFIED"
    REJECTED = "REJECTED"
    DEPRECATED = "DEPRECATED"
    STALE = "STALE"


class MetricDimensionSpec(StrictContract):
    dimension_key: str = Field(min_length=1)
    display_name: str = ""
    description: str = ""
    data_type: str = "string"
    is_time_dimension: bool = False
    allowed_operators: tuple[str, ...] = ()
    #: Confirmed semantic attribute backing this dimension. When set,
    #: EntityResolver resolves values against that real column.
    semantic_attribute_id: UUID | None = None


class RegisteredMetric(StrictContract):
    """A governed metric definition owned by one datasource."""

    id: UUID = Field(default_factory=uuid4)
    data_source_id: UUID
    metric_key: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    description: str = ""
    business_meaning: str = ""
    version: int = Field(default=1, ge=1)
    status: MetricStatus = MetricStatus.PROPOSED
    #: Semantic expression over confirmed concepts. Never executed as SQL.
    semantic_expression: str | None = None
    grain: str | None = None
    unit: str | None = None
    null_behavior: str | None = None
    owner: str | None = None
    dimensions: tuple[MetricDimensionSpec, ...] = ()
    #: Free-text meanings used to build retrieval documents. These are not
    #: routing aliases and carry no matching authority of their own.
    concepts: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    example_questions: tuple[str, ...] = ()
    approved_at: datetime | None = None
    approved_by: str | None = None

    @property
    def is_governed_runtime_visible(self) -> bool:
        return self.status is MetricStatus.CERTIFIED

    def retrieval_document(self) -> str:
        """Rich text describing what this metric means, for embedding.

        Deliberately verbose and prose-like. Retrieval has to match a question
        phrased in business language against a definition written in business
        language, so the document leads with meaning rather than with the
        metric key, and includes approved example questions because those are
        the closest thing to how a real person would ask.
        """
        lines = [
            f"Metric: {self.display_name}",
            f"Description: {self.description}" if self.description else "",
            f"Business meaning: {self.business_meaning}" if self.business_meaning else "",
            f"Grain: {self.grain}" if self.grain else "",
            f"Unit: {self.unit}" if self.unit else "",
        ]
        if self.dimensions:
            names = ", ".join(
                dimension.display_name or dimension.dimension_key
                for dimension in self.dimensions
            )
            lines.append(f"Dimensions: {names}")
        if self.concepts:
            lines.append(f"Concepts: {', '.join(self.concepts)}")
        if self.example_questions:
            lines.append("Example questions:")
            lines.extend(f"- {question}" for question in self.example_questions)
        return "\n".join(line for line in lines if line)


class MetricRegistry(Protocol):
    """Datasource-scoped storage for governed metric definitions."""

    async def certified(self, data_source_id: UUID) -> list[RegisteredMetric]:
        """Every CERTIFIED metric for one datasource."""
        ...

    async def get(
        self, data_source_id: UUID, metric_key: str
    ) -> RegisteredMetric | None: ...

    async def upsert(self, metric: RegisteredMetric) -> RegisteredMetric: ...

    async def set_status(
        self,
        data_source_id: UUID,
        metric_key: str,
        status: MetricStatus,
        *,
        approved_by: str | None = None,
    ) -> RegisteredMetric: ...


class InMemoryMetricRegistry:
    """Reference implementation used by tests and single-process runs.

    Keyed by `(data_source_id, metric_key)`, so isolation is structural here as
    it is in the database: a lookup cannot reach another datasource's metric
    because the datasource is part of the key.
    """

    def __init__(self, metrics: list[RegisteredMetric] | None = None) -> None:
        self._metrics: dict[tuple[UUID, str], RegisteredMetric] = {}
        for metric in metrics or []:
            self._metrics[(metric.data_source_id, metric.metric_key)] = metric

    async def certified(self, data_source_id: UUID) -> list[RegisteredMetric]:
        return sorted(
            (
                metric
                for (source, _), metric in self._metrics.items()
                if source == data_source_id and metric.is_governed_runtime_visible
            ),
            key=lambda metric: metric.metric_key,
        )

    async def all_for(self, data_source_id: UUID) -> list[RegisteredMetric]:
        """Every metric regardless of status, for administration surfaces."""
        return sorted(
            (
                metric
                for (source, _), metric in self._metrics.items()
                if source == data_source_id
            ),
            key=lambda metric: (metric.status.value, metric.metric_key),
        )

    async def get(
        self, data_source_id: UUID, metric_key: str
    ) -> RegisteredMetric | None:
        return self._metrics.get((data_source_id, metric_key))

    async def upsert(self, metric: RegisteredMetric) -> RegisteredMetric:
        self._metrics[(metric.data_source_id, metric.metric_key)] = metric
        return metric

    async def set_status(
        self,
        data_source_id: UUID,
        metric_key: str,
        status: MetricStatus,
        *,
        approved_by: str | None = None,
    ) -> RegisteredMetric:
        existing = self._metrics.get((data_source_id, metric_key))
        if existing is None:
            raise KeyError(f"Metric {metric_key!r} is not registered for this datasource.")
        updated = existing.model_copy(
            update={
                "status": status,
                "approved_by": approved_by,
                "approved_at": datetime.now().astimezone()
                if status is MetricStatus.CERTIFIED
                else existing.approved_at,
            }
        )
        self._metrics[(data_source_id, metric_key)] = updated
        return updated
