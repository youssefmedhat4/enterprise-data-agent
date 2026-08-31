"""The knowledge layer a running API actually uses.

Built once per process at startup and shared by every request. Whether it is
persistent is decided by configuration, never by whether a connection happened
to succeed: when `KNOWLEDGE_STORAGE=postgres` the stores are PostgreSQL-backed
or startup fails, and only `KNOWLEDGE_STORAGE=memory` — a development and test
setting — yields in-memory stores.

That distinction matters more than it looks. A silent fall back to memory would
keep serving requests while learning state diverged per worker, an approved
metric vanished on the next restart, and a reviewer's decision appeared to have
been recorded when it had not. An outage is recoverable; quietly wrong state
that a human has acted on is not.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from app.config import Settings
from app.embeddings.factory import build_embedding_gateway
from app.knowledge.candidates import InMemoryCandidateStore
from app.knowledge.database import KnowledgeDatabase, KnowledgeDatabaseError
from app.knowledge.datasources import PostgresDataSourceRegistry
from app.knowledge.guidance import InMemoryGuidanceStore
from app.knowledge.jobs import PostgresGenerationJobQueue
from app.knowledge.memory import InMemoryQuestionMemory
from app.knowledge.metrics import InMemoryMetricRegistry, MetricRegistry
from app.knowledge.postgres_candidates import PostgresCandidateStore
from app.knowledge.postgres_guidance import PostgresGuidanceStore
from app.knowledge.postgres_memory import PostgresQuestionMemory
from app.knowledge.postgres_metrics import PostgresMetricRegistry
from app.knowledge.postgres_semantics import PostgresSemanticRepository
from app.knowledge.retrieval import MetricRetriever
from app.knowledge.seed import (
    DEFAULT_DATA_SOURCE_ID,
    registered_metrics_for_default_datasource,
)

logger = logging.getLogger(__name__)


class SemanticRepository(Protocol):
    async def load(
        self, data_source_id: UUID, *, schema_fingerprint: str = ...
    ) -> Any: ...

    async def save(self, model: Any) -> None: ...


@dataclass(slots=True)
class KnowledgeRuntime:
    """Every knowledge collaborator a request may need, already constructed."""

    registry: MetricRegistry
    memory: Any
    candidates: Any
    guidance: Any
    semantics: SemanticRepository | None
    jobs: PostgresGenerationJobQueue | None
    data_sources: Any | None
    retriever: MetricRetriever
    database: KnowledgeDatabase | None
    persistent: bool

    async def reindex(
        self, data_source_id: UUID = DEFAULT_DATA_SOURCE_ID
    ) -> None:
        """Rebuild retrieval for one datasource.

        Called after a metric is certified, deprecated or edited. Without it a
        newly certified metric would stay invisible until restart, and a
        deprecated one would keep being retrieved.
        """
        await self.retriever.index(
            data_source_id, await self.registry.certified(data_source_id)
        )

    async def close(self) -> None:
        if self.database is not None:
            await self.database.close()


async def build_knowledge_runtime(
    settings: Settings,
    *,
    data_source_id: UUID = DEFAULT_DATA_SOURCE_ID,
) -> KnowledgeRuntime:
    """Construct the knowledge layer for this process.

    Raises rather than degrading when persistent storage is configured and
    unavailable.
    """
    if settings.knowledge_storage == "memory":
        logger.info("knowledge storage: in-memory (development configuration)")
        return await _in_memory_runtime(settings, data_source_id)

    database = KnowledgeDatabase.from_settings(settings)
    await database.initialize()
    pool = database.pool

    # The default datasource must exist before metrics can reference it.
    sources = PostgresDataSourceRegistry(pool)
    await sources.ensure_default(data_source_id)

    registry = PostgresMetricRegistry(pool)
    await _seed_default_metrics(registry, data_source_id)

    retriever = MetricRetriever(build_embedding_gateway(settings))
    await retriever.index(
        data_source_id, await registry.certified(data_source_id)
    )
    logger.info("knowledge storage: postgres")
    return KnowledgeRuntime(
        registry=registry,
        memory=PostgresQuestionMemory(pool),
        candidates=PostgresCandidateStore(pool),
        guidance=PostgresGuidanceStore(pool),
        semantics=PostgresSemanticRepository(pool),
        jobs=PostgresGenerationJobQueue(pool),
        data_sources=sources,
        retriever=retriever,
        database=database,
        persistent=True,
    )


async def _in_memory_runtime(
    settings: Settings, data_source_id: UUID
) -> KnowledgeRuntime:
    registry = InMemoryMetricRegistry(
        registered_metrics_for_default_datasource(data_source_id)
    )
    retriever = MetricRetriever(build_embedding_gateway(settings))
    await retriever.index(
        data_source_id, await registry.certified(data_source_id)
    )
    return KnowledgeRuntime(
        registry=registry,
        memory=InMemoryQuestionMemory(),
        candidates=InMemoryCandidateStore(),
        guidance=InMemoryGuidanceStore(),
        semantics=None,
        jobs=None,
        data_sources=None,
        retriever=retriever,
        database=None,
        persistent=False,
    )


async def _seed_default_metrics(
    registry: MetricRegistry, data_source_id: UUID
) -> int:
    """Seed the demo catalog, without overwriting reviewed work.

    A metric already present is left alone. Re-seeding on every start would
    reset a reviewer's edit or resurrect one they deprecated, which would make
    the registry unreliable in exactly the way persistence is meant to fix.
    """
    written = 0
    for metric in registered_metrics_for_default_datasource(data_source_id):
        if await registry.get(data_source_id, metric.metric_key) is not None:
            continue
        await registry.upsert(metric)
        written += 1
    if written:
        logger.info("seeded %d default metrics", written)
    return written


__all__ = [
    "KnowledgeDatabaseError",
    "KnowledgeRuntime",
    "build_knowledge_runtime",
]
