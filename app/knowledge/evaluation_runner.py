"""Running an evaluation set through the product, not around it.

The temptation with a benchmark is to give it a shortcut: call the SQL layer
directly, skip authorization, skip routing, skip the guardrails. That produces a
number that means nothing, because it measures a system nobody uses.

So each case is asked exactly the way a person asks it: a real request to the
real application, through dependency injection, authorization, datasource
selection, routing, SQL validation and the read-only role. The evaluation knows
only what a caller knows -- the answer that came back.

Concurrency is bounded because a run calls a model once per case, and a
benchmark that stampedes the provider is a benchmark people turn off.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Protocol
from uuid import UUID

from app.knowledge.evaluation import (
    CaseResult,
    EvaluationCase,
    EvaluationRun,
    EvaluationStore,
    Outcome,
    compare,
)

logger = logging.getLogger(__name__)

#: How many cases may be in flight at once. Small on purpose: every case is a
#: model call, and the point of a benchmark is a trustworthy number rather than
#: a fast one.
DEFAULT_CONCURRENCY = 2


class AnalysisRunner(Protocol):
    """Asks one question the way a user would, and returns what came back."""

    async def ask(
        self,
        *,
        question: str,
        data_source_id: UUID,
        as_of: datetime | None = None,
    ) -> dict[str, Any]: ...


class EvaluationRunner:
    def __init__(
        self,
        *,
        store: EvaluationStore,
        analysis: AnalysisRunner,
        concurrency: int = DEFAULT_CONCURRENCY,
    ) -> None:
        self._store = store
        self._analysis = analysis
        self._concurrency = max(1, concurrency)

    async def run(
        self,
        data_source_id: UUID,
        *,
        model_profile: str,
        triggered_by: str | None = None,
        configuration: dict[str, Any] | None = None,
        as_of: datetime | None = None,
    ) -> EvaluationRun:
        cases = await self._store.cases(data_source_id)
        started = datetime.now(UTC)
        limiter = asyncio.Semaphore(self._concurrency)

        async def one(case: EvaluationCase) -> CaseResult:
            async with limiter:
                return await self._evaluate(case, data_source_id, as_of)

        results = (
            tuple(await asyncio.gather(*(one(case) for case in cases)))
            if cases
            else ()
        )
        passed = sum(1 for result in results if result.outcome is Outcome.PASS)
        failed = sum(1 for result in results if result.outcome is Outcome.FAIL)
        errored = sum(1 for result in results if result.outcome is Outcome.ERROR)
        latencies = [result.latency_ms for result in results]
        run = EvaluationRun(
            data_source_id=data_source_id,
            model_profile=model_profile,
            started_at=started,
            finished_at=datetime.now(UTC),
            case_count=len(results),
            passed=passed,
            failed=failed,
            errored=errored,
            average_latency_ms=(sum(latencies) / len(latencies)) if latencies else 0.0,
            configuration=configuration or {},
            triggered_by=triggered_by,
            as_of=as_of,
            results=results,
        )
        return await self._store.record_run(run)

    async def _evaluate(
        self,
        case: EvaluationCase,
        data_source_id: UUID,
        run_as_of: datetime | None = None,
    ) -> CaseResult:
        started = perf_counter()
        try:
            answer = await self._analysis.ask(
                question=case.question,
                data_source_id=data_source_id,
                # The case's own anchor wins: a benchmark about a relative
                # period is only reproducible against a fixed instant.
                as_of=case.as_of or run_as_of,
            )
        except Exception as exc:
            # A case that could not be asked is an ERROR, deliberately distinct
            # from FAIL: the system did not give a wrong answer, it gave none,
            # and those want different attention.
            logger.info(
                "evaluation case errored: case=%s reason=%s",
                case.id,
                type(exc).__name__,
            )
            return CaseResult(
                case_id=case.id,
                outcome=Outcome.ERROR,
                # The stable error code, which is what an operator acts on.
                # It names a failure class, never anything about the data.
                detail=f"The request failed: {_failure_code(exc)}.",
                latency_ms=_elapsed(started),
            )

        status = str(answer.get("status", ""))
        if status in {"clarification_required", "blocked"}:
            return CaseResult(
                case_id=case.id,
                outcome=Outcome.ERROR,
                detail=f"The request ended as {status}.",
                route=_route(answer),
                latency_ms=_elapsed(started),
            )

        comparison = compare(
            case,
            rows=list(answer.get("rows") or []),
            route=_route(answer),
            metric_ids=_metric_ids(answer),
        )
        return CaseResult(
            case_id=case.id,
            outcome=comparison.outcome,
            actual=comparison.actual,
            detail=comparison.detail,
            route=_route(answer),
            latency_ms=_elapsed(started),
        )


def _failure_code(exc: Exception) -> str:
    from app.api.analysis_client import AnalysisRequestError

    if isinstance(exc, AnalysisRequestError):
        return str(exc)
    return type(exc).__name__


def _elapsed(started: float) -> float:
    return round((perf_counter() - started) * 1000, 3)


def _route(answer: dict[str, Any]) -> str | None:
    debug = (answer.get("provenance") or {}).get("debug") or {}
    route = debug.get("route")
    return str(route) if route else None


def _metric_ids(answer: dict[str, Any]) -> tuple[str, ...]:
    debug = (answer.get("provenance") or {}).get("debug") or {}
    metric = debug.get("metric_id")
    return (str(metric),) if metric else ()
