"""Asking the application a question the way a caller does.

An evaluation exists to say whether the product still answers correctly, so it
has to ask the product. Calling the SQL layer directly would skip authentication,
authorization, datasource selection, routing, guidance retrieval, SQL validation
and grounding -- every part where a regression actually lives -- and would report
a number about a system nobody uses.

So the request goes over the application's own ASGI interface, in process. The
same dependency graph resolves, the same middleware runs, and what comes back is
exactly what a client would receive.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from app.config import Settings

#: Generous: a real question against a real model and database, not a unit test.
REQUEST_TIMEOUT_SECONDS = 300.0


class InProcessAnalysisRunner:
    """Issues a real analytics request against this process's own app."""

    def __init__(
        self,
        *,
        settings: Settings,
        authorization: str | None = None,
        timeout_seconds: float = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self._settings = settings
        self._authorization = authorization
        self._timeout = timeout_seconds

    async def ask(
        self,
        *,
        question: str,
        data_source_id: UUID,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        from httpx import ASGITransport, AsyncClient

        from app.main import app

        headers = {"Content-Type": "application/json"}
        if self._authorization:
            headers["Authorization"] = self._authorization
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://evaluation",
            timeout=self._timeout,
        ) as client:
            response = await client.post(
                "/analytics/query",
                headers=headers,
                json={
                    "question": question,
                    "data_source_id": str(data_source_id),
                    # Route and metric assertions read the debug block. Whether
                    # it is populated still depends on deployment policy and the
                    # caller's authority; a case that asserts on route simply
                    # cannot be checked where debug provenance is off.
                    "include_debug": True,
                    # Injected through the application path rather than by
                    # moving the system clock, so a run stays reproducible
                    # without changing anything global.
                    **({"as_of": as_of.isoformat()} if as_of is not None else {}),
                },
            )
        body: dict[str, Any] = response.json()
        if response.status_code >= 400:
            error = body.get("error") or {}
            raise AnalysisRequestError(
                str(error.get("code") or f"http_{response.status_code}")
            )
        return body


class AnalysisRequestError(RuntimeError):
    """Raised when the application refused or failed the request.

    Carries the stable error code only. A benchmark records that a question
    could not be answered, not what the failure said about the data.
    """
