"""What time it is, asked once.

`datetime.now()` scattered through a codebase makes every time-dependent
behaviour untestable: "year to date" cannot be asserted against a moving target,
so those assertions get written loosely or not at all, and the boundary bugs
that matter most go unnoticed.

One clock, injected. Production reads the real UTC time; a test pins it. Nothing
below this module calls `now()` on its own.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """The current instant, always timezone-aware and always UTC."""

    def now(self) -> datetime: ...


class SystemClock(Clock):
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock(Clock):
    """A clock pinned to one instant, for tests and evaluation anchors.

    An evaluation case asking "revenue year to date" means nothing unless the
    run says which date it is to. Pinning that here rather than mocking the
    system clock keeps the anchor an explicit, recorded input.
    """

    def __init__(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            raise ValueError("A fixed clock needs a timezone-aware instant.")
        self._instant = instant.astimezone(UTC)

    def now(self) -> datetime:
        return self._instant
