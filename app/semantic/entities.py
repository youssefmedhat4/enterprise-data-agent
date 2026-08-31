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
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from app.data.gateway import TableMetadata

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps semantic <- knowledge
    from app.knowledge.discovery import SemanticModel

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
class ConceptBinding:
    """A confirmed mapping from a business concept to a real column.

    Produced only from CONFIRMED semantic entities and attributes, so a binding
    is a reviewed assertion about meaning rather than a guess about naming.
    """

    schema_name: str
    table_name: str
    column: str
    is_identifier: bool
    canonical_column: str | None

    @property
    def qualified_column(self) -> str:
        return f"{self.schema_name}.{self.table_name}.{self.column}"


def confirmed_bindings(model: SemanticModel, concept: str) -> tuple[ConceptBinding, ...]:
    """Columns backing `concept`, from confirmed semantic mappings only.

    A concept may name an attribute ("Annual Base Salary"), which binds to the
    column that carries it, or an entity ("Organizational Unit"), which binds to
    that entity's confirmed columns. Both forms also carry the entity's
    identifier column, so a caller can filter by canonical key rather than by a
    display label.

    PROPOSED, REJECTED and STALE knowledge is excluded, so unreviewed or
    invalidated mappings can never steer resolution.
    """
    entities_by_id = {entity.id: entity for entity in model.confirmed_entities()}
    confirmed_attributes = model.confirmed_attributes()

    canonical_by_entity: dict[UUID, str] = {}
    for attribute in confirmed_attributes:
        entity = entities_by_id.get(attribute.entity_id)
        if entity is None or not attribute.is_identifier:
            continue
        canonical_by_entity.setdefault(
            attribute.entity_id,
            f"{entity.source_schema}.{entity.source_table}.{attribute.source_column}",
        )

    selected = list(model.attributes_for_concept(concept))
    entity_match = model.entity_for_concept(concept)
    if entity_match is not None:
        selected.extend(
            attribute
            for attribute in confirmed_attributes
            if attribute.entity_id == entity_match.id
        )

    bindings: list[ConceptBinding] = []
    seen: set[tuple[str, str, str]] = set()
    for attribute in selected:
        entity = entities_by_id.get(attribute.entity_id)
        if entity is None:
            continue
        key = (entity.source_schema, entity.source_table, attribute.source_column)
        if key in seen:
            continue
        seen.add(key)
        bindings.append(
            ConceptBinding(
                schema_name=entity.source_schema,
                table_name=entity.source_table,
                column=attribute.source_column,
                is_identifier=attribute.is_identifier,
                canonical_column=canonical_by_entity.get(attribute.entity_id),
            )
        )
    return tuple(bindings)


@dataclass(frozen=True, slots=True)
class EntityCandidate:
    """One possible real value, always traceable to where it came from."""

    value: str
    schema_name: str
    table_name: str
    column: str
    strategy: ResolutionStrategy
    confidence: float
    canonical_column: str | None = None

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
        semantic_model: SemanticModel | None = None,
    ) -> EntityResolution:
        """Resolve `user_text` against observed values in authorized tables.

        `authorized_tables` must already be filtered to what the caller may
        read; this method does no authorization of its own and must never be
        handed the full schema. Confirmed mappings decide which *columns* are
        searched, but every value still comes from `authorized_tables`, so a
        mapping onto a table the caller cannot read yields nothing.

        `concept` narrows resolution to the columns backing one business
        concept. When `semantic_model` is supplied, those columns come from
        CONFIRMED semantic mappings and nothing else: if the concept has no
        confirmed binding, resolution returns no candidates rather than falling
        back to guessing, because an unreviewed concept has no agreed meaning.

        Without a semantic model the resolver keeps its original behaviour and
        treats a concept as a name hint against table and column names. That
        path exists only for callers that have not yet onboarded a semantic
        model; it is a fallback, not the intended route.
        """
        normalized_question = normalize_value(user_text)
        if not normalized_question:
            return EntityResolution(candidates=())

        if semantic_model is not None and concept is not None:
            bindings = confirmed_bindings(semantic_model, concept)
            if not bindings:
                return EntityResolution(candidates=())
            pool = self._values_for_bindings(authorized_tables, bindings)
        else:
            pool = self._candidate_values(authorized_tables, concept)

        for strategy in ("canonical", "exact", "prefix", "fuzzy"):
            matches = self._match(strategy, normalized_question, pool)
            if matches:
                # Ladder short-circuits: a weaker strategy never overrides a
                # stronger one that already matched.
                return EntityResolution(candidates=tuple(matches[:_MAX_AMBIGUOUS]))
        return EntityResolution(candidates=())

    def _values_for_bindings(
        self,
        tables: list[TableMetadata],
        bindings: tuple[ConceptBinding, ...],
    ) -> list[tuple[str, str, str, str, str | None]]:
        """Observed values for exactly the confirmed columns, nothing wider."""
        by_column = {
            (binding.schema_name, binding.table_name, binding.column): binding
            for binding in bindings
        }
        pool: list[tuple[str, str, str, str, str | None]] = []
        for table in tables:
            for column in table.column_metadata:
                binding = by_column.get(
                    (table.schema_name, table.table_name, column.name)
                )
                if binding is None:
                    continue
                for value in column.observed_values:
                    pool.append(
                        (
                            value,
                            table.schema_name,
                            table.table_name,
                            column.name,
                            binding.canonical_column,
                        )
                    )
        return pool

    def _candidate_values(
        self,
        tables: list[TableMetadata],
        concept: str | None,
    ) -> list[tuple[str, str, str, str, str | None]]:
        """(value, schema, table, column, canonical) for values in scope."""
        pool: list[tuple[str, str, str, str, str | None]] = []
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
                    pool.append(
                        (value, table.schema_name, table.table_name, column.name, None)
                    )
        return pool

    def _match(
        self,
        strategy: str,
        normalized_question: str,
        pool: list[tuple[str, str, str, str, str | None]],
    ) -> list[EntityCandidate]:
        matches: list[EntityCandidate] = []
        seen: set[tuple[str, str]] = set()
        for value, schema_name, table_name, column, canonical_column in pool:
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
                    canonical_column=canonical_column,
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
