"""Datasource-scoped question memory and recurring-question clustering.

This is controlled product data, not observability. It records *how people ask*
and *what shape of analysis answered them*, so that a recurring need can later
be proposed as reusable knowledge.

It deliberately does not record what the answer was. No result rows, no measure
values, no answer text. A question asked today is always executed against the
live database; memory can suggest how to interpret a question, never what the
number is. That boundary is why `QuestionEvent` has no field capable of
carrying a result.

Clustering is structural first. Two events join the same cluster when their
fingerprints match exactly, which means the same metrics at the same grain.
Embedding similarity is recorded alongside, and is used to summarise a cluster
and to judge whether new wording is really the same need, but it never
overrides structure: questions that are phrased alike but analysed differently
must not merge, because a cluster that mixes grains cannot produce one correct
reusable definition.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from app.embeddings.gateway import EmbeddingVector
from app.knowledge.fingerprints import normalize_question


@dataclass(frozen=True, slots=True)
class QuestionEvent:
    """One terminal analytics request, reduced to what is safe to remember.

    There is intentionally no field for rows, measures, or answer text. Trust
    is explicit: only an event that succeeded, validated and grounded may later
    be used as evidence for reusable knowledge.
    """

    data_source_id: UUID
    question_text: str
    structural_fingerprint: str
    route: str
    id: UUID = field(default_factory=uuid4)
    thread_id: str | None = None
    model_profile: str | None = None
    metric_keys: tuple[str, ...] = ()
    semantic_plan_summary: str | None = None
    success: bool = False
    validated: bool = False
    grounded: bool = False
    embedding: EmbeddingVector | None = None
    embedding_provider: str | None = None
    embedding_model: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def normalized_question(self) -> str:
        return normalize_question(self.question_text)

    @property
    def is_trustworthy_evidence(self) -> bool:
        """Whether this event may support a reusable knowledge proposal.

        A blocked, clarifying, failed or ungrounded request still deserves to be
        remembered for operational insight, but must never become the basis of a
        certified metric or an approved example.
        """
        return self.success and self.validated and self.grounded


@dataclass(frozen=True, slots=True)
class QuestionCluster:
    """A recurring analytical need within one datasource."""

    data_source_id: UUID
    structural_fingerprint: str
    canonical_summary: str
    id: UUID = field(default_factory=uuid4)
    occurrence_count: int = 0
    successful_count: int = 0
    first_seen_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_seen_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    status: str = "ACTIVE"
    representative_embedding: EmbeddingVector | None = None
    embedding_provider: str | None = None
    embedding_model: str | None = None

    def is_eligible_for_proposal(
        self, *, min_occurrences: int, min_successful: int
    ) -> bool:
        return (
            self.status == "ACTIVE"
            and self.occurrence_count >= min_occurrences
            and self.successful_count >= min_successful
        )


class QuestionMemory(Protocol):
    """Datasource-scoped storage for question events and their clusters."""

    async def record(self, event: QuestionEvent) -> QuestionCluster: ...

    async def clusters(self, data_source_id: UUID) -> list[QuestionCluster]: ...

    async def events_for_cluster(
        self, data_source_id: UUID, cluster_id: UUID, *, limit: int = 20
    ) -> list[QuestionEvent]: ...


class InMemoryQuestionMemory:
    """Reference implementation. Keyed by datasource, so isolation is structural.

    A cluster is addressed by `(data_source_id, structural_fingerprint)`, so the
    same wording asked of two databases cannot reach the same cluster: the
    datasource is part of the key rather than a filter applied afterwards.
    """

    def __init__(self) -> None:
        self._events: dict[UUID, list[QuestionEvent]] = {}
        self._clusters: dict[tuple[UUID, str], QuestionCluster] = {}
        self._members: dict[UUID, list[UUID]] = {}

    async def record(self, event: QuestionEvent) -> QuestionCluster:
        self._events.setdefault(event.data_source_id, []).append(event)
        key = (event.data_source_id, event.structural_fingerprint)
        existing = self._clusters.get(key)
        if existing is None:
            cluster = QuestionCluster(
                data_source_id=event.data_source_id,
                structural_fingerprint=event.structural_fingerprint,
                canonical_summary=event.normalized_question,
                occurrence_count=1,
                successful_count=1 if event.is_trustworthy_evidence else 0,
                first_seen_at=event.created_at,
                last_seen_at=event.created_at,
                representative_embedding=event.embedding,
                embedding_provider=event.embedding_provider,
                embedding_model=event.embedding_model,
            )
        else:
            cluster = QuestionCluster(
                id=existing.id,
                data_source_id=existing.data_source_id,
                structural_fingerprint=existing.structural_fingerprint,
                canonical_summary=existing.canonical_summary,
                occurrence_count=existing.occurrence_count + 1,
                successful_count=existing.successful_count
                + (1 if event.is_trustworthy_evidence else 0),
                first_seen_at=existing.first_seen_at,
                last_seen_at=event.created_at,
                status=existing.status,
                representative_embedding=existing.representative_embedding
                or event.embedding,
                embedding_provider=existing.embedding_provider
                or event.embedding_provider,
                embedding_model=existing.embedding_model or event.embedding_model,
            )
        self._clusters[key] = cluster
        self._members.setdefault(cluster.id, []).append(event.id)
        return cluster

    async def clusters(self, data_source_id: UUID) -> list[QuestionCluster]:
        return [
            cluster
            for (source, _), cluster in self._clusters.items()
            if source == data_source_id
        ]

    async def events_for_cluster(
        self, data_source_id: UUID, cluster_id: UUID, *, limit: int = 20
    ) -> list[QuestionEvent]:
        member_ids = set(self._members.get(cluster_id, []))
        return [
            event
            for event in self._events.get(data_source_id, [])
            if event.id in member_ids
        ][:limit]

    async def eligible_clusters(
        self,
        data_source_id: UUID,
        *,
        min_occurrences: int,
        min_successful: int,
    ) -> list[QuestionCluster]:
        return [
            cluster
            for cluster in await self.clusters(data_source_id)
            if cluster.is_eligible_for_proposal(
                min_occurrences=min_occurrences, min_successful=min_successful
            )
        ]
