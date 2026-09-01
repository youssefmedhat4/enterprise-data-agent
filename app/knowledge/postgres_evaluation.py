"""PostgreSQL-backed evaluation sets and run history.

An evaluation set is only useful across the change it is meant to catch, so it
has to outlive the process. Run history is what makes a regression visible: one
run says a case failed, two say it started failing.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.knowledge.evaluation import (
    CaseResult,
    CaseStatus,
    EvaluationCase,
    EvaluationRun,
    EvaluationStore,
    ExpectationKind,
    Outcome,
)

_CASE_COLUMNS = """
    id, data_source_id, name, question, expectation, expected, tolerance,
    ordered, expected_route, expected_metric_ids, status, created_by,
    created_at, updated_at
"""

_UPSERT_CASE = """
    INSERT INTO knowledge.evaluation_cases
        (id, data_source_id, name, question, expectation, expected, tolerance,
         ordered, expected_route, expected_metric_ids, status, created_by,
         created_at, updated_at)
    VALUES
        (%(id)s, %(data_source_id)s, %(name)s, %(question)s, %(expectation)s,
         %(expected)s, %(tolerance)s, %(ordered)s, %(route)s, %(metric_ids)s,
         %(status)s, %(created_by)s, %(created_at)s, now())
    ON CONFLICT (id) DO UPDATE SET
        name = EXCLUDED.name,
        question = EXCLUDED.question,
        expectation = EXCLUDED.expectation,
        expected = EXCLUDED.expected,
        tolerance = EXCLUDED.tolerance,
        ordered = EXCLUDED.ordered,
        expected_route = EXCLUDED.expected_route,
        expected_metric_ids = EXCLUDED.expected_metric_ids,
        status = EXCLUDED.status,
        updated_at = now()
