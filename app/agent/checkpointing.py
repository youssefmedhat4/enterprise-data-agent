from __future__ import annotations

from typing import Any, Protocol, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import Settings


class CheckpointConfigurationError(RuntimeError):
    """Raised when the selected conversation persistence adapter is unavailable or unsafe."""


class CheckpointProviderUnavailableError(RuntimeError):
    """Raised when persistent conversation state cannot be initialized."""


class ConversationCheckpointStore(Protocol):
    async def initialize(self) -> None:
        """Initialize storage and required migrations."""

    def saver(self) -> BaseCheckpointSaver[Any]:
        """Return the LangGraph checkpointer owned by this store."""

    async def close(self) -> None:
        """Release storage resources."""


class InMemoryConversationCheckpointStore:
    def __init__(self) -> None:
        self._saver = InMemorySaver()

    async def initialize(self) -> None:
        return None

    def saver(self) -> BaseCheckpointSaver[Any]:
        return self._saver

    async def close(self) -> None:
        return None


class PostgresConversationCheckpointStore:
    """Own a dedicated connection pool and official asynchronous LangGraph saver."""

    def __init__(
        self,
        *,
        connection_string: str,
        min_size: int,
        max_size: int,
        connect_timeout_seconds: float,
    ) -> None:
        self._pool = AsyncConnectionPool(
            connection_string,
            min_size=min_size,
            max_size=max_size,
            open=False,
            timeout=connect_timeout_seconds,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
            },
        )
        self._saver: AsyncPostgresSaver | None = None
        self._connect_timeout_seconds = connect_timeout_seconds
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        try:
            await self._pool.open(wait=True, timeout=self._connect_timeout_seconds)
            self._saver = AsyncPostgresSaver(
                cast(Any, self._pool),
                serde=JsonPlusSerializer(
                    allowed_msgpack_modules=_CHECKPOINT_ALLOWED_TYPES,
                ),
            )
            await self._saver.setup()
        except Exception as exc:
            await self._pool.close()
            raise CheckpointProviderUnavailableError(
                "The configured conversation checkpoint store is unavailable."
            ) from exc
        self._initialized = True

    def saver(self) -> BaseCheckpointSaver[Any]:
        if not self._initialized or self._saver is None:
            raise CheckpointConfigurationError(
                "The PostgreSQL conversation checkpoint store was not initialized."
            )
        return self._saver

    async def close(self) -> None:
        await self._pool.close()
        self._initialized = False
        self._saver = None


def build_conversation_checkpoint_store(settings: Settings) -> ConversationCheckpointStore:
    if settings.conversation_checkpoint_provider == "postgres":
        if settings.checkpoint_database_url is None:
            raise CheckpointConfigurationError(
                "CHECKPOINT_DATABASE_URL is required for PostgreSQL checkpointing."
            )
        return PostgresConversationCheckpointStore(
            connection_string=settings.checkpoint_database_url.get_secret_value(),
            min_size=settings.checkpoint_pool_min_size,
            max_size=settings.checkpoint_pool_max_size,
            connect_timeout_seconds=settings.checkpoint_connect_timeout_seconds,
        )
    if settings.app_env.casefold() in {"production", "staging"}:
        raise CheckpointConfigurationError(
            "In-memory conversation checkpointing is not allowed outside development."
        )
    return InMemoryConversationCheckpointStore()


_CHECKPOINT_ALLOWED_TYPES = {
    ("app.agent.context", "AnalysisPlan"),
    ("app.agent.context", "AnalyticalContext"),
    ("app.agent.context", "ConversationTurn"),
    ("app.agent.context", "TimeRange"),
    ("app.authentication.gateway", "UserIdentity"),
    ("app.authorization.gateway", "AuthorizationDecision"),
    ("app.authorization.gateway", "AuthorizedScopeSummary"),
    ("app.contracts.analytics", "AnalyticalResult"),
    ("app.contracts.analytics", "ChartSpec"),
    ("app.contracts.analytics", "ClaimEvidence"),
    ("app.contracts.analytics", "ExecutionMetadata"),
    ("app.contracts.analytics", "Freshness"),
    ("app.contracts.analytics", "GroundedClaim"),
    ("app.contracts.analytics", "InternalProvenance"),
    ("app.contracts.analytics", "ResultMetadata"),
    ("app.data.gateway", "ColumnMetadata"),
    ("app.data.gateway", "DatabaseExecutionMetadata"),
    ("app.data.gateway", "DatabaseQueryResult"),
    ("app.data.gateway", "ForeignKeyMetadata"),
    ("app.data.gateway", "ResultColumnMetadata"),
    ("app.data.gateway", "TableMetadata"),
    ("app.governance.gateway", "GovernanceColumnMetadata"),
    ("app.governance.gateway", "GovernanceFreshness"),
    ("app.governance.gateway", "GovernanceLineage"),
    ("app.governance.gateway", "GovernanceOwner"),
    ("app.governance.gateway", "GovernanceSnapshot"),
    ("app.governance.gateway", "GovernanceTableMetadata"),
    ("app.governance.gateway", "GovernanceTag"),
    ("app.metrics.gateway", "MetricFilter"),
    ("app.metrics.gateway", "MetricFilterOperator"),
    ("app.metrics.gateway", "MetricOrder"),
    ("app.metrics.gateway", "MetricOrderDirection"),
    ("app.metrics.gateway", "MetricQuery"),
    ("app.metrics.gateway", "MetricResult"),
    ("app.metrics.gateway", "MetricResultProvenance"),
    ("app.metrics.gateway", "MetricTimeGrain"),
    ("app.routing.contracts", "QueryRoute"),
    ("app.routing.contracts", "RouteDecision"),
    ("app.routing.contracts", "RouteReasonCode"),
    ("app.security.sql_validation", "SQLValidationResult"),
    ("app.semantic.gateway", "SemanticDefinition"),
    ("app.semantic.gateway", "SemanticMeasure"),
}
