"""Construction of the governed knowledge layer.

Keeps database and embedding wiring out of graph nodes. A node receives
abstractions, so tests can inject a fake registry, retriever, or model without
a database or a network.

The registry is the runtime authority for governed metric definitions. The
Python catalog in `app.metrics.catalog` is seed material for it and is not
consulted at runtime: `bootstrap_default_datasource` copies those definitions
into the registry once, idempotently, and everything afterwards reads the
registry.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from app.config import Settings
from app.embeddings.factory import build_embedding_gateway
from app.knowledge.metrics import InMemoryMetricRegistry, MetricRegistry
from app.knowledge.planner import MetricIntentPlanner
from app.knowledge.postgres_metrics import PostgresMetricRegistry
from app.knowledge.retrieval import MetricRetriever
from app.knowledge.seed import (
    DEFAULT_DATA_SOURCE_ID,
    registered_metrics_for_default_datasource,
)
from app.llm.gateway import LLMGateway

logger = logging.getLogger(__name__)


def build_metric_registry(
    settings: Settings,
    *,
    pool: AsyncConnectionPool[Any] | None = None,
) -> MetricRegistry:
    """The persistent registry when an internal database is configured.

    Falls back to the in-memory registry only when there is no internal
    database to persist to, which is the development and test shape. The
    fallback is seeded from the same catalog, so both shapes agree on what
    exists rather than differing silently.
    """
    if pool is not None:
        return PostgresMetricRegistry(pool)
    if settings.checkpoint_database_url is not None:
        # An internal database is configured but no pool was supplied. Warn
        # rather than fail: the caller still gets a working, correctly seeded
        # registry, and refusing here would take the whole analytics API down
        # over a wiring gap that does not affect correctness of an answer.
        logger.warning(
            "metric registry falling back to in-memory: no connection pool supplied"
        )
    return InMemoryMetricRegistry(
        registered_metrics_for_default_datasource(DEFAULT_DATA_SOURCE_ID)
    )


async def bootstrap_default_datasource(
    registry: MetricRegistry,
    *,
    data_source_id: UUID = DEFAULT_DATA_SOURCE_ID,
) -> int:
    """Seed the catalog into the registry, idempotently.

    Upsert rather than insert, so restarting does not duplicate or fail, and so
    editing a seed definition propagates on the next start. Returns how many
    definitions were written, which callers log rather than discard.
    """
    seeded = registered_metrics_for_default_datasource(data_source_id)
    for metric in seeded:
        await registry.upsert(metric)
    return len(seeded)


async def build_metric_retriever(
    settings: Settings,
    registry: MetricRegistry,
    *,
    data_source_id: UUID = DEFAULT_DATA_SOURCE_ID,
) -> MetricRetriever:
    """A retriever indexed over one datasource's certified metrics.

    Indexing embeds metric documents, which is database-derived content sent to
    a third party when the provider is cloud. `build_embedding_gateway` applies
    the cloud-data guard, so there is no embedding loophole around it.
    """
    retriever = MetricRetriever(build_embedding_gateway(settings))
    await retriever.index(data_source_id, await registry.certified(data_source_id))
    return retriever


def build_metric_intent_planner(
    retriever: MetricRetriever,
    llm_gateway: LLMGateway,
    *,
    model_alias: str = "analytics-general",
) -> MetricIntentPlanner:
    """Per-request planner: it must use the request's selected model."""
    return MetricIntentPlanner(
        retriever=retriever, llm=llm_gateway, model_alias=model_alias
    )
