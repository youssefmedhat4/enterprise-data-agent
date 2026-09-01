"""Five more kinds of reusable knowledge, and where each one is promoted to.

The learning loop could already propose a metric, a query example and a business
rule. These are the other things a recurring question reveals: a population
people keep asking for, language they keep using, a name for something, a
relationship the database never declared, and a description that keeps having to
be explained.

Two rules shape all of them.

Nothing a model writes is trusted as logic. A filter is a bounded predicate over
attributes a reviewer confirmed, not a SQL fragment; a join rule names two
confirmed attributes rather than a join clause; a synonym points at meaning that
already exists rather than creating any.

Approval writes to a real store. Leaving the knowledge inside an approved
candidate row would make approval a status change with no effect, and the
runtime would go on not knowing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: A predicate deeper than this stops being a population anyone can read.
MAX_PREDICATE_DEPTH = 4

#: A synonym set larger than this is a thesaurus, not a naming decision.
MAX_PHRASES = 20


class FilterOperator(StrEnum):
    EQ = "EQ"
    NEQ = "NEQ"
    IN = "IN"
    NOT_IN = "NOT_IN"
    IS_NULL = "IS_NULL"
    IS_NOT_NULL = "IS_NOT_NULL"


class LearnedKnowledgeError(RuntimeError):
    """Raised when learned knowledge cannot be validated or promoted."""


class StrictNode(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AttributePredicate(StrictNode):
    """One comparison against a confirmed semantic attribute.

    The attribute is named by its reviewed concept, not by a column: a model
    that could name columns could name any column.
    """

    kind: Literal["compare"] = "compare"
    concept: str = Field(min_length=1, max_length=200)
    operator: FilterOperator
    values: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _values_match_the_operator(self) -> AttributePredicate:
        needs_values = self.operator not in {
            FilterOperator.IS_NULL,
            FilterOperator.IS_NOT_NULL,
        }
        if needs_values and not self.values:
            raise ValueError(f"{self.operator.value} needs at least one value.")
        if not needs_values and self.values:
            raise ValueError(f"{self.operator.value} takes no values.")
        if self.operator in {FilterOperator.EQ, FilterOperator.NEQ} and len(
            self.values
        ) != 1:
            raise ValueError(f"{self.operator.value} takes exactly one value.")
        return self


class PredicateGroup(StrictNode):
    """AND or OR over other predicates. Bounded, and shallow by design."""

    kind: Literal["group"] = "group"
    operator: Literal["AND", "OR"]
    children: list[FilterPredicate] = Field(default_factory=list)

    @model_validator(mode="after")
    def _needs_children(self) -> PredicateGroup:
        if len(self.children) < 2:
            raise ValueError("A group needs at least two predicates.")
        return self


type FilterPredicate = AttributePredicate | PredicateGroup

PredicateGroup.model_rebuild()


def predicate_depth(predicate: FilterPredicate) -> int:
    if isinstance(predicate, PredicateGroup):
        return 1 + max(predicate_depth(child) for child in predicate.children)
    return 1


def referenced_concepts(predicate: FilterPredicate) -> set[str]:
    if isinstance(predicate, PredicateGroup):
        concepts: set[str] = set()
        for child in predicate.children:
            concepts |= referenced_concepts(child)
        return concepts
    return {predicate.concept}


def describe_predicate(predicate: FilterPredicate) -> str:
    """A reviewer-facing rendering. Never SQL, and never executed."""
    if isinstance(predicate, PredicateGroup):
        joined = f" {predicate.operator} ".join(
            describe_predicate(child) for child in predicate.children
        )
        return f"({joined})"
    if predicate.operator in {FilterOperator.IS_NULL, FilterOperator.IS_NOT_NULL}:
        return f"{predicate.concept} {predicate.operator.value.replace('_', ' ').lower()}"
    values = ", ".join(predicate.values)
    symbol = {
        FilterOperator.EQ: "is",
        FilterOperator.NEQ: "is not",
        FilterOperator.IN: "is one of",
        FilterOperator.NOT_IN: "is none of",
    }[predicate.operator]
    return f"{predicate.concept} {symbol} {values}"


# --- what the worker may propose --------------------------------------------


class StrictProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FilterProposal(StrictProposal):
    """A population people keep asking for, as structure rather than SQL."""

    candidate_type: Literal["FILTER"] = "FILTER"
    display_name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    predicate: FilterPredicate


class SynonymProposal(StrictProposal):
    """Language that points at meaning the review already confirmed."""

    candidate_type: Literal["SYNONYM"] = "SYNONYM"
    display_name: str = Field(min_length=1, max_length=200)
    target_kind: Literal["concept", "metric", "dimension"]
    target: str = Field(min_length=1, max_length=200)
    phrases: list[str] = Field(min_length=1, max_length=MAX_PHRASES)


class EntityAliasProposal(StrictProposal):
    """What people call an entity, or one known member of it."""

    candidate_type: Literal["ENTITY_ALIAS"] = "ENTITY_ALIAS"
    display_name: str = Field(min_length=1, max_length=200)
    entity_name: str = Field(min_length=1, max_length=200)
    alias: str = Field(min_length=1, max_length=200)
    #: Optional. Null means the alias names the entity, not one of its rows --
    #: which is the safe default, because binding a row identity is the live
    #: entity lookup's job and not something to fix in advance.
    canonical_key: str | None = Field(default=None, max_length=200)


class JoinRuleProposal(StrictProposal):
    """A relationship the database does not declare, named semantically."""

    candidate_type: Literal["JOIN_RULE"] = "JOIN_RULE"
    display_name: str = Field(min_length=1, max_length=200)
    left_concept: str = Field(min_length=1, max_length=200)
    right_concept: str = Field(min_length=1, max_length=200)
    cardinality: Literal["ONE_TO_ONE", "MANY_TO_ONE", "ONE_TO_MANY"] = "MANY_TO_ONE"


class DescriptionProposal(StrictProposal):
    """A better description for something already confirmed."""

    candidate_type: Literal["DESCRIPTION_IMPROVEMENT"] = "DESCRIPTION_IMPROVEMENT"
    display_name: str = Field(min_length=1, max_length=200)
    subject_kind: Literal["entity", "attribute", "metric"]
    subject: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)


# --- approved stores ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ApprovedFilter:
    data_source_id: UUID
    name: str
    predicate: dict[str, Any]
    description: str = ""
    id: UUID = field(default_factory=uuid4)
    source_candidate_id: UUID | None = None
    approved_by: str | None = None
    approved_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class ApprovedSynonym:
    data_source_id: UUID
    target_kind: str
    target: str
    phrases: tuple[str, ...]
    id: UUID = field(default_factory=uuid4)
    source_candidate_id: UUID | None = None
    approved_by: str | None = None
    approved_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class ApprovedEntityAlias:
    data_source_id: UUID
    entity_id: UUID
    alias: str
    canonical_key: str | None = None
    id: UUID = field(default_factory=uuid4)
    source_candidate_id: UUID | None = None
    approved_by: str | None = None
    approved_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class ApprovedJoinRule:
    data_source_id: UUID
    left_attribute_id: UUID
    right_attribute_id: UUID
    cardinality: str = "MANY_TO_ONE"
    id: UUID = field(default_factory=uuid4)
    source_candidate_id: UUID | None = None
    approved_by: str | None = None
    approved_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class DescriptionRevision:
    data_source_id: UUID
    subject_kind: str
    subject_id: UUID
    description: str
    previous_description: str = ""
    id: UUID = field(default_factory=uuid4)
    source_candidate_id: UUID | None = None
    approved_by: str | None = None
    approved_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class LearnedKnowledgeStore(Protocol):
    """Where each kind of approved knowledge lives."""

    async def add_filter(self, item: ApprovedFilter) -> ApprovedFilter: ...

    async def filters(self, data_source_id: UUID) -> list[ApprovedFilter]: ...

    async def add_synonym(self, item: ApprovedSynonym) -> ApprovedSynonym: ...

    async def synonyms(self, data_source_id: UUID) -> list[ApprovedSynonym]: ...

    async def add_alias(self, item: ApprovedEntityAlias) -> ApprovedEntityAlias: ...

    async def aliases(self, data_source_id: UUID) -> list[ApprovedEntityAlias]: ...

    async def add_join_rule(self, item: ApprovedJoinRule) -> ApprovedJoinRule: ...

    async def join_rules(self, data_source_id: UUID) -> list[ApprovedJoinRule]: ...

    async def add_description(
        self, item: DescriptionRevision
    ) -> DescriptionRevision: ...

    async def descriptions(
        self, data_source_id: UUID
    ) -> list[DescriptionRevision]: ...


class InMemoryLearnedKnowledgeStore(LearnedKnowledgeStore):
    def __init__(self) -> None:
        self._filters: list[ApprovedFilter] = []
        self._synonyms: list[ApprovedSynonym] = []
        self._aliases: list[ApprovedEntityAlias] = []
        self._joins: list[ApprovedJoinRule] = []
        self._descriptions: list[DescriptionRevision] = []

    async def add_filter(self, item: ApprovedFilter) -> ApprovedFilter:
        self._filters.append(item)
        return item

    async def filters(self, data_source_id: UUID) -> list[ApprovedFilter]:
        return [item for item in self._filters if item.data_source_id == data_source_id]

    async def add_synonym(self, item: ApprovedSynonym) -> ApprovedSynonym:
        self._synonyms.append(item)
        return item

    async def synonyms(self, data_source_id: UUID) -> list[ApprovedSynonym]:
        return [
            item for item in self._synonyms if item.data_source_id == data_source_id
        ]

    async def add_alias(self, item: ApprovedEntityAlias) -> ApprovedEntityAlias:
        self._aliases.append(item)
        return item

    async def aliases(self, data_source_id: UUID) -> list[ApprovedEntityAlias]:
        return [item for item in self._aliases if item.data_source_id == data_source_id]

    async def add_join_rule(self, item: ApprovedJoinRule) -> ApprovedJoinRule:
        self._joins.append(item)
        return item

    async def join_rules(self, data_source_id: UUID) -> list[ApprovedJoinRule]:
        return [item for item in self._joins if item.data_source_id == data_source_id]

    async def add_description(self, item: DescriptionRevision) -> DescriptionRevision:
        self._descriptions.append(item)
        return item

    async def descriptions(self, data_source_id: UUID) -> list[DescriptionRevision]:
        return [
            item
            for item in self._descriptions
            if item.data_source_id == data_source_id
        ]
