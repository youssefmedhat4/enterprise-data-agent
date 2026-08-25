from __future__ import annotations

import logging
from dataclasses import dataclass, field
from time import perf_counter

from app.observability.gateway import TraceAttribute, TraceService, TraceSpan

_LOGGER = logging.getLogger("enterprise_data_agent.telemetry")


@dataclass
class _NoopSpan(TraceSpan):
    def set_attribute(self, key: str, value: TraceAttribute) -> None:
        del key, value

    def record_error(self, error: BaseException) -> None:
        del error

    def end(self) -> None:
        return None


class NoopTraceService(TraceService):
    def start_span(
        self,
        name: str,
        attributes: dict[str, TraceAttribute] | None = None,
    ) -> TraceSpan:
        del name, attributes
        return _NoopSpan()


@dataclass
class _LoggingSpan(TraceSpan):
    name: str
    attributes: dict[str, TraceAttribute]
    started_at: float = field(default_factory=perf_counter)
    error_type: str | None = None
    ended: bool = False

    def set_attribute(self, key: str, value: TraceAttribute) -> None:
        self.attributes[key] = value

    def record_error(self, error: BaseException) -> None:
        self.error_type = type(error).__name__

    def end(self) -> None:
        if self.ended:
            return
        self.ended = True
        fields: dict[str, TraceAttribute] = {
            **self.attributes,
            "span": self.name,
            "latency_ms": round((perf_counter() - self.started_at) * 1000, 3),
            "status": "error" if self.error_type else "ok",
        }
        if self.error_type:
            fields["error_type"] = self.error_type
        _LOGGER.info("analytics_span", extra={"telemetry": fields})


class LoggingTraceService(TraceService):
    """Emit content-free operation telemetry through standard structured logging."""

    def start_span(
        self,
        name: str,
        attributes: dict[str, TraceAttribute] | None = None,
    ) -> TraceSpan:
        return _LoggingSpan(name=name, attributes=dict(attributes or {}))
