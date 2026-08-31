"""Per-datasource execution resources.

A datasource defines two things, and until now only one of them was honoured.
It names a *knowledge namespace* — semantics, metrics, memory — and it names a
*physical execution target*. Selecting a datasource changed the first and left
the second pointing at whatever database the process was started with, so a
question asked of one database was answered from another.

This resolves the second half. `data_source_id` reaches the registry, the
registry yields a `connection_ref`, the server-side resolver turns that
reference into a DSN inside the process, and the result is a gateway bound to
that database with that datasource's schema scope.

Two boundaries matter here.

The client never supplies connection information. A request carries a
datasource id and nothing else; host, user, DSN and even the reference *name*
are looked up server-side, so a caller cannot point execution anywhere by
crafting a request.

Gateways are pooled per datasource and closed together at shutdown. The cache
is bounded, because datasources are registered by administrators but a
misconfiguration should still not be able to exhaust a process with connection
pools.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.config import Settings
from app.data.gateway import DatabaseGateway
from app.knowledge.contracts import DataSource
from app.knowledge.datasources import DataSourceConnectionResolver, DataSourceError

logger = logging.getLogger(__name__)

#: How many datasource gateways to keep open. Far above any realistic
#: deployment, low enough that a runaway registration cannot open pools without
#: bound. The least recently used is closed when the limit is passed.
MAX_CACHED_GATEWAYS = 16


class DataSourceLookup(Protocol):
    """The registry capability this provider needs: find one datasource."""

    async def get(self, data_source_id: UUID) -> DataSource | None: ...


class DataSourceUnavailableError(RuntimeError):
    """Raised when the selected datasource cannot be reached.

    Deliberately distinct from a generic database error: the caller asked for a
    specific database, and the honest answer is that it is unavailable — never
    a result from a different one.
    """


@dataclass(frozen=True, slots=True)
class DataSourceExecutionContext:
    """Everything a request needs to read one datasource."""

    data_source: DataSource
    gateway: DatabaseGateway

    @property
    def data_source_id(self) -> UUID:
        return self.data_source.id

    @property
    def allowed_schemas(self) -> tuple[str, ...]:
        """Schema scope for this datasource, not for the process."""
        return self.data_source.allowed_schemas


class DataSourceRuntimeProvider:
    """Builds and caches an execution context per datasource."""

    def __init__(
        self,
        settings: Settings,
        *,
        registry: DataSourceLookup,
        max_cached: int = MAX_CACHED_GATEWAYS,
        gateway_factory: Callable[..., DatabaseGateway] | None = None,
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._resolver = DataSourceConnectionResolver(settings)
        self._gateways: OrderedDict[str, DatabaseGateway] = OrderedDict()
        self._lock = asyncio.Lock()
        self._max_cached = max_cached
        self._gateway_factory = gateway_factory

    async def context_for(
        self,
        data_source_id: UUID,
        *,
        sample_columns: tuple[str, ...] = (),
    ) -> DataSourceExecutionContext:
        """The execution context for one datasource.

        `sample_columns` comes from the datasource's confirmed semantic model
        when the caller has one; it decides which columns the scanner may
        sample values from, and is part of the cache key because two callers
        wanting different sampling must not share a gateway.
        """
        source = await self._require(data_source_id)
        key = f"{source.id}|{'|'.join(sorted(sample_columns))}"

        async with self._lock:
            cached = self._gateways.get(key)
            if cached is not None:
                self._gateways.move_to_end(key)
                return DataSourceExecutionContext(
                    data_source=source, gateway=cached
                )
            gateway = self._build(source, sample_columns)
            self._gateways[key] = gateway
            await self._evict_if_needed()

        return DataSourceExecutionContext(data_source=source, gateway=gateway)

    def _build(
        self, source: DataSource, sample_columns: tuple[str, ...]
    ) -> DatabaseGateway:
        from app.data.factory import build_database_gateway_for

        try:
            dsn = self._resolver.resolve(source.connection_ref)
        except DataSourceError as exc:
            # Names the reference, never the value it resolves to.
            raise DataSourceUnavailableError(str(exc)) from exc
        try:
            factory = self._gateway_factory or build_database_gateway_for
            return factory(
                self._settings,
                database_url=dsn,
                allowed_schemas=source.allowed_schemas,
                sample_columns=sample_columns,
                database_type=source.database_type,
            )
        except Exception as exc:
            # Type only: a DSN can appear in a driver or validation message.
            raise DataSourceUnavailableError(
                f"The datasource connection could not be prepared "
                f"({type(exc).__name__})."
            ) from exc

    async def _require(self, data_source_id: UUID) -> DataSource:
        source = await self._registry.get(data_source_id)
        if source is None:
            raise DataSourceUnavailableError("No such datasource.")
        return source

    async def _evict_if_needed(self) -> None:
        while len(self._gateways) > self._max_cached:
            _, evicted = self._gateways.popitem(last=False)
            await _close_quietly(evicted)

    async def close(self) -> None:
        async with self._lock:
            gateways = list(self._gateways.values())
            self._gateways.clear()
        for gateway in gateways:
            await _close_quietly(gateway)


async def _close_quietly(gateway: DatabaseGateway) -> None:
    close = getattr(gateway, "close", None)
    if close is None:
        return
    try:
        await close()
    except Exception as exc:  # pragma: no cover - shutdown best effort
        logger.warning("datasource gateway close failed: %s", type(exc).__name__)
