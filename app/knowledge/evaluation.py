"""Known-answer questions, and the comparison that decides whether they still work.

A change to a model, a prompt, a confirmed mapping, a business rule or a routing
rule is invisible until someone notices a wrong answer. An evaluation set is the
questions whose answers are already known, so a change that breaks one is caught
by running them.

The comparison is deliberately unforgiving. A benchmark that reports a wrong
number as correct is worse than no benchmark, because it converts an unnoticed
regression into a confident claim that nothing broke. So: numbers are compared
exactly unless a tolerance was configured, and nothing here treats "close
enough" or "looks similar" as a pass.

Two things are allowed to vary, because they carry no meaning:

* column order, unless the case says otherwise -- `SELECT a, b` and
  `SELECT b, a` answer the same question;
* row order, unless the case says the ranking is the point.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID, uuid4

#: A stored actual value is a reader's aid, not a result set. Anything longer
#: is truncated: evaluation history is for spotting change, not for holding
#: business data.
MAX_ACTUAL_LENGTH = 2000

#: An expected table is a comparison value, not a captured export. A case that
#: needs more rows than this is testing something a benchmark cannot keep
#: stable anyway.
MAX_EXPECTED_ROWS = 200


class ExpectationKind(StrEnum):
    SCALAR = "SCALAR"
    TABLE = "TABLE"
    ROW_COUNT = "ROW_COUNT"
    EMPTY = "EMPTY"


class CaseStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class Outcome(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"


class Movement(StrEnum):
    """How one case moved between two runs."""

    UNCHANGED_PASS = "UNCHANGED_PASS"
    UNCHANGED_FAIL = "UNCHANGED_FAIL"
    IMPROVED = "IMPROVED"
    REGRESSION = "REGRESSION"
    NEW = "NEW"


class EvaluationError(RuntimeError):
    """Raised when a case cannot be stored or compared."""


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    data_source_id: UUID
    name: str
    question: str
    expectation: ExpectationKind
    #: SCALAR: {"value": "42"}. ROW_COUNT: {"value": 3}. TABLE:
    #: {"rows": [{"col": "v"}, ...]}. EMPTY: {}.
    expected: dict[str, Any] = field(default_factory=dict)
    id: UUID = field(default_factory=uuid4)
    tolerance: Decimal = Decimal(0)
    #: True only when the question is about ranking, where row order is the
    #: answer rather than an artefact of how the database returned it.
    ordered: bool = False
    expected_route: str | None = None
    expected_metric_ids: tuple[str, ...] = ()
    status: CaseStatus = CaseStatus.ACTIVE
    #: The instant a relative period resolves against. Without one, "revenue
    #: year to date" means something different every month and the regression
    #: this case was written to catch never fails twice the same way.
    as_of: datetime | None = None
    created_by: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_active(self) -> bool:
        return self.status is CaseStatus.ACTIVE


@dataclass(frozen=True, slots=True)
class CaseResult:
    case_id: UUID
    outcome: Outcome
    actual: str | None = None
    detail: str | None = None
    route: str | None = None
    latency_ms: float = 0.0
    id: UUID = field(default_factory=uuid4)


@dataclass(frozen=True, slots=True)
class EvaluationRun:
    data_source_id: UUID
    model_profile: str
    id: UUID = field(default_factory=uuid4)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    case_count: int = 0
    passed: int = 0
    failed: int = 0
    errored: int = 0
    average_latency_ms: float = 0.0
    configuration: dict[str, Any] = field(default_factory=dict)
    triggered_by: str | None = None
    #: The anchor this run resolved relative periods against.
    as_of: datetime | None = None
    results: tuple[CaseResult, ...] = ()

    @property
    def pass_rate(self) -> float:
        return self.passed / self.case_count if self.case_count else 0.0


class EvaluationStore(Protocol):
    """Storage the evaluation routes and runner need."""

    async def upsert_case(self, case: EvaluationCase) -> EvaluationCase: ...

    async def cases(
        self, data_source_id: UUID, *, include_archived: bool = False
    ) -> list[EvaluationCase]: ...

    async def case(
        self, data_source_id: UUID, case_id: UUID
    ) -> EvaluationCase | None: ...

    async def record_run(self, run: EvaluationRun) -> EvaluationRun: ...

    async def runs(
        self, data_source_id: UUID, *, limit: int = 20
    ) -> list[EvaluationRun]: ...

    async def run(
        self, data_source_id: UUID, run_id: UUID
    ) -> EvaluationRun | None: ...


# --- comparison --------------------------------------------------------------


def validate_expected(
    expectation: ExpectationKind, expected: dict[str, Any]
) -> dict[str, Any]:
    """Refuse an expectation that cannot be compared against anything.

    Storing one would produce a case that fails forever or passes vacuously,
    and either teaches a reviewer to ignore the benchmark.
    """
    if expectation is ExpectationKind.EMPTY:
        return {}
    if expectation in {ExpectationKind.SCALAR, ExpectationKind.ROW_COUNT}:
        if "value" not in expected:
            raise EvaluationError(
                f"A {expectation.value} expectation needs a 'value'."
            )
        if expectation is ExpectationKind.ROW_COUNT:
            try:
                count = int(str(expected["value"]))
            except (TypeError, ValueError) as exc:
                raise EvaluationError(
                    "A ROW_COUNT expectation must be a whole number."
                ) from exc
            if count < 0:
                raise EvaluationError("A row count cannot be negative.")
            return {"value": count}
        return {"value": expected["value"]}
    rows = expected.get("rows")
    if not isinstance(rows, list) or not rows:
        raise EvaluationError("A TABLE expectation needs at least one row.")
    if len(rows) > MAX_EXPECTED_ROWS:
        raise EvaluationError(
            f"A TABLE expectation holds at most {MAX_EXPECTED_ROWS} rows; "
            "a benchmark is a comparison value, not a captured export."
        )
    if not all(isinstance(row, dict) for row in rows):
        raise EvaluationError("Every expected row must be an object.")
    return {"rows": rows}


@dataclass(frozen=True, slots=True)
class Comparison:
    """The verdict, and why."""

    outcome: Outcome
    actual: str | None
    detail: str | None = None


def compare(
    case: EvaluationCase,
    *,
    rows: list[dict[str, Any]],
    route: str | None = None,
    metric_ids: tuple[str, ...] = (),
) -> Comparison:
    """Decide whether this answer still matches what the case expects."""
    actual = _render(rows)
    if case.expected_route and route and route != case.expected_route:
        return Comparison(
            Outcome.FAIL,
            actual,
            f"Answered by {route}, expected {case.expected_route}.",
        )
    if case.expected_metric_ids:
        missing = sorted(set(case.expected_metric_ids) - set(metric_ids))
        if missing:
            return Comparison(
                Outcome.FAIL, actual, f"Metrics not used: {', '.join(missing)}."
            )

    if case.expectation is ExpectationKind.EMPTY:
        if rows:
            return Comparison(
                Outcome.FAIL, actual, f"Expected no rows, got {len(rows)}."
            )
        return Comparison(Outcome.PASS, actual)

    if case.expectation is ExpectationKind.ROW_COUNT:
        expected_count = int(case.expected["value"])
        if len(rows) != expected_count:
            return Comparison(
                Outcome.FAIL,
                actual,
                f"Expected {expected_count} rows, got {len(rows)}.",
            )
        return Comparison(Outcome.PASS, actual)

    if case.expectation is ExpectationKind.SCALAR:
        return _compare_scalar(case, rows, actual)

    return _compare_table(case, rows, actual)


def _compare_scalar(
    case: EvaluationCase, rows: list[dict[str, Any]], actual: str
) -> Comparison:
    if len(rows) != 1 or len(rows[0]) != 1:
        return Comparison(
            Outcome.FAIL,
            actual,
            "Expected a single value; the answer was not one row of one column.",
        )
    value = next(iter(rows[0].values()))
    if _matches(case.expected["value"], value, case.tolerance):
        return Comparison(Outcome.PASS, actual)
    return Comparison(
        Outcome.FAIL,
        actual,
        f"Expected {_canonical(case.expected['value'])}, got {_canonical(value)}.",
    )


def _compare_table(
    case: EvaluationCase, rows: list[dict[str, Any]], actual: str
) -> Comparison:
    expected_rows = [dict(row) for row in case.expected["rows"]]
    if len(rows) != len(expected_rows):
        return Comparison(
            Outcome.FAIL,
            actual,
            f"Expected {len(expected_rows)} rows, got {len(rows)}.",
        )
    if case.ordered:
        pairs = list(zip(expected_rows, rows, strict=True))
        for index, (expected_row, row) in enumerate(pairs):
            if not _rows_match(expected_row, row, case.tolerance):
                return Comparison(
                    Outcome.FAIL, actual, f"Row {index + 1} does not match."
                )
        return Comparison(Outcome.PASS, actual)

    # Unordered: every expected row must be matched by exactly one actual row.
    remaining = list(rows)
    for expected_row in expected_rows:
        match = next(
            (
                candidate
                for candidate in remaining
                if _rows_match(expected_row, candidate, case.tolerance)
            ),
            None,
        )
        if match is None:
            return Comparison(
                Outcome.FAIL,
                actual,
                f"No row matched {_canonical_row(expected_row)}.",
            )
        remaining.remove(match)
    return Comparison(Outcome.PASS, actual)


def _rows_match(
    expected: dict[str, Any], actual: dict[str, Any], tolerance: Decimal
) -> bool:
    """Compare by column name, so column order cannot fail a case.

    Only the columns the case names are compared: an extra column the answer
    happens to carry is not a regression in what was asked about.
    """
    normalized = {str(key).casefold(): value for key, value in actual.items()}
    for key, value in expected.items():
        name = str(key).casefold()
        if name not in normalized:
            return False
        if not _matches(value, normalized[name], tolerance):
            return False
    return True


def _matches(expected: Any, actual: Any, tolerance: Decimal) -> bool:
    expected_number = _number(expected)
    actual_number = _number(actual)
    if expected_number is not None and actual_number is not None:
        difference = abs(expected_number - actual_number)
        return difference <= tolerance
    return _canonical(expected) == _canonical(actual)


def _number(value: Any) -> Decimal | None:
    """Numbers arrive as int, float, Decimal or string, and must compare alike.

    `numeric` comes back from the driver as a string and `int8` as an int, so
    comparing raw values would fail a case for a reason that has nothing to do
    with the answer.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float | Decimal):
        return Decimal(str(value))
    if isinstance(value, str):
        text = value.strip().replace(",", "").removeprefix("$")
        try:
            return Decimal(text)
        except InvalidOperation:
            return None
    return None


