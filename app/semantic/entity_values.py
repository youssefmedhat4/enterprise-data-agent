"""Live, datasource-scoped entity value resolution.

Schema scans may retain small samples for discovery, but samples are not a
runtime authority: high-cardinality entities would otherwise disappear from
the product.  This module resolves a user-supplied value through confirmed
semantic mappings and the selected :class:`DatabaseGateway`.

The gateway deliberately has no model dependency.  It issues only trusted,
parameterized read-only lookups against the already authorized key and display
columns; it never accepts SQL from a caller and it never enumerates an entity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, cast
from uuid import UUID

from app.data.gateway import DatabaseGateway, TableMetadata
from app.knowledge.contracts import ApprovalStatus
from app.knowledge.discovery import SemanticModel
from app.semantic.entities import (
    EntityCandidate,
    EntityResolution,
    EntityResolver,
    ResolutionStrategy,
)

MAX_ENTITY_CANDIDATES = 5
_MIN_SEARCH_LENGTH = 3


@dataclass(frozen=True, slots=True)
class EntityLookupBinding:
    """One reviewed entity's canonical key and human display attribute."""

    entity_id: UUID
    schema_name: str
    table_name: str
    canonical_column: str
    display_column: str


class EntityValueGateway(Protocol):
    """Resolve user values against the selected datasource at runtime."""

    async def resolve(
        self,
        *,
        user_text: str,
        semantic_model: SemanticModel,
        authorized_tables: list[TableMetadata],
        concept: str | None = None,
    ) -> EntityResolution: ...


def entity_lookup_bindings(
    model: SemanticModel,
    authorized_tables: list[TableMetadata],
    *,
    concept: str | None = None,
) -> tuple[EntityLookupBinding, ...]:
    """Return only confirmed and already-authorized key/display mappings.

    A semantic entity is usable only when a reviewer confirmed both a canonical
    identifier and a display/name attribute.  The small display-name heuristic
    is applied to reviewed *business concept* labels, never physical names.
    That keeps the execution path database-agnostic while avoiding a schema
    migration merely to duplicate information reviewers already provide.
    """
    allowed = {
        (table.schema_name, table.table_name): set(table.columns)
        for table in authorized_tables
    }
    attributes = [
        attribute
        for attribute in model.confirmed_attributes()
        if attribute.status is ApprovalStatus.CONFIRMED
    ]
    bindings: list[EntityLookupBinding] = []
    target = concept.casefold() if concept is not None else None
    for entity in model.confirmed_entities():
        if target is not None and entity.entity_name.casefold() != target:
            continue
        permitted_columns = allowed.get((entity.source_schema, entity.source_table))
        if permitted_columns is None:
            continue
        owned = [attribute for attribute in attributes if attribute.entity_id == entity.id]
        key = next(
            (
                attribute
                for attribute in owned
                if attribute.is_identifier and attribute.source_column in permitted_columns
            ),
            None,
        )
        display = next(
            (
                attribute
                for attribute in owned
                if (
                    not attribute.is_identifier
                    and attribute.source_column in permitted_columns
                    and _is_display_concept(attribute.concept_name)
                )
            ),
            None,
        )
        if key is None or display is None:
            continue
        bindings.append(
            EntityLookupBinding(
                entity_id=entity.id,
                schema_name=entity.source_schema,
                table_name=entity.source_table,
                canonical_column=key.source_column,
                display_column=display.source_column,
            )
        )
    return tuple(bindings)


