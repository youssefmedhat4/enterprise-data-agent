"""Approved query examples and business instructions.

Two kinds of reviewed knowledge that improve reasoning without ever becoming
execution.

An **approved query example** is a question a human confirmed was answered well,
stored with the SQL pattern that answered it. It reaches the model as context,
never as a shortcut: the model still writes SQL for the current question, and
that SQL still passes SQLGlot, current schema validation, current authorization
and the read-only role. Storing a query does not make it trusted to run --
schemas change, permissions differ per caller, and an approved example says
"this shape worked once", not "run this".

A **business instruction** is durable guidance about what a metric means, such
as which employees a payroll figure includes. It is retrieved only when relevant
to the question at hand. Appending every instruction to every prompt would bury
the relevant one and spend context on guidance the question never touches.

Both are datasource-scoped, and both carry the schema fingerprint they were
approved against so a later schema change can mark them stale rather than
letting them silently describe a database that no longer exists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.knowledge.contracts import ApprovalStatus
from app.knowledge.fingerprints import normalize_question

#: Statements that must never appear in a stored example. The database enforces
#: this too; this is the same rule at the application boundary, so a bad example
#: is refused with a clear error rather than a constraint violation.
_MUTATING = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|grant|revoke|create|merge)\b",
    re.IGNORECASE,
)

_WORD = re.compile(r"[a-z0-9]+")


class GuidanceError(RuntimeError):
    """Raised when knowledge cannot be approved or stored."""


@dataclass(frozen=True, slots=True)
class ApprovedQueryExample:
    data_source_id: UUID
    question: str
    query_pattern: str
    id: UUID = field(default_factory=uuid4)
    semantic_plan: str = ""
    schema_fingerprint: str | None = None
    status: ApprovalStatus = ApprovalStatus.CONFIRMED
    source_query_id: str | None = None
    source_cluster_id: UUID | None = None
    approved_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def normalized_question(self) -> str:
        return normalize_question(self.question)

    @property
    def is_usable(self) -> bool:
        return self.status is ApprovalStatus.CONFIRMED


@dataclass(frozen=True, slots=True)
class BusinessInstruction:
    data_source_id: UUID
    title: str
    instruction: str
    id: UUID = field(default_factory=uuid4)
    semantic_concepts: tuple[str, ...] = ()
    metric_keys: tuple[str, ...] = ()
    status: ApprovalStatus = ApprovalStatus.CONFIRMED
    schema_fingerprint: str | None = None
    source_candidate_id: UUID | None = None
    approved_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_usable(self) -> bool:
        return self.status is ApprovalStatus.CONFIRMED


class InMemoryGuidanceStore:
    """Datasource-scoped storage for approved examples and instructions."""

    def __init__(self) -> None:
        self._examples: dict[UUID, list[ApprovedQueryExample]] = {}
        self._instructions: dict[UUID, list[BusinessInstruction]] = {}

    # -- approval -----------------------------------------------------------

    async def approve_example(
        self,
        example: ApprovedQueryExample,
        *,
        was_successful: bool,
        was_validated: bool,
        current_schema_fingerprint: str | None = None,
    ) -> ApprovedQueryExample:
        """Store an example, refusing anything that should not be reusable.

        The originating request must have succeeded and its SQL must have been
        validated: an example is a claim that this shape answered the question
        well, and a failed or unvalidated run is no evidence of that.
        """
        if not was_successful or not was_validated:
            raise GuidanceError(
                "Only a successful, validated request can become an approved example."
            )
        if _MUTATING.search(example.query_pattern):
            raise GuidanceError(
                "An approved example must be a read-only statement."
            )
        if (
            current_schema_fingerprint is not None
            and example.schema_fingerprint is not None
            and example.schema_fingerprint != current_schema_fingerprint
        ):
            raise GuidanceError(
                "The example was validated against a different schema version."
            )
        self._examples.setdefault(example.data_source_id, []).append(example)
        return example

    async def approve_instruction(
        self, instruction: BusinessInstruction
    ) -> BusinessInstruction:
        self._instructions.setdefault(instruction.data_source_id, []).append(
            instruction
        )
        return instruction

    # -- retrieval ----------------------------------------------------------

    async def relevant_examples(
        self,
        data_source_id: UUID,
        question: str,
        *,
        authorized_tables: frozenset[str] | None = None,
        limit: int = 3,
    ) -> list[ApprovedQueryExample]:
        """Usable examples for this datasource, most relevant first.

        `authorized_tables` filters out any example whose SQL touches a table
        the caller may not read. Without it an example could reveal that a table
        exists, which would make approved knowledge an authorization side
        channel.
        """
        candidates = [
            example
            for example in self._examples.get(data_source_id, [])
            if example.is_usable
            and (
                authorized_tables is None
                or _tables_in(example.query_pattern) <= authorized_tables
            )
        ]
        scored = [
            (_overlap(question, example.question), example) for example in candidates
        ]
        relevant = [(score, example) for score, example in scored if score > 0]
        relevant.sort(key=lambda pair: (-pair[0], pair[1].normalized_question))
        return [example for _, example in relevant[:limit]]

    async def relevant_instructions(
        self,
        data_source_id: UUID,
        question: str,
        *,
        metric_keys: frozenset[str] = frozenset(),
        limit: int = 3,
    ) -> list[BusinessInstruction]:
        """Instructions this question actually touches.

        An instruction matches when the plan uses a metric it governs, or when
        the question mentions one of its concepts. An instruction that matches
        neither is not returned, so unrelated guidance never reaches the prompt.
        """
        matches: list[tuple[int, BusinessInstruction]] = []
        for instruction in self._instructions.get(data_source_id, []):
            if not instruction.is_usable:
                continue
            score = 0
            if metric_keys & set(instruction.metric_keys):
                score += 10
            for concept in instruction.semantic_concepts:
                score += _overlap(question, concept)
            if score > 0:
                matches.append((score, instruction))
        matches.sort(key=lambda pair: (-pair[0], pair[1].title))
        return [instruction for _, instruction in matches[:limit]]

    # -- staleness ----------------------------------------------------------

    async def mark_stale_for_schema(
        self, data_source_id: UUID, *, new_schema_fingerprint: str
    ) -> int:
        """Mark examples whose validated schema no longer matches.

        Only examples are marked. A business instruction states what a figure
        means and usually outlives a schema change, so it is marked stale only
        when it recorded a fingerprint of its own -- marking all business
        knowledge stale because an unrelated table changed would destroy
        reviewed work for no reason.
        """
        stale_count = 0
        refreshed: list[ApprovedQueryExample] = []
        for example in self._examples.get(data_source_id, []):
            if (
                example.schema_fingerprint is not None
                and example.schema_fingerprint != new_schema_fingerprint
                and example.status is ApprovalStatus.CONFIRMED
            ):
                refreshed.append(_stale_example(example))
                stale_count += 1
            else:
                refreshed.append(example)
        self._examples[data_source_id] = refreshed
        return stale_count

    async def examples(self, data_source_id: UUID) -> list[ApprovedQueryExample]:
        return list(self._examples.get(data_source_id, []))

    async def instructions(self, data_source_id: UUID) -> list[BusinessInstruction]:
        return list(self._instructions.get(data_source_id, []))


def _stale_example(example: ApprovedQueryExample) -> ApprovedQueryExample:
    return ApprovedQueryExample(
        id=example.id,
        data_source_id=example.data_source_id,
        question=example.question,
        query_pattern=example.query_pattern,
        semantic_plan=example.semantic_plan,
        schema_fingerprint=example.schema_fingerprint,
        status=ApprovalStatus.STALE,
        source_query_id=example.source_query_id,
        source_cluster_id=example.source_cluster_id,
        approved_at=example.approved_at,
    )


def _tables_in(sql: str) -> frozenset[str]:
    import sqlglot
    from sqlglot import exp

    try:
        statement = sqlglot.parse_one(sql)
    except Exception:
        return frozenset()
    if statement is None:
        return frozenset()
    return frozenset(
        f"{table.db}.{table.name}".strip(".").casefold()
        for table in statement.find_all(exp.Table)
        if table.name
    )


#: Suffixes stripped before comparing words, longest first. Not linguistics:
#: just enough that a reviewer writing "current annual payroll" is still found
#: by someone asking what they pay "annually". Exact word matching made
#: retrieval depend on a questioner guessing the reviewer's word forms, which
#: silently withheld approved meaning from the answer that needed it.
_SUFFIXES = ("ingly", "edly", "ing", "ies", "ed", "ly", "es", "s")

#: A stem shorter than this is too generic to be evidence of anything.
_MIN_STEM = 4


def _stems(text: str) -> set[str]:
    stems: set[str] = set()
    for word in _WORD.findall(text.casefold()):
        if len(word) <= 3:
            continue
        stems.add(_stem(word))
    return stems


def _stem(word: str) -> str:
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= _MIN_STEM:
            stripped = word[: -len(suffix)]
            # "ies" -> "y" keeps company/companies together.
            return f"{stripped}y" if suffix == "ies" else stripped
    return word


def _overlap(question: str, text: str) -> int:
    """Shared meaningful stems. Small and deterministic, not a ranker."""
    return len(_stems(question) & _stems(text))
