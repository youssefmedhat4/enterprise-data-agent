"""Who may review, and what a review action may do.

Analytics access and review authority are deliberately separate: being allowed
to read the data says nothing about being allowed to decide what it means. These
check that the separation holds at the boundary rather than only in the UI,
which is where it would be trivial to bypass.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.routes import (
    get_authorization_gateway,
    get_database_gateway,
    get_knowledge_runtime,
)
from app.authorization.gateway import (
    AuthorizationDecision,
    AuthorizationGateway,
    AuthorizationRequest,
)
from app.config import Settings
from app.data.fake import FakeDatabaseGateway
from app.knowledge.runtime import _in_memory_runtime
from app.main import app

SOURCE = uuid4()


class _Reviewer(AuthorizationGateway):
    """An identity granted review authority, like the development admin role."""

    review_allowed = True

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        return AuthorizationDecision(
            allowed=True,
            provider="test",
            table_columns={table.identifier: table.columns for table in request.tables},
            allowed_schemas=tuple(
                sorted({table.schema_name for table in request.tables})
            ),
            allowed_metrics=request.metrics,
            knowledge_review_allowed=self.review_allowed,
        )

    async def close(self) -> None:
        return None


class _Analyst(_Reviewer):
    """Full analytics access, no authority over what anything means."""

    review_allowed = False


@pytest.fixture
async def runtime():  # type: ignore[no-untyped-def]
    built = await _in_memory_runtime(Settings(), SOURCE)
    try:
        yield built
    finally:
        await built.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path",
    [
        "candidates",
        "semantics",
        "clusters",
        "metrics",
        "examples",
        "evaluation-cases",
        "quality",
        "time-policy",
        "temporal-dimensions",
    ],
)
async def test_an_analyst_cannot_reach_any_review_surface(runtime, path: str) -> None:  # type: ignore[no-untyped-def]
    """Reading the data is not authority over what it means."""
    app.dependency_overrides[get_knowledge_runtime] = lambda: runtime
    app.dependency_overrides[get_database_gateway] = lambda: FakeDatabaseGateway()
    app.dependency_overrides[get_authorization_gateway] = lambda: _Analyst()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/knowledge/data-sources/{SOURCE}/{path}"
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403, f"{path} was readable without review authority"
    # The same message whichever way it failed, so probing cannot map what
    # exists behind the boundary.
    assert "review authority" in response.text


@pytest.mark.anyio
async def test_a_reviewer_reaches_the_same_surfaces(runtime) -> None:  # type: ignore[no-untyped-def]
    app.dependency_overrides[get_knowledge_runtime] = lambda: runtime
    app.dependency_overrides[get_database_gateway] = lambda: FakeDatabaseGateway()
    app.dependency_overrides[get_authorization_gateway] = lambda: _Reviewer()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/knowledge/data-sources/{SOURCE}/candidates"
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200


@pytest.mark.anyio
async def test_an_analyst_cannot_review_a_candidate(runtime) -> None:  # type: ignore[no-untyped-def]
    """The write path is gated by the same authority as the read path."""
    app.dependency_overrides[get_knowledge_runtime] = lambda: runtime
    app.dependency_overrides[get_database_gateway] = lambda: FakeDatabaseGateway()
    app.dependency_overrides[get_authorization_gateway] = lambda: _Analyst()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/knowledge/data-sources/{SOURCE}/candidates/{uuid4()}/review",
                json={"action": "approve"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403


@pytest.mark.anyio
async def test_editing_a_candidate_is_refused_rather_than_approving_it(runtime) -> None:  # type: ignore[no-untyped-def]
    """`edit` parses on this route because the payload is shared with semantic
    review, where editing means approving under a corrected name. A candidate
    has no such path, and the action used to fall through to the approve
    branch -- an unsupported action silently becoming an approval.
    """
    app.dependency_overrides[get_knowledge_runtime] = lambda: runtime
    app.dependency_overrides[get_database_gateway] = lambda: FakeDatabaseGateway()
    app.dependency_overrides[get_authorization_gateway] = lambda: _Reviewer()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/knowledge/data-sources/{SOURCE}/candidates/{uuid4()}/review",
                json={"action": "edit", "concept_name": "Something else"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422, "an unsupported action was treated as approval"
    assert "cannot be edited" in response.text
