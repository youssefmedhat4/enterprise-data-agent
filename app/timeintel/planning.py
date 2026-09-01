"""Deciding, for one request, what period it covers and which column carries it.

This is the seam between language and calendar. It reads the phrase, picks the
temporal column from confirmed mappings, resolves the boundaries from the
datasource's own policy, and hands the planner exact instants. No model call
happens here: a question with a time phrase costs exactly what it cost before.

Three outcomes, and the difference between them is the point:

* **resolved** -- a period and a column, ready to constrain a query;
* **clarify** -- the phrase is clear but the column is not, because the tables
  hold several dates and nobody said which one a question means;
* **unsupported** -- the period cannot be honoured, because the calendar is
  unconfirmed or the metric describes a moment rather than a stretch of time.

None of them is "answer anyway without the filter", which is the outcome worth
preventing: correct SQL over all of history looks exactly like a right answer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from app.timeintel.clock import Clock
from app.timeintel.dimensions import TemporalDimension, choose
from app.timeintel.intent import TimeIntent
from app.timeintel.parser import parse_time_phrase
from app.timeintel.policy import TimePolicy
from app.timeintel.resolver import ResolvedTimePlan, TimeResolutionError, resolve

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TimePlanning:
    """What this request should do about time."""

    plan: ResolvedTimePlan | None = None
    dimension: TemporalDimension | None = None
    clarification: str | None = None
    unsupported: str | None = None

    @property
    def is_temporal(self) -> bool:
        return self.plan is not None

    @property
    def needs_attention(self) -> bool:
        return self.clarification is not None or self.unsupported is not None


def plan_time(
    question: str,
    *,
    policy: TimePolicy,
    dimensions: list[TemporalDimension],
    tables: set[str],
    clock: Clock,
    inherited: TimeIntent | None = None,
    metric_behavior: str | None = None,
    metric_dimension_id: UUID | None = None,
) -> TimePlanning:
    """Work out the period, or say honestly why it cannot be worked out.

    `inherited` carries a period from earlier in the conversation, so "compare
    it with last year" keeps the period the previous turn established instead of
    silently becoming a question about all of history.
    """
    intent = _intent_for(question, inherited)
    if intent is None or not intent.is_temporal:
        return TimePlanning()

    confirmed = [item for item in dimensions if item.is_usable]
    if not confirmed:
        # This datasource has no temporal mappings at all, so time intelligence
        # is not configured for it and a time phrase is handled exactly as it
        # was before this layer existed. Refusing here would break every
        # time-flavoured question on a database nobody has reviewed yet, which
        # is a worse outcome than the one it guards against.
        return TimePlanning()

    dimension, alternatives = choose(
        dimensions,
        tables=tables,
        requested_id=metric_dimension_id or intent.time_dimension_id,
    )
    if dimension is None:
        if alternatives:
            # Several dates, no decision on file. "Projects last year" over a
            # start date, a close date and a created date is three different
            # questions, and picking one is answering a question nobody asked.
            return TimePlanning(clarification=_ask_which(intent, alternatives))
        # The datasource *does* have confirmed temporal mappings, just none on
        # the tables this question reads. Answering without the filter would
        # cover all of history and look like a result, so say so instead.
        return TimePlanning(
            unsupported=(
                f"{intent.phrase.strip() or 'That period'} cannot be applied here: "
                "no confirmed date column is available for the data this question "
                "reads."
            )
        )

    if metric_behavior == "SNAPSHOT":
        # Summing a snapshot across a period produces a number with no meaning
        # -- headcount year to date is not the sum of daily headcounts. Where
        # the metric only describes the present, saying so beats inventing
        # history.
        return TimePlanning(
            unsupported=(
                "This measure describes a point in time rather than activity "
                "over one, so it cannot be totalled across "
                f"{intent.phrase.strip() or 'a period'}."
            )
        )

    try:
        plan = resolve(intent, policy, clock=clock, temporal_dimension_id=dimension.id)
    except TimeResolutionError as exc:
        return TimePlanning(unsupported=str(exc))
    return TimePlanning(plan=plan, dimension=dimension)


def _intent_for(question: str, inherited: TimeIntent | None) -> TimeIntent | None:
    """The period this turn means, allowing for what the last one established."""
    parsed = parse_time_phrase(question)
    if inherited is None:
        return parsed
    if parsed is None:
        return None
    from app.timeintel.intent import Comparison, Grain, PeriodType

    if parsed.period is not PeriodType.NONE:
        return parsed
    # The turn changed only the comparison or the grain -- "compare it with
    # last year", "now monthly" -- so the period comes from the conversation.
    return inherited.model_copy(
        update={
            "comparison": parsed.comparison
            if parsed.comparison is not Comparison.NONE
            else inherited.comparison,
            "grain": parsed.grain if parsed.grain is not Grain.NONE else inherited.grain,
            "phrase": parsed.phrase,
        }
    )


def _ask_which(intent: TimeIntent, alternatives: list[TemporalDimension]) -> str:
    names = "; ".join(
        f"{item.concept_name or item.column_name}" for item in alternatives[:5]
    )
    phrase = intent.phrase.strip() or "that period"
    return (
        f"Which date should {phrase} be measured against: {names}?"
    )


def structural_tags(planning: TimePlanning | None) -> tuple[str, ...]:
    """The temporal concept of a question, for clustering.

    Concepts, never dates: the same question asked in two months belongs to one
    recurring pattern, and two questions about different periods do not.
    """
    if planning is None or planning.plan is None:
        return ()
    intent = planning.plan.intent
    tags = [f"time:{intent.period.value}"]
    if intent.period.value == "ROLLING" and intent.rolling_unit is not None:
        tags.append(f"rolling:{intent.rolling_unit.value}")
    if intent.comparison.value != "NONE":
        tags.append(f"comparison:{intent.comparison.value}")
    if intent.grain.value != "NONE":
        tags.append(f"grain:{intent.grain.value}")
    return tuple(tags)
