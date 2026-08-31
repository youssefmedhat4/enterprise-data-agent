"""Dynamic entity-value resolution.

Replaces hardcoded value lists such as `("Engineering", "Sales", ...)`. Those
only ever worked for one database; anything else silently failed to resolve.

Values are resolved against the **live datasource** through the observed values
already attached to authorized schema metadata. That matters for two reasons:
the caller only ever sees values from tables authorization already granted, so
resolution is not an information side channel; and nothing is cached across
datasources, so datasource A's values can never satisfy a question asked of B.

The resolver returns candidates. It never invents one, and a language model
never supplies one — the model may only choose among values returned here.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

from app.data.gateway import TableMetadata

type ResolutionStrategy = Literal["canonical", "exact", "prefix", "fuzzy"]

#: Below this similarity a fuzzy candidate is noise rather than a near-miss.
_FUZZY_THRESHOLD = 0.82

#: A resolution offering more than this many candidates is not a useful
#: clarification prompt; it means the question did not narrow anything.
_MAX_AMBIGUOUS = 5


def normalize_value(text: str) -> str:
    """Casefold, strip accents, and collapse whitespace and punctuation."""
    decomposed = unicodedata.normalize("NFKD", text)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    collapsed = re.sub(r"[\s_\-]+", " ", without_marks)
    return collapsed.casefold().strip()


@dataclass(frozen=True, slots=True)
class EntityCandidate:
    """One possible real value, always traceable to where it came from."""

    value: str
    schema_name: str
    table_name: str
    column: str
    strategy: ResolutionStrategy
    confidence: float

    @property
    def qualified_column(self) -> str:
        return f"{self.schema_name}.{self.table_name}.{self.column}"


@dataclass(frozen=True, slots=True)
class EntityResolution:
    """Outcome of resolving user text against a datasource."""

    candidates: tuple[EntityCandidate, ...]

    @property
    def resolved(self) -> EntityCandidate | None:
        """The single confident match, or None when absent or ambiguous."""
        return self.candidates[0] if len(self.candidates) == 1 else None

    @property
    def is_ambiguous(self) -> bool:
        return len(self.candidates) > 1

    @property
    def is_unresolved(self) -> bool:
        return not self.candidates


class EntityResolver:
    """Resolves user text to real values in one authorized datasource."""

    def resolve(
        self,
        *,
        user_text: str,
        authorized_tables: list[TableMetadata],
        concept: str | None = None,
    ) -> EntityResolution:
        """Resolve `user_text` against observed values in authorized tables.

        `authorized_tables` must already be filtered to what the caller may
        read; this method does no authorization of its own and must never be
        handed the full schema.

        `concept` optionally narrows resolution to the columns backing one
        semantic concept, such as a governed dimension.

        Until the confirmed semantic model supplies an explicit
        concept-to-column mapping, a column is considered to back a concept when
        the concept name appears in the column name or in its table name — which
        covers both `employees.department` and `departments.name`. This is a
        bridge, not the destination: a confirmed mapping should replace it.
        """
        normalized_question = normalize_value(user_text)
        if not normalized_question:
            return EntityResolution(candidates=())

        pool = list(self._candidate_values(authorized_tables, concept))

        for strategy in ("canonical", "exact", "prefix", "fuzzy"):
            matches = self._match(strategy, normalized_question, pool)
            if matches:
                # Ladder short-circuits: a weaker strategy never overrides a
                # stronger one that already matched.
                return EntityResolution(candidates=tuple(matches[:_MAX_AMBIGUOUS]))
        return EntityResolution(candidates=())

    def _candidate_values(
        self,
        tables: list[TableMetadata],
        concept: str | None,
    ) -> list[tuple[str, str, str, str]]:
        """(value, schema, table, column) for every observed value in scope."""
        pool: list[tuple[str, str, str, str]] = []
        needle = normalize_value(concept) if concept else None
        for table in tables:
            table_matches = needle is not None and needle in normalize_value(
                table.table_name
            )
            for column in table.column_metadata:
                if needle is not None and not (
                    table_matches or needle in normalize_value(column.name)
                ):
                    continue
                # Only values the database actually reported. A column whose
                # cardinality exceeded the configured bound reports none, which
                # keeps high-cardinality personal data out of resolution.
                for value in column.observed_values:
                    pool.append((value, table.schema_name, table.table_name, column.name))
        return pool

    def _match(
        self,
        strategy: str,
        normalized_question: str,
        pool: list[tuple[str, str, str, str]],
    ) -> list[EntityCandidate]:
        matches: list[EntityCandidate] = []
        seen: set[tuple[str, str]] = set()
        for value, schema_name, table_name, column in pool:
            normalized_value = normalize_value(value)
            if not normalized_value:
                continue
            confidence = self._score(strategy, normalized_question, normalized_value, value)
            if confidence is None:
                continue
            key = (normalized_value, column)
            if key in seen:
                continue
            seen.add(key)
            matches.append(
                EntityCandidate(
                    value=value,
                    schema_name=schema_name,
                    table_name=table_name,
                    column=column,
                    strategy=strategy,  # type: ignore[arg-type]
                    confidence=confidence,
                )
            )
        # Longer values first: "People Operations" should beat "Operations"
        # when the question contains the longer phrase.
        matches.sort(key=lambda match: (-match.confidence, -len(match.value), match.value))
        return matches

    def _score(
        self,
        strategy: str,
        question: str,
        normalized_value: str,
        raw_value: str,
    ) -> float | None:
        if strategy == "canonical":
            # An identifier-like token quoted verbatim, e.g. PRJ-14 or a UUID.
            if not re.fullmatch(r"[a-z0-9][a-z0-9\-_.]{2,}", normalized_value):
                return None
            if any(ch.isdigit() for ch in normalized_value) and self._contains_word(
                question, normalized_value
            ):
                return 1.0
            return None
        if strategy == "exact":
            return 1.0 if self._contains_word(question, normalized_value) else None
        if strategy == "prefix":
            # The question names a leading portion of the value, e.g. "Falcon"
            # for "Falcon Migration". Require enough characters to be meaningful.
            head = normalized_value.split(" ")[0]
            if len(head) >= 4 and self._contains_word(question, head):
                return 0.9
            return None
        similarity = max(
            (
                SequenceMatcher(None, token, normalized_value).ratio()
                for token in self._windows(question, normalized_value)
            ),
            default=0.0,
        )
        return similarity if similarity >= _FUZZY_THRESHOLD else None

    @staticmethod
    def _contains_word(question: str, value: str) -> bool:
        return re.search(rf"(?<!\w){re.escape(value)}(?!\w)", question) is not None

    @staticmethod
    def _windows(question: str, value: str) -> list[str]:
        """Word windows of the question the length of the candidate value."""
        words = question.split(" ")
        width = max(1, len(value.split(" ")))
        return [" ".join(words[i : i + width]) for i in range(len(words))]
