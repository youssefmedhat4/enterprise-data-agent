"""Whether the data underneath an answer is worth trusting.

The system already knows whether its SQL is correct. It does not know whether
the table it read stopped loading yesterday, and a correct query over stale data
produces a confident wrong answer that nothing in the pipeline objects to.

Deliberately small. Six assertion types, each configured by a human, each scoped
to one datasource and one table, each answerable by a single bounded read-only
query. This is not an observability platform and should not grow into one.

Two rules shape everything here:

* the SQL is built by trusted code from a reviewed configuration, never by a
  model, and a custom assertion is validated and executed exactly like any other
  analytical statement -- SQLGlot, schema authorization, read-only role;
* a check reports one number and one sentence. Never rows, never a sample. A
  quality history that accumulates business data is a second copy of the
  database with none of its controls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)

#: A check is a health probe, not a query. Anything slower than this is
#: measuring the wrong thing.
CHECK_ROW_LIMIT = 1


class AssertionType(StrEnum):
    FRESHNESS = "FRESHNESS"
    ROW_COUNT = "ROW_COUNT"
    NULL_RATE = "NULL_RATE"
    UNIQUE = "UNIQUE"
    ACCEPTED_VALUES = "ACCEPTED_VALUES"
    CUSTOM_SAFE_SQL = "CUSTOM_SAFE_SQL"


class QualityStatus(StrEnum):
    HEALTHY = "HEALTHY"
    WARNING = "WARNING"
    STALE = "STALE"
    FAILING = "FAILING"
    #: Not every database records when a row arrived. Saying so is honest;
    #: inventing a freshness number is not.
    UNKNOWN = "UNKNOWN"


class QualityError(RuntimeError):
    """Raised when an assertion cannot be configured or run."""


@dataclass(frozen=True, slots=True)
class QualityAssertion:
    data_source_id: UUID
    name: str
    assertion_type: AssertionType
    schema_name: str
    table_name: str
    column_name: str | None = None
    configuration: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    id: UUID = field(default_factory=uuid4)
    created_by: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def table_identifier(self) -> str:
        return f"{self.schema_name}.{self.table_name}"


@dataclass(frozen=True, slots=True)
class QualityCheckResult:
    assertion_id: UUID
    data_source_id: UUID
    status: QualityStatus
    observed: float | None = None
    detail: str | None = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    id: UUID = field(default_factory=uuid4)

    @property
    def is_concerning(self) -> bool:
        return self.status in {QualityStatus.STALE, QualityStatus.FAILING}


class QualityStore(Protocol):
    async def upsert(self, assertion: QualityAssertion) -> QualityAssertion: ...

    async def assertions(
        self, data_source_id: UUID, *, enabled_only: bool = False
    ) -> list[QualityAssertion]: ...

    async def assertion(
        self, data_source_id: UUID, assertion_id: UUID
    ) -> QualityAssertion | None: ...

    async def record(self, result: QualityCheckResult) -> QualityCheckResult: ...

    async def latest(
        self, data_source_id: UUID
    ) -> dict[UUID, QualityCheckResult]: ...


def validate_configuration(
    assertion_type: AssertionType,
    column_name: str | None,
    configuration: dict[str, Any],
) -> dict[str, Any]:
    """Refuse a configuration that could not produce a meaningful verdict."""
    if assertion_type is AssertionType.FRESHNESS:
        if not column_name:
            raise QualityError("A freshness assertion needs a timestamp column.")
        minutes = _positive(configuration.get("max_age_minutes"), "max_age_minutes")
        return {"max_age_minutes": minutes}
    if assertion_type is AssertionType.ROW_COUNT:
        return {"min_rows": int(_positive(configuration.get("min_rows", 1), "min_rows"))}
    if assertion_type is AssertionType.NULL_RATE:
        if not column_name:
            raise QualityError("A null-rate assertion needs a column.")
        ratio = float(configuration.get("max_ratio", 0))
        if not 0 <= ratio <= 1:
            raise QualityError("max_ratio must be between 0 and 1.")
        return {"max_ratio": ratio}
    if assertion_type is AssertionType.UNIQUE:
        if not column_name:
            raise QualityError("A uniqueness assertion needs a column.")
        return {}
    if assertion_type is AssertionType.ACCEPTED_VALUES:
        if not column_name:
            raise QualityError("An accepted-values assertion needs a column.")
        values = configuration.get("values")
        if not isinstance(values, list) or not values:
            raise QualityError("An accepted-values assertion needs at least one value.")
        if len(values) > 100:
            raise QualityError("An accepted-values list is capped at 100 values.")
        return {"values": [str(value) for value in values]}
    sql = str(configuration.get("sql", "")).strip()
    if not sql:
        raise QualityError("A custom assertion needs a read-only statement.")
    bounds = {
        key: float(configuration[key])
        for key in ("min_value", "max_value")
        if configuration.get(key) is not None
    }
    if not bounds:
        raise QualityError(
            "A custom assertion needs a min_value, a max_value, or both, so its "
            "result means something."
        )
    return {"sql": sql, **bounds}


def _positive(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise QualityError(f"{name} must be a number.") from exc
    if number <= 0:
        raise QualityError(f"{name} must be greater than zero.")
    return number


def build_check_sql(assertion: QualityAssertion) -> tuple[str, tuple[Any, ...]]:
    """The statement that answers this assertion, built by trusted code.

    Identifiers come from a reviewed configuration and are quoted; values are
    bound as parameters. A model never writes any part of this, and a custom
    statement is passed through untouched so it goes through exactly the same
    validation and read-only execution as an analytical query.
    """
    relation = f"{_quote(assertion.schema_name)}.{_quote(assertion.table_name)}"
    column = _quote(assertion.column_name) if assertion.column_name else None

    if assertion.assertion_type is AssertionType.FRESHNESS:
        return (
            "SELECT EXTRACT(EPOCH FROM (now() - max("
            f"{column})) ) / 60 AS observed FROM {relation}",
            (),
        )
    if assertion.assertion_type is AssertionType.ROW_COUNT:
        return f"SELECT count(*) AS observed FROM {relation}", ()
    if assertion.assertion_type is AssertionType.NULL_RATE:
        return (
            f"SELECT CASE WHEN count(*) = 0 THEN 0 ELSE count(*) FILTER "
            f"(WHERE {column} IS NULL)::float / count(*) END AS observed "
            f"FROM {relation}",
            (),
        )
    if assertion.assertion_type is AssertionType.UNIQUE:
        return (
            f"SELECT count(*) - count(DISTINCT {column}) AS observed FROM {relation}",
            (),
        )
    if assertion.assertion_type is AssertionType.ACCEPTED_VALUES:
        values = tuple(assertion.configuration.get("values", ()))
        placeholders = ", ".join(["%s"] * len(values))
        return (
            f"SELECT count(*) AS observed FROM {relation} "
            f"WHERE {column} IS NOT NULL AND {column}::text NOT IN ({placeholders})",
            values,
        )
    return str(assertion.configuration["sql"]), ()


def interpret(assertion: QualityAssertion, observed: float | None) -> QualityCheckResult:
    """Turn one measured number into a status a reader can act on."""
    if observed is None:
        return QualityCheckResult(
            assertion_id=assertion.id,
            data_source_id=assertion.data_source_id,
            status=QualityStatus.UNKNOWN,
            detail="The check returned no value.",
        )

    def result(status: QualityStatus, detail: str) -> QualityCheckResult:
        return QualityCheckResult(
            assertion_id=assertion.id,
            data_source_id=assertion.data_source_id,
            status=status,
            observed=observed,
            detail=detail,
        )

    if assertion.assertion_type is AssertionType.FRESHNESS:
        limit = float(assertion.configuration["max_age_minutes"])
        age = _describe_age(observed)
        if observed <= limit:
            return result(QualityStatus.HEALTHY, f"Latest data is {age} old.")
        # A warning band before stale, so a table drifting toward its limit is
        # visible before it crosses one.
        if observed <= limit * 1.5:
            return result(QualityStatus.WARNING, f"Latest data is {age} old.")
        return result(
            QualityStatus.STALE,
            f"Latest data is {age} old, past the {_describe_age(limit)} limit.",
        )

    if assertion.assertion_type is AssertionType.ROW_COUNT:
        minimum = float(assertion.configuration["min_rows"])
        if observed >= minimum:
            return result(QualityStatus.HEALTHY, f"{int(observed)} rows.")
        return result(
            QualityStatus.FAILING,
            f"{int(observed)} rows, below the expected {int(minimum)}.",
        )

    if assertion.assertion_type is AssertionType.NULL_RATE:
        limit = float(assertion.configuration["max_ratio"])
        percent = f"{observed * 100:.1f}%"
        if observed <= limit:
            return result(QualityStatus.HEALTHY, f"{percent} of values are missing.")
        return result(
            QualityStatus.FAILING,
            f"{percent} of values are missing, above the "
            f"{limit * 100:.1f}% threshold.",
        )

    if assertion.assertion_type is AssertionType.UNIQUE:
        if observed == 0:
            return result(QualityStatus.HEALTHY, "Every value is distinct.")
        return result(
            QualityStatus.FAILING, f"{int(observed)} duplicate values."
        )

    if assertion.assertion_type is AssertionType.ACCEPTED_VALUES:
        if observed == 0:
            return result(QualityStatus.HEALTHY, "Every value is a known code.")
        return result(
            QualityStatus.FAILING,
            f"{int(observed)} rows carry a value outside the approved list.",
        )

    lower = assertion.configuration.get("min_value")
    upper = assertion.configuration.get("max_value")
    if lower is not None and observed < float(lower):
        return result(QualityStatus.FAILING, f"Measured {observed:g}, below {lower}.")
    if upper is not None and observed > float(upper):
        return result(QualityStatus.FAILING, f"Measured {observed:g}, above {upper}.")
    return result(QualityStatus.HEALTHY, f"Measured {observed:g}.")


def _describe_age(minutes: float) -> str:
    if minutes < 60:
        return f"{minutes:.0f} minutes"
    if minutes < 60 * 48:
        return f"{minutes / 60:.1f} hours"
    return f"{minutes / 1440:.1f} days"


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def relevant_to(
    assertions: list[QualityAssertion],
    results: dict[UUID, QualityCheckResult],
    tables: set[str],
) -> list[tuple[QualityAssertion, QualityCheckResult]]:
    """Only what speaks about a table this answer actually read.

    Attaching every warning to every answer teaches people to ignore all of
    them. A payroll question has no business carrying an invoice freshness
    warning, however true that warning is.
    """
    wanted = {table.casefold() for table in tables}
    concerning: list[tuple[QualityAssertion, QualityCheckResult]] = []
    for assertion in assertions:
        if assertion.table_identifier.casefold() not in wanted:
            continue
        result = results.get(assertion.id)
        if result is not None and result.is_concerning:
            concerning.append((assertion, result))
    return concerning


class InMemoryQualityStore(QualityStore):
    def __init__(self) -> None:
        self._assertions: dict[UUID, QualityAssertion] = {}
        self._latest: dict[UUID, QualityCheckResult] = {}

    async def upsert(self, assertion: QualityAssertion) -> QualityAssertion:
        self._assertions[assertion.id] = assertion
        return assertion

    async def assertions(
        self, data_source_id: UUID, *, enabled_only: bool = False
    ) -> list[QualityAssertion]:
        return sorted(
            (
                item
                for item in self._assertions.values()
                if item.data_source_id == data_source_id
                and (not enabled_only or item.enabled)
            ),
            key=lambda item: item.name,
        )

    async def assertion(
        self, data_source_id: UUID, assertion_id: UUID
    ) -> QualityAssertion | None:
        found = self._assertions.get(assertion_id)
        return found if found and found.data_source_id == data_source_id else None

    async def record(self, result: QualityCheckResult) -> QualityCheckResult:
        self._latest[result.assertion_id] = result
        return result

    async def latest(self, data_source_id: UUID) -> dict[UUID, QualityCheckResult]:
        return {
            assertion_id: result
            for assertion_id, result in self._latest.items()
            if result.data_source_id == data_source_id
        }
