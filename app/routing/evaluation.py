from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.agent.context import AnalyticalContext
from app.metrics.gateway import MetricQuery
from app.routing.contracts import QueryRoute
from app.routing.router import DeterministicQueryRouter


class RouterCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    category: str
    language: Literal["en", "ar", "mixed"]
    question: str
    expected_route: QueryRoute
    expected_metric_id: str | None = None
    prior_route: Literal["governed_metric", "adhoc_analytics"] | None = None
    prior_metric_id: str | None = None
    prior_dimensions: tuple[str, ...] = ()


class RouterCaseOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    passed: bool
    expected_route: QueryRoute
    actual_route: QueryRoute
    expected_metric_id: str | None
    actual_metric_id: str | None
    latency_ms: float = Field(ge=0)


def load_router_cases(path: Path) -> tuple[RouterCase, ...]:
    return tuple(TypeAdapter(list[RouterCase]).validate_json(path.read_text(encoding="utf-8")))


def evaluate_router_cases(path: Path) -> dict[str, object]:
    router = DeterministicQueryRouter()
    cases = load_router_cases(path)
    outcomes: list[RouterCaseOutcome] = []
    for case in cases:
        started = perf_counter()
        decision = router.route(case.question, prior_context=_prior_context(case))
        latency_ms = (perf_counter() - started) * 1000
        actual_metric_id = (
            decision.metric_candidates[0] if len(decision.metric_candidates) == 1 else None
        )
        outcomes.append(
            RouterCaseOutcome(
                case_id=case.id,
                passed=(
                    decision.route == case.expected_route
                    and actual_metric_id == case.expected_metric_id
                ),
                expected_route=case.expected_route,
                actual_route=decision.route,
                expected_metric_id=case.expected_metric_id,
                actual_metric_id=actual_metric_id,
                latency_ms=round(latency_ms, 4),
            )
        )
    return _report(cases, outcomes)


def _prior_context(case: RouterCase) -> AnalyticalContext | None:
    if case.prior_route is None:
        return None
    metric_query = None
    if case.prior_metric_id is not None:
        metric_query = MetricQuery(
            metric=case.prior_metric_id,
            dimensions=case.prior_dimensions,
        )
    return AnalyticalContext(
        previous_question="prior analytical question",
        resolved_question="prior analytical question",
        execution_route=case.prior_route,
        metric_query=metric_query,
    )


def _report(
    cases: tuple[RouterCase, ...],
    outcomes: list[RouterCaseOutcome],
) -> dict[str, object]:
    expected = Counter(case.expected_route for case in cases)
    actual = Counter(outcome.actual_route for outcome in outcomes)
    true_positive_metric = sum(
        outcome.expected_route == outcome.actual_route == QueryRoute.GOVERNED_METRIC
        for outcome in outcomes
    )
    false_positive_metric = sum(
        outcome.actual_route == QueryRoute.GOVERNED_METRIC
        and outcome.expected_route != QueryRoute.GOVERNED_METRIC
        for outcome in outcomes
    )
    false_negative_metric = sum(
        outcome.expected_route == QueryRoute.GOVERNED_METRIC
        and outcome.actual_route != QueryRoute.GOVERNED_METRIC
        for outcome in outcomes
    )
    latencies = sorted(outcome.latency_ms for outcome in outcomes)
    category_accuracy = {
        category: _accuracy(
            [
                outcome
                for outcome, case in zip(outcomes, cases, strict=True)
                if case.category == category
            ]
        )
        for category in sorted({case.category for case in cases})
    }
    language_accuracy = {
        language: _accuracy(
            [
                outcome
                for outcome, case in zip(outcomes, cases, strict=True)
                if case.language == language
            ]
        )
        for language in ("en", "ar", "mixed")
    }
    followups = [
        outcome
        for outcome, case in zip(outcomes, cases, strict=True)
        if case.category == "follow_up"
    ]
    metric_cases = [case for case in cases if case.expected_route == QueryRoute.GOVERNED_METRIC]
    metric_id_correct = sum(
        outcome.expected_metric_id == outcome.actual_metric_id
        for outcome in outcomes
        if outcome.expected_route == QueryRoute.GOVERNED_METRIC
    )
    return {
        "case_count": len(cases),
        "passed": sum(outcome.passed for outcome in outcomes),
        "failed": sum(not outcome.passed for outcome in outcomes),
        "overall_route_accuracy": _accuracy(outcomes),
        "governed_metric_precision": _ratio(
            true_positive_metric, true_positive_metric + false_positive_metric
        ),
        "governed_metric_recall": _ratio(
            true_positive_metric, true_positive_metric + false_negative_metric
        ),
        "adhoc_precision": _route_precision(outcomes, QueryRoute.ADHOC_ANALYTICS),
        "adhoc_recall": _route_recall(outcomes, QueryRoute.ADHOC_ANALYTICS),
        "block_accuracy": _expected_route_accuracy(outcomes, QueryRoute.BLOCK),
        "clarification_accuracy": _expected_route_accuracy(outcomes, QueryRoute.CLARIFY),
        "follow_up_route_retention": _accuracy(followups),
        "metric_id_accuracy": _ratio(metric_id_correct, len(metric_cases)),
        "false_governed_metric_routes": false_positive_metric,
        "expected_route_counts": {key.value: value for key, value in expected.items()},
        "actual_route_counts": {key.value: value for key, value in actual.items()},
        "accuracy_by_category": category_accuracy,
        "accuracy_by_language": language_accuracy,
        "latency_ms": {
            "average": round(sum(latencies) / len(latencies), 4),
            "median": round(median(latencies), 4),
            "p95": round(latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))], 4),
        },
        "failed_case_ids": [outcome.case_id for outcome in outcomes if not outcome.passed],
        "outcomes": [outcome.model_dump(mode="json") for outcome in outcomes],
    }


def _accuracy(outcomes: Sequence[RouterCaseOutcome]) -> float:
    return _ratio(sum(outcome.passed for outcome in outcomes), len(outcomes))


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 1.0


def _route_precision(outcomes: list[RouterCaseOutcome], route: QueryRoute) -> float:
    predicted = [outcome for outcome in outcomes if outcome.actual_route == route]
    return _ratio(sum(outcome.expected_route == route for outcome in predicted), len(predicted))


def _route_recall(outcomes: list[RouterCaseOutcome], route: QueryRoute) -> float:
    expected = [outcome for outcome in outcomes if outcome.expected_route == route]
    return _ratio(sum(outcome.actual_route == route for outcome in expected), len(expected))


def _expected_route_accuracy(outcomes: list[RouterCaseOutcome], route: QueryRoute) -> float:
    expected = [outcome for outcome in outcomes if outcome.expected_route == route]
    return _ratio(sum(outcome.actual_route == route for outcome in expected), len(expected))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate deterministic query routing.")
    parser.add_argument("--dataset", type=Path, default=Path("evals/router_cases.json"))
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    report = evaluate_router_cases(args.dataset)
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