class DatabaseEntityValueGateway(EntityValueGateway):
    """Entity lookup using a selected, already-authorized database gateway."""

    def __init__(self, database: DatabaseGateway) -> None:
        self._database = database
        self._matcher = EntityResolver()

    async def resolve(
        self,
        *,
        user_text: str,
        semantic_model: SemanticModel,
        authorized_tables: list[TableMetadata],
        concept: str | None = None,
    ) -> EntityResolution:
        bindings = entity_lookup_bindings(
            semantic_model, authorized_tables, concept=concept
        )
        if not bindings:
            return EntityResolution(candidates=())

        candidates: list[EntityCandidate] = []
        for binding in bindings:
            candidates.extend(await self._lookup(binding, user_text))
        return self._matcher.resolve_candidates(user_text=user_text, candidates=candidates)

    async def _lookup(
        self, binding: EntityLookupBinding, user_text: str
    ) -> list[EntityCandidate]:
        """Fetch at most a small candidate set through the match ladder."""
        for strategy, predicate, parameter in _lookup_attempts(user_text):
            sql = _lookup_sql(binding, predicate)
            result = await self._database.execute_readonly(sql, (parameter,))
            if result.rows:
                return [
                    EntityCandidate(
                        value=str(row["display_value"]),
                        schema_name=binding.schema_name,
                        table_name=binding.table_name,
                        column=binding.display_column,
                        strategy=cast("ResolutionStrategy", strategy),
                        confidence=1.0 if strategy in {"canonical", "exact"} else 0.9,
                        canonical_column=(
                            f"{binding.schema_name}.{binding.table_name}."
                            f"{binding.canonical_column}"
                        ),
                        semantic_entity_id=binding.entity_id,
                        canonical_key=str(row["canonical_key"]),
                        display_value=str(row["display_value"]),
                    )
                    for row in result.rows[:MAX_ENTITY_CANDIDATES]
                ]
        return []


def _lookup_attempts(user_text: str) -> tuple[tuple[str, str, str], ...]:
    """Trusted predicates and parameters for the progressive lookup ladder."""
    text = user_text.strip()
    if not text:
        return ()
    # The full question is deliberately not sent to SQL.  Candidate terms are
    # bounded fragments supplied by the user, ordered longest-first, and each
    # lookup is capped.  This prevents accidental table enumeration.
    terms = _search_terms(text)
    if not terms:
        return ()
    attempts: list[tuple[str, str, str]] = []
    for strategy, predicate, escape_like in (
        ("canonical", "lower(key_value) = lower($1)", False),
        ("exact", "lower(display_value) = lower($1)", False),
        ("prefix", "lower(display_value) LIKE lower($1) || '%' ESCAPE '\\'", True),
        ("prefix", "lower(display_value) LIKE '%' || lower($1) || '%' ESCAPE '\\'", True),
    ):
        attempts.extend(
            (strategy, predicate, _escape_like(term) if escape_like else term)
            for term in terms
        )
    return tuple(attempts)


def _search_terms(user_text: str) -> tuple[str, ...]:
    words = re.findall(r"[\w-]+", user_text, flags=re.UNICODE)
    # An entity is commonly the final part of a natural-language request
    # ("payroll for Operations" or "margin for Project 040"). Preserve its
    # trailing phrases before broader grammar, then add individual tokens so a
    # canonical key such as OU2100 remains reachable. This stays bounded below.
    phrases = [user_text.strip()]
    for width in range(min(4, len(words)), 1, -1):
        phrases.append(" ".join(words[-width:]))
    phrases.extend(sorted(words, key=lambda word: (-len(word), word)))
    for width in range(min(4, len(words)), 0, -1):
        phrases.extend(
            " ".join(words[start : start + width])
            for start in range(len(words) - width + 1)
        )
    unique: list[str] = []
    for phrase in phrases:
        clean = phrase.strip()
        if len(clean) < _MIN_SEARCH_LENGTH or clean in unique:
            continue
        unique.append(clean)
    return tuple(unique[:8])


def _lookup_sql(binding: EntityLookupBinding, predicate: str) -> str:
    relation = f"{_quote(binding.schema_name)}.{_quote(binding.table_name)}"
    key = _quote(binding.canonical_column)
    display = _quote(binding.display_column)
    # The outer aliases make predicates independent of physical column names.
    return (
        "SELECT canonical_key, display_value FROM ("
        f"SELECT {key}::text AS canonical_key, {display}::text AS display_value, "
        f"{key}::text AS key_value FROM {relation} "
        f"WHERE {key} IS NOT NULL AND {display} IS NOT NULL"
        f") entity_values WHERE {predicate} "
        "ORDER BY canonical_key LIMIT 5"
    )


def _is_display_concept(concept: str) -> bool:
    lowered = concept.casefold()
    return any(token in lowered for token in ("name", "label", "title", "display"))


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'