"""


class PostgresEvaluationStore(EvaluationStore):
    def __init__(self, pool: AsyncConnectionPool[Any]) -> None:
        self._pool = pool

    async def upsert_case(self, case: EvaluationCase) -> EvaluationCase:
        async with (
            self._pool.connection() as connection,
            connection.cursor() as cursor,
        ):
            await cursor.execute(
                _UPSERT_CASE,
                {
                    "id": case.id,
                    "data_source_id": case.data_source_id,
                    "name": case.name,
                    "question": case.question,
                    "expectation": case.expectation.value,
                    "expected": Jsonb(case.expected),
                    "tolerance": case.tolerance,
                    "ordered": case.ordered,
                    "route": case.expected_route,
                    "metric_ids": list(case.expected_metric_ids),
                    "status": case.status.value,
                    "created_by": case.created_by,
                    "created_at": case.created_at,
                },
            )
        stored = await self.case(case.data_source_id, case.id)
        return stored if stored is not None else case

    async def cases(
        self, data_source_id: UUID, *, include_archived: bool = False
    ) -> list[EvaluationCase]:
        clause = "" if include_archived else " AND status = 'ACTIVE'"
        async with (
            self._pool.connection() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(
                f"SELECT {_CASE_COLUMNS} FROM knowledge.evaluation_cases"
                " WHERE data_source_id = %(data_source_id)s" + clause + " ORDER BY name",
                {"data_source_id": data_source_id},
            )
            rows = cast("list[dict[str, Any]]", await cursor.fetchall())
        return [_to_case(row) for row in rows]

    async def case(
        self, data_source_id: UUID, case_id: UUID
    ) -> EvaluationCase | None:
        async with (
            self._pool.connection() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(
                f"SELECT {_CASE_COLUMNS} FROM knowledge.evaluation_cases"
                " WHERE data_source_id = %(data_source_id)s AND id = %(id)s",
                {"data_source_id": data_source_id, "id": case_id},
            )
            row = cast("dict[str, Any] | None", await cursor.fetchone())
        return _to_case(row) if row is not None else None

    async def record_run(self, run: EvaluationRun) -> EvaluationRun:
        async with (
            self._pool.connection() as connection,
            connection.cursor() as cursor,
        ):
            await cursor.execute(
                "INSERT INTO knowledge.evaluation_runs"
                " (id, data_source_id, model_profile, started_at, finished_at,"
                "  case_count, passed, failed, errored, average_latency_ms,"
                "  configuration, triggered_by)"
                " VALUES (%(id)s, %(data_source_id)s, %(model_profile)s,"
                "  %(started_at)s, %(finished_at)s, %(case_count)s, %(passed)s,"
                "  %(failed)s, %(errored)s, %(latency)s, %(configuration)s,"
                "  %(triggered_by)s)"
                " ON CONFLICT (id) DO UPDATE SET"
                "  finished_at = EXCLUDED.finished_at,"
                "  case_count = EXCLUDED.case_count,"
                "  passed = EXCLUDED.passed,"
                "  failed = EXCLUDED.failed,"
                "  errored = EXCLUDED.errored,"
                "  average_latency_ms = EXCLUDED.average_latency_ms",
                {
                    "id": run.id,
                    "data_source_id": run.data_source_id,
                    "model_profile": run.model_profile,
                    "started_at": run.started_at,
                    "finished_at": run.finished_at,
                    "case_count": run.case_count,
                    "passed": run.passed,
                    "failed": run.failed,
                    "errored": run.errored,
                    "latency": run.average_latency_ms,
                    "configuration": Jsonb(run.configuration),
                    "triggered_by": run.triggered_by,
                },
            )
            for result in run.results:
                await cursor.execute(
                    "INSERT INTO knowledge.evaluation_case_results"
                    " (id, run_id, case_id, outcome, actual, detail, route, latency_ms)"
                    " VALUES (%(id)s, %(run_id)s, %(case_id)s, %(outcome)s,"
                    "  %(actual)s, %(detail)s, %(route)s, %(latency)s)"
                    " ON CONFLICT (run_id, case_id) DO UPDATE SET"
                    "  outcome = EXCLUDED.outcome,"
                    "  actual = EXCLUDED.actual,"
                    "  detail = EXCLUDED.detail,"
                    "  route = EXCLUDED.route,"
                    "  latency_ms = EXCLUDED.latency_ms",
                    {
                        "id": result.id,
                        "run_id": run.id,
                        "case_id": result.case_id,
                        "outcome": result.outcome.value,
                        "actual": result.actual,
                        "detail": result.detail,
                        "route": result.route,
                        "latency": result.latency_ms,
                    },
                )
        return run

    async def runs(
        self, data_source_id: UUID, *, limit: int = 20
    ) -> list[EvaluationRun]:
        async with (
            self._pool.connection() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(
                "SELECT * FROM knowledge.evaluation_runs"
                " WHERE data_source_id = %(data_source_id)s"
                " ORDER BY started_at DESC LIMIT %(limit)s",
                {"data_source_id": data_source_id, "limit": limit},
            )
            rows = cast("list[dict[str, Any]]", await cursor.fetchall())
            runs = [_to_run(row) for row in rows]
            for index, run in enumerate(runs):
                runs[index] = await self._with_results(cursor, run)
        return runs

    async def run(
        self, data_source_id: UUID, run_id: UUID
    ) -> EvaluationRun | None:
        async with (
            self._pool.connection() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(
                "SELECT * FROM knowledge.evaluation_runs"
                " WHERE data_source_id = %(data_source_id)s AND id = %(id)s",
                {"data_source_id": data_source_id, "id": run_id},
            )
            row = cast("dict[str, Any] | None", await cursor.fetchone())
            if row is None:
                return None
            return await self._with_results(cursor, _to_run(row))

    async def _with_results(self, cursor: Any, run: EvaluationRun) -> EvaluationRun:
        await cursor.execute(
            "SELECT id, case_id, outcome, actual, detail, route, latency_ms"
            " FROM knowledge.evaluation_case_results WHERE run_id = %(run_id)s",
            {"run_id": run.id},
        )
        rows = cast("list[dict[str, Any]]", await cursor.fetchall())
        results = tuple(
            CaseResult(
                id=row["id"],
                case_id=row["case_id"],
                outcome=Outcome(row["outcome"]),
                actual=row["actual"],
                detail=row["detail"],
                route=row["route"],
                latency_ms=row["latency_ms"],
            )
            for row in rows
        )
        return EvaluationRun(
            id=run.id,
            data_source_id=run.data_source_id,
            model_profile=run.model_profile,
            started_at=run.started_at,
            finished_at=run.finished_at,
            case_count=run.case_count,
            passed=run.passed,
            failed=run.failed,
            errored=run.errored,
            average_latency_ms=run.average_latency_ms,
            configuration=run.configuration,
            triggered_by=run.triggered_by,
            results=results,
        )


def _to_case(row: dict[str, Any]) -> EvaluationCase:
    return EvaluationCase(
        id=row["id"],
        data_source_id=row["data_source_id"],
        name=row["name"],
        question=row["question"],
        expectation=ExpectationKind(row["expectation"]),
        expected=row["expected"] or {},
        tolerance=Decimal(str(row["tolerance"])),
        ordered=row["ordered"],
        expected_route=row["expected_route"],
        expected_metric_ids=tuple(row["expected_metric_ids"] or ()),
        status=CaseStatus(row["status"]),
        created_by=row["created_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _to_run(row: dict[str, Any]) -> EvaluationRun:
    return EvaluationRun(
        id=row["id"],
        data_source_id=row["data_source_id"],
        model_profile=row["model_profile"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        case_count=row["case_count"],
        passed=row["passed"],
        failed=row["failed"],
        errored=row["errored"],
        average_latency_ms=row["average_latency_ms"],
        configuration=row["configuration"] or {},
        triggered_by=row["triggered_by"],
    )
