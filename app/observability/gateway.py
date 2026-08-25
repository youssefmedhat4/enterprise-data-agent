from __future__ import annotations

from typing import Protocol

type TraceAttribute = str | int | float | bool | None


class TraceSpan(Protocol):
    def set_attribute(self, key: str, value: TraceAttribute) -> None:
        """Attach non-sensitive request metadata to this span."""

    def record_error(self, error: BaseException) -> None:
        """Record only the exception class, never its potentially sensitive message."""

    def end(self) -> None:
        """Finish the span."""


class TraceService(Protocol):
    def start_span(
        self,
        name: str,
        attributes: dict[str, TraceAttribute] | None = None,
    ) -> TraceSpan:
        """Start one vendor-neutral operation span."""
