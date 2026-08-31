"""Metric intent planning.

Replaces literal alias matching as the governed routing decision. The old path
scanned a question for configured alias substrings and routed on a hit, which
made routing a property of wording rather than of meaning.

The new shape is: retrieve authorized candidates, hand the model *only* those
candidates, and have it select among them. Two properties make this safe.

First, the model selects rather than authors. It receives a closed list of
metric keys and dimension keys and may return only those; anything else is
rejected by `MetricIntentValidator` before it can reach execution. The model
cannot invent a metric, a dimension, a filter member, a table, or an entity id,
because there is no field in the contract capable of carrying one and every
returned identifier is checked against the supplied candidates.

Second, validation is not the model's job. `MetricSelection` constrains shape;
the validator constrains content against what the backend actually offered.
A well-formed selection naming an unknown metric is still refused.

Deterministic write-intent blocking stays ahead of this. A question asking to
delete rows must never reach a planner that could route it anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.knowledge.metrics import RegisteredMetric
from app.knowledge.retrieval import MetricCandidate


class MetricIntentError(RuntimeError):
    """Raised when a model's selection cannot be trusted."""


class MetricSelection(BaseModel):
    """Structured intent returned by the model.

    Every field is either a closed enum or a list of identifiers validated
    against backend-supplied candidates. There is deliberately no field able to
    carry SQL, an expression, a table name, or a formula.
    """

    model_config = ConfigDict(extra="forbid")

    intent: Literal["governed", "adhoc", "clarify"]
    metrics: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    filters: list[MetricFilterSelection] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""
    clarification_question: str | None = None


class MetricFilterSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: str = Field(min_length=1)
    operator: Literal["eq", "neq", "in", "not_in"] = "eq"
    #: Free text as the user phrased it. Resolved to real values by
    #: EntityResolver against the datasource; never used as a value directly.
    value_text: str = Field(min_length=1)


MetricSelection.model_rebuild()


@dataclass(frozen=True, slots=True)
class ValidatedMetricPlan:
    """A selection proven to reference only offered, authorized members."""

    data_source_id: UUID
    metrics: tuple[RegisteredMetric, ...]
    dimensions: tuple[str, ...]
    filters: tuple[MetricFilterSelection, ...]
    confidence: float

    @property
    def metric_keys(self) -> tuple[str, ...]:
        return tuple(metric.metric_key for metric in self.metrics)

    @property
    def is_multi_metric(self) -> bool:
        return len(self.metrics) > 1


class MetricIntentValidator:
    """Checks a model selection against what the backend actually offered.

    This is the authority. The model proposes; this decides.
    """

    def validate(
        self,
        *,
        selection: MetricSelection,
        data_source_id: UUID,
        candidates: list[MetricCandidate],
    ) -> ValidatedMetricPlan:
        if selection.intent != "governed":
            raise MetricIntentError(
                f"Only a governed selection can be planned, got {selection.intent!r}."
            )
        if not selection.metrics:
            raise MetricIntentError("A governed selection must name at least one metric.")

        offered = {candidate.metric_key: candidate.metric for candidate in candidates}
        chosen: list[RegisteredMetric] = []
        for key in selection.metrics:
            metric = offered.get(key)
            if metric is None:
                # The model named something it was not offered. This is the
                # invented-identifier case and is always fatal.
                raise MetricIntentError(
                    f"Metric {key!r} was not among the offered candidates."
                )
            if metric.data_source_id != data_source_id:
                raise MetricIntentError(
                    f"Metric {key!r} belongs to a different datasource."
                )
            if not metric.is_governed_runtime_visible:
                raise MetricIntentError(f"Metric {key!r} is not certified.")
            if metric not in chosen:
                chosen.append(metric)

        allowed_dimensions = _shared_dimensions(chosen)
        for dimension in selection.dimensions:
            if dimension not in allowed_dimensions:
                raise MetricIntentError(
                    f"Dimension {dimension!r} is not available on every selected metric."
                )
        for filter_selection in selection.filters:
            if filter_selection.dimension not in allowed_dimensions:
                raise MetricIntentError(
                    f"Filter dimension {filter_selection.dimension!r} is not available."
                )

        return ValidatedMetricPlan(
            data_source_id=data_source_id,
            metrics=tuple(chosen),
            dimensions=tuple(dict.fromkeys(selection.dimensions)),
            filters=tuple(selection.filters),
            confidence=selection.confidence,
        )


def _shared_dimensions(metrics: list[RegisteredMetric]) -> set[str]:
    """Dimensions available on *every* selected metric.

    A composite plan can only group by something all its measures share.
    Intersecting here is what stops a multi-metric request from silently
    producing a grain one of its measures cannot express — the shape that
    caused the earlier fan-out bug.
    """
    if not metrics:
        return set()
    shared: set[str] | None = None
    for metric in metrics:
        keys = {dimension.dimension_key for dimension in metric.dimensions}
        shared = keys if shared is None else shared & keys
    return shared or set()


def candidate_prompt_payload(candidates: list[MetricCandidate]) -> list[dict[str, object]]:
    """The closed list of options shown to the model.

    Only keys, human descriptions, and dimension keys. No SQL, no physical
    table names, no formulas — the model has no need for them and giving it
    them would invite it to author execution rather than select intent.
    """
    return [
        {
            "metric_key": candidate.metric_key,
            "display_name": candidate.metric.display_name,
            "description": candidate.metric.description,
            "business_meaning": candidate.metric.business_meaning,
            "grain": candidate.metric.grain,
            "unit": candidate.metric.unit,
            "dimensions": [
                dimension.dimension_key for dimension in candidate.metric.dimensions
            ],
        }
        for candidate in candidates
    ]
