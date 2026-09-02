"""Which column carries time, and how that column stores it.

A database has many dates. An invoice has an invoice date, a posting date and a
load timestamp; a project has a start, an end and a created-at. "Projects last
year" is genuinely ambiguous across those, and picking the one whose name
happens to contain "date" is guessing with extra steps.

So a temporal dimension is reviewed metadata attached to a confirmed semantic
attribute: what role the column plays, and how it physically stores a date. The
second half matters more than it sounds. Older systems store dates as `CHAR(8)`
text, and the only safe way to read one is a declared strategy from a small
allowlist -- never a parsing expression a model wrote, which is how an
`ORDER BY` on text silently sorts 20260901 before 20251231 or how a cast blows
up on one bad row.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from app.knowledge.contracts import ApprovalStatus


class TemporalRole(StrEnum):
    """What this column means in time, not merely that it is a date."""

    #: When the thing happened. The default for measuring flows.
    EVENT_TIME = "EVENT_TIME"
    EFFECTIVE_START = "EFFECTIVE_START"
    EFFECTIVE_END = "EFFECTIVE_END"
    SNAPSHOT_DATE = "SNAPSHOT_DATE"
    CREATED_AT = "CREATED_AT"
    UPDATED_AT = "UPDATED_AT"
    #: When the row arrived in this database, which answers freshness rather
    #: than any business question.
    LOAD_TIME = "LOAD_TIME"
    START_DATE = "START_DATE"
    END_DATE = "END_DATE"


class TemporalStorage(StrEnum):
    """How the column physically holds the value.

    A closed allowlist: each member maps to one expression trusted code emits.
    There is deliberately no member meaning "whatever the model suggests".
    """

    NATIVE_DATE = "NATIVE_DATE"
    NATIVE_TIMESTAMP = "NATIVE_TIMESTAMP"
    TIMESTAMP_WITH_TIMEZONE = "TIMESTAMP_WITH_TIMEZONE"
    #: Eight characters of text, `YYYYMMDD`, as older systems store dates.
    YYYYMMDD_TEXT = "YYYYMMDD_TEXT"


class TemporalError(RuntimeError):
    """Raised when temporal metadata cannot be configured or used."""


@dataclass(frozen=True, slots=True)
class TemporalDimension:
    """One reviewed temporal attribute of one datasource."""

    data_source_id: UUID
    semantic_attribute_id: UUID
    role: TemporalRole
    storage: TemporalStorage
    #: Resolved from the semantic attribute at load time, so callers building
    #: SQL never have to look the physical column up again.
    schema_name: str = ""
    table_name: str = ""
    column_name: str = ""
    concept_name: str = ""
    entity_name: str = ""
    #: The one to use when a question about this entity names no column. Only a
    #: reviewer sets it -- it is the answer to "projects last year" and must be
    #: a decision rather than an inference.
    is_default_for_entity: bool = False
    status: ApprovalStatus = ApprovalStatus.PROPOSED
    schema_fingerprint: str | None = None
    id: UUID = field(default_factory=uuid4)
    reviewed_by: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_usable(self) -> bool:
        return self.status is ApprovalStatus.CONFIRMED

    @property
    def qualified_column(self) -> str:
        return f"{self.schema_name}.{self.table_name}.{self.column_name}"

    @property
    def table_identifier(self) -> str:
        return f"{self.schema_name}.{self.table_name}"

    @property
    def measures_flow(self) -> bool:
        """Whether filtering on this column selects events in a window."""
        return self.role in {
            TemporalRole.EVENT_TIME,
            TemporalRole.CREATED_AT,
            TemporalRole.START_DATE,
        }


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def timestamp_expression(
    dimension: TemporalDimension, *, alias: str | None = None
) -> str:
    """A SQL expression yielding a comparable timestamp for this column.

    Built here, from a reviewed storage strategy, and nowhere else. The point of
    the allowlist is that `YYYYMMDD_TEXT` produces one known-correct conversion
    rather than whichever of a dozen plausible ones a model reaches for.
    """
    column = _quote(dimension.column_name)
    relation = alias if alias is not None else _quote(dimension.table_name)
    if relation and not _IDENTIFIER.match(relation.strip('"')):
        raise TemporalError("A table alias must be a plain identifier.")
    reference = f"{_quote(relation.strip(chr(34)))}.{column}" if relation else column

    if dimension.storage is TemporalStorage.YYYYMMDD_TEXT:
        # Text dates compare and sort wrongly, and one malformed row must not
        # take the whole answer with it -- a bare cast raises and the query
        # dies. The guard checks eight characters that are all digits, using
        # `translate` rather than a pattern: no regex engine is involved, and
        # nothing here accepts a caller-supplied expression.
        return (
            f"CASE WHEN length(trim({reference})) = 8 "
            f"AND translate(trim({reference}), '0123456789', '') = '' "
            f"THEN to_timestamp(trim({reference}), 'YYYYMMDD') END"
        )
    if dimension.storage is TemporalStorage.NATIVE_DATE:
        return f"{reference}::timestamp"
    if dimension.storage is TemporalStorage.TIMESTAMP_WITH_TIMEZONE:
        return reference
    return f"{reference}::timestamp"


def is_timezone_aware(dimension: TemporalDimension) -> bool:
    """Whether the stored value already carries a zone.

    A naive column records a local wall-clock reading with no zone attached, so
    comparing it against a UTC instant is only correct once the datasource's
    timezone is applied.
    """
    return dimension.storage is TemporalStorage.TIMESTAMP_WITH_TIMEZONE


def default_for_entity(
    dimensions: list[TemporalDimension], entity_name: str
) -> TemporalDimension | None:
    """The column a question about this entity means, when a reviewer said so."""
    folded = entity_name.casefold()
    for dimension in dimensions:
        if (
            dimension.is_usable
            and dimension.is_default_for_entity
            and dimension.entity_name.casefold() == folded
        ):
            return dimension
    return None


def candidates_for_tables(
    dimensions: list[TemporalDimension], tables: set[str]
) -> list[TemporalDimension]:
    """Confirmed temporal columns on the tables an answer may read."""
    wanted = {table.casefold() for table in tables}
    return [
        dimension
        for dimension in dimensions
        if dimension.is_usable and dimension.table_identifier.casefold() in wanted
    ]


def named_by_question(
    dimensions: list[TemporalDimension], question: str
) -> list[TemporalDimension]:
    """Candidates the question itself points at.

    A question saying "invoiced revenue" is about invoices, and the invoice
    date is not in competition with the compensation effective date however
    many tables happen to be in scope. Matching is on the reviewed entity and
    concept names, so it works on a schema whose physical columns are called
    `inv_dt_chr`.

    Word overlap rather than whole-phrase containment: nobody writes "invoice
    date" in a sentence, they write "what was invoiced". Short words are
    dropped from both sides so nothing matches on "the".
    """
    asked = {word for word in _words(question) if len(word) > 3}
    if not asked:
        return []

    def mentions(term: str) -> bool:
        words = {word for word in _words(term) if len(word) > 3}
        return any(
            word in asked
            or f"{word}s" in asked
            or f"{word}d" in asked
            or word.rstrip("s") in asked
            for word in words
        )

    return [
        dimension
        for dimension in dimensions
        if mentions(dimension.entity_name) or mentions(dimension.concept_name)
    ]


def _words(text: str) -> set[str]:
    return {word for word in re.split(r"[^a-z0-9]+", text.casefold()) if word}


def choose(
    dimensions: list[TemporalDimension],
    *,
    tables: set[str],
    requested_id: UUID | None = None,
    question: str = "",
) -> tuple[TemporalDimension | None, list[TemporalDimension]]:
    """The temporal column to use, or the choices to ask a person about.

    Returns `(chosen, alternatives)`. A `None` choice with several alternatives
    means the question is genuinely ambiguous -- "projects last year" over a
    table holding a start date, a close date and a created date -- and guessing
    between them produces an answer that is confidently about the wrong thing.

    The order of preference is deliberate: an explicit binding, then what the
    question names, then a reviewer's default, then the single event date.
    Asking comes last, because asking about something the question already
    answered is its own kind of wrong.
    """
    available = candidates_for_tables(dimensions, tables)
    if requested_id is not None:
        chosen = next(
            (item for item in available if item.id == requested_id), None
        )
        if chosen is not None:
            return chosen, []

    named = named_by_question(available, question) if question else []
    if len(named) == 1:
        return named[0], []
    # Narrow to what the question named before falling back to the rest: a
    # question about invoices should not be offered the payroll date as an
    # alternative, and should not be asked at all when it named one date.
    considered = named if len(named) > 1 else available

    marked = [item for item in considered if item.is_default_for_entity]
    if len(marked) == 1:
        return marked[0], []
    events = [item for item in considered if item.role is TemporalRole.EVENT_TIME]
    if len(events) == 1:
        return events[0], []
    if len(considered) == 1:
        return considered[0], []
    return None, considered


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'
