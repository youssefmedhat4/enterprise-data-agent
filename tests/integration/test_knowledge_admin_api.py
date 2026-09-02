"""Knowledge administration API and its authorization boundary.

Reading data is not authority over what the data is defined to mean, so every
route here needs an explicit capability that ordinary analytics access does not
grant.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.routes import get_authenticated_identity
from app.authentication.gateway import UserIdentity
from app.knowledge.seed import DEFAULT_DATA_SOURCE_ID
from app.main import create_app

ADMIN_ROUTES = [
    "/knowledge/data-sources",
    f"/knowledge/data-sources/{DEFAULT_DATA_SOURCE_ID}/semantics",
    f"/knowledge/data-sources/{DEFAULT_DATA_SOURCE_ID}/column-previews",
    f"/knowledge/data-sources/{DEFAULT_DATA_SOURCE_ID}/clusters",
    f"/knowledge/data-sources/{DEFAULT_DATA_SOURCE_ID}/candidates",
    f"/knowledge/data-sources/{DEFAULT_DATA_SOURCE_ID}/metrics",
    f"/knowledge/data-sources/{DEFAULT_DATA_SOURCE_ID}/examples",
]


def client_as(role: str) -> Iterator[TestClient]:
    """A client authenticated as one role.

    Local development authentication returns a fixed identity and ignores
    credentials, so the identity dependency is overridden rather than faked
    through a header that the gateway would not read.
    """
    app = create_app()
    app.dependency_overrides[get_authenticated_identity] = lambda: UserIdentity(
        subject_id=f"test-{role}", roles=(role,), provider="local"
    )
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def analyst() -> Iterator[TestClient]:
    yield from client_as("analyst")


@pytest.fixture
def reviewer() -> Iterator[TestClient]:
    yield from client_as("admin_analytics")


@pytest.mark.parametrize("path", ADMIN_ROUTES)
def test_an_ordinary_analyst_cannot_reach_knowledge_administration(
    analyst: TestClient, path: str
) -> None:
    response = analyst.get(path)

    assert response.status_code == 403
    body = response.json()
    # The refusal must not describe what exists behind it.
    assert "candidate" not in str(body).casefold()


def test_a_reviewer_can_list_data_sources(reviewer: TestClient) -> None:
    response = reviewer.get("/knowledge/data-sources")

    assert response.status_code == 200
    sources = response.json()
    assert sources and sources[0]["name"] == "Company Analytics"


def test_a_data_source_never_exposes_a_credential(reviewer: TestClient) -> None:
    response = reviewer.get("/knowledge/data-sources")

    payload = str(response.json()).casefold()
    for secret_marker in ("://", "password", "postgresql", "secret", "@localhost"):
        assert secret_marker not in payload, f"datasource leaked {secret_marker!r}"
    # The reference is a variable name, which is safe and is what admins need.
    assert response.json()[0]["connection_ref"] == "DATABASE_URL"


def test_certified_metrics_are_visible_to_a_reviewer(reviewer: TestClient) -> None:
    response = reviewer.get(
        f"/knowledge/data-sources/{DEFAULT_DATA_SOURCE_ID}/metrics"
    )

    assert response.status_code == 200
    keys = {metric["metric_key"] for metric in response.json()}
    assert "annual_base_payroll" in keys


def test_query_examples_do_not_return_sql_by_default(reviewer: TestClient) -> None:
    response = reviewer.get(
        f"/knowledge/data-sources/{DEFAULT_DATA_SOURCE_ID}/examples"
    )

    assert response.status_code == 200
    for example in response.json():
        assert example["query_pattern"] is None


def test_reviewing_a_missing_candidate_does_not_confirm_what_exists(
    reviewer: TestClient,
) -> None:
    response = reviewer.post(
        f"/knowledge/data-sources/{DEFAULT_DATA_SOURCE_ID}"
        "/candidates/00000000-0000-0000-0000-0000000000ff/review",
        json={"action": "reject", "reason": "no"},
    )

    assert response.status_code in {404, 422}


MUTATING_ROUTES = [
    ("post", "/knowledge/data-sources", {"name": "X", "connection_ref": "DATABASE_URL"}),
    (
        "post",
        f"/knowledge/data-sources/{DEFAULT_DATA_SOURCE_ID}/scan",
        None,
    ),
    (
        "post",
        f"/knowledge/data-sources/{DEFAULT_DATA_SOURCE_ID}"
        "/semantics/00000000-0000-0000-0000-0000000000aa/review",
        {"action": "approve"},
    ),
    (
        "post",
        f"/knowledge/data-sources/{DEFAULT_DATA_SOURCE_ID}"
        "/candidates/00000000-0000-0000-0000-0000000000bb/review",
        {"action": "approve"},
    ),
]


@pytest.mark.parametrize(("method", "path", "body"), MUTATING_ROUTES)
def test_an_analyst_cannot_perform_knowledge_mutations(
    analyst: TestClient, method: str, path: str, body: dict[str, str] | None
) -> None:
    """Registering, scanning, and approving all need review authority."""
    response = getattr(analyst, method)(path, json=body)

    assert response.status_code == 403


def test_an_analyst_cannot_list_connection_references(analyst: TestClient) -> None:
    """Reference names describe server configuration; analysts do not see them."""
    assert analyst.get("/knowledge/connection-refs").status_code == 403


def test_a_reviewer_sees_only_configured_connection_references(
    reviewer: TestClient,
) -> None:
    response = reviewer.get("/knowledge/connection-refs")

    assert response.status_code == 200
    refs = response.json()
    assert refs == ["DATABASE_URL"]
    # Names only. A value would be the credential itself.
    assert all("://" not in ref for ref in refs)


def test_registering_with_a_pasted_dsn_is_refused(reviewer: TestClient) -> None:
    response = reviewer.post(
        "/knowledge/data-sources",
        json={
            "name": "Sneaky",
            "connection_ref": "postgresql://user:secret@localhost/db",
        },
    )

    assert response.status_code in {422, 503}
    assert "secret" not in response.text