def _canonical(value: Any) -> str:
    number = _number(value)
    if number is not None:
        normalized = number.normalize()
        # Decimal normalises 1000 to 1E+3; expand it so a reader sees a number.
        return f"{normalized:f}"
    if value is None:
        return "null"
    return str(value).strip()


def _canonical_row(row: dict[str, Any]) -> str:
    return json.dumps(
        {str(key).casefold(): _canonical(value) for key, value in sorted(row.items())},
        sort_keys=True,
    )


def _render(rows: list[dict[str, Any]]) -> str:
    """A short, stable rendering of the answer, for a human comparing runs."""
    if not rows:
        return "(no rows)"
    if len(rows) == 1 and len(rows[0]) == 1:
        return _canonical(next(iter(rows[0].values())))
    rendered = json.dumps(
        [
            {str(key): _canonical(value) for key, value in row.items()}
            for row in rows[:20]
        ],
        sort_keys=True,
    )
    if len(rows) > 20:
        rendered = f"{rendered} (+{len(rows) - 20} more rows)"
    return rendered[:MAX_ACTUAL_LENGTH]


def movements(
    current: EvaluationRun, previous: EvaluationRun | None
) -> dict[UUID, Movement]:
    """How each case moved since the previous run.

    A regression is the reason this exists, so it is named separately from a
    case that has simply always failed.
    """
    before = (
        {result.case_id: result.outcome for result in previous.results}
        if previous is not None
        else {}
    )
    moved: dict[UUID, Movement] = {}
    for result in current.results:
        was = before.get(result.case_id)
        now_passed = result.outcome is Outcome.PASS
        if was is None:
            moved[result.case_id] = Movement.NEW
        elif was is Outcome.PASS and now_passed:
            moved[result.case_id] = Movement.UNCHANGED_PASS
        elif was is Outcome.PASS:
            moved[result.case_id] = Movement.REGRESSION
        elif now_passed:
            moved[result.case_id] = Movement.IMPROVED
        else:
            moved[result.case_id] = Movement.UNCHANGED_FAIL
    return moved


class InMemoryEvaluationStore(EvaluationStore):
    """Development storage, datasource-scoped like the persistent one."""

    def __init__(self) -> None:
        self._cases: dict[UUID, EvaluationCase] = {}
        self._runs: dict[UUID, EvaluationRun] = {}

    async def upsert_case(self, case: EvaluationCase) -> EvaluationCase:
        self._cases[case.id] = case
        return case

    async def cases(
        self, data_source_id: UUID, *, include_archived: bool = False
    ) -> list[EvaluationCase]:
        return sorted(
            (
                case
                for case in self._cases.values()
                if case.data_source_id == data_source_id
                and (include_archived or case.is_active)
            ),
            key=lambda case: case.name,
        )

    async def case(
        self, data_source_id: UUID, case_id: UUID
    ) -> EvaluationCase | None:
        found = self._cases.get(case_id)
        return found if found and found.data_source_id == data_source_id else None

    async def record_run(self, run: EvaluationRun) -> EvaluationRun:
        self._runs[run.id] = run
        return run

    async def runs(
        self, data_source_id: UUID, *, limit: int = 20
    ) -> list[EvaluationRun]:
        return sorted(
            (run for run in self._runs.values() if run.data_source_id == data_source_id),
            key=lambda run: run.started_at,
            reverse=True,
        )[:limit]

    async def run(
        self, data_source_id: UUID, run_id: UUID
    ) -> EvaluationRun | None:
        found = self._runs.get(run_id)
        return found if found and found.data_source_id == data_source_id else None
