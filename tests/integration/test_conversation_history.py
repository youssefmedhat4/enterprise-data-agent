"""Asking a question records a transcript, and reopening it restores one.

The bug this covers is a product one: the analytical context survived on the
server while the visible conversation did not, so reopening a thread showed an
empty page above a composer that still worked. These drive the real endpoints
end to end -- ask, then read the conversation back -- rather than the store in
isolation, because the interesting failure was in the wiring.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import InMemorySaver

from app.api.routes import (
    get_authenticated_identity,
    get_conversation_checkpointer,
    get_database_gateway,
    get_llm_gateway,
)
from app.authentication.gateway import UserIdentity
from app.data.fake import FakeDatabaseGateway
from app.knowledge.seed import DEFAULT_DATA_SOURCE_ID
from app.llm.fake import FakeLLMGateway
from app.main import app

# The questions the fake gateway answers, so these exercise the real route
# without a provider call.
QUESTION = "Which department has the highest payroll?"
FOLLOW_UP = "What about last year?"


def _as(subject: str) -> UserIdentity:
    # The development role, so authorization behaves exactly as it does for the
    # running app. Only the subject differs, which is what ownership turns on.
    return UserIdentity(
        subject_id=subject, roles=("admin_analytics",), provider="local"
    )


def _subject() -> str:
    """A fresh identity per test, so one run's transcripts are its own."""
    return f"test-{uuid4()}"


def _install(subject: str) -> None:
    """Wire the fakes, leaving the knowledge layer as configured.

    The knowledge runtime is deliberately not overridden: the conversation
    store under test is part of it, and an in-memory stand-in would prove
    nothing about the storage a deployment actually runs.
    """
    # Analytical state stays in memory here. The transcript under test is a
    # separate store, which is the distinction these tests exist to hold.
    checkpointer = InMemorySaver()
    app.dependency_overrides[get_conversation_checkpointer] = lambda: checkpointer
    # Lambdas, not the classes: FastAPI would read a class's __init__ signature
    # as request parameters and reject every call as invalid.
    app.dependency_overrides[get_database_gateway] = lambda: FakeDatabaseGateway()
    app.dependency_overrides[get_llm_gateway] = lambda: FakeLLMGateway()
    app.dependency_overrides[get_authenticated_identity] = lambda: _as(subject)


async def _ask(client: AsyncClient, question: str, thread_id: str | None = None):  # type: ignore[no-untyped-def]
    response = await client.post(
        "/analytics/query",
        json={"question": question, "thread_id": thread_id},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_asking_a_question_records_the_turn() -> None:
    _install(_subject())
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            answered = await _ask(client, QUESTION)
            listed = await client.get("/conversations")
    finally:
        app.dependency_overrides.clear()

    assert listed.status_code == 200
    conversations = listed.json()
    assert len(conversations) == 1
    assert conversations[0]["thread_id"] == answered["thread_id"]
    # The title comes from the question, deterministically. No model call.
    assert conversations[0]["title"] == QUESTION
    assert conversations[0]["message_count"] == 2
    # The answer was saved, so nothing warns that it was not.
    assert not any("conversation history" in w for w in answered["warnings"])


@pytest.mark.asyncio
async def test_reopening_restores_the_questions_and_the_answers() -> None:
    """The transcript comes back in order, with the answers attached.

    This is the whole feature: before it, both turns existed only in the tab
    that asked them.
    """
    _install(_subject())
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            first = await _ask(client, QUESTION)
            await _ask(client, FOLLOW_UP, thread_id=first["thread_id"])
            listed = (await client.get("/conversations")).json()
            restored = await client.get(f"/conversations/{listed[0]['id']}")
    finally:
        app.dependency_overrides.clear()

    assert restored.status_code == 200
    messages = restored.json()["messages"]
    assert [message["role"] for message in messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert messages[0]["content"] == QUESTION
    assert messages[2]["content"] == FOLLOW_UP
    # The assistant turn carries what the page is redrawn from.
    payload = messages[1]["payload"]
    assert payload["answer"] == messages[1]["content"]
    assert payload["rows"]
    assert payload["columns"]
    assert payload["provenance"]["result"]["row_count"] == len(payload["rows"])


@pytest.mark.asyncio
async def test_a_restored_answer_keeps_its_provenance_but_not_its_sql() -> None:
    """Why This Answer survives; the debug SQL does not.

    A live response may include the generated statement when the authorization
    decision allowed debug detail for that request. Replaying it later would
    show what a fresh answer might now withhold, so it is dropped on the way
    into storage rather than filtered on the way out.
    """
    _install(_subject())
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.post(
                "/analytics/query",
                json={"question": QUESTION, "include_debug": True},
            )
            listed = (await client.get("/conversations")).json()
            restored = (
                await client.get(f"/conversations/{listed[0]['id']}")
            ).json()
    finally:
        app.dependency_overrides.clear()

    payload = restored["messages"][1]["payload"]
    assert payload["trace"] is not None
    assert payload["trace"]["route"]
    assert payload["trace"]["generated_sql"] is None
    assert payload["provenance"]["debug"] is None


@pytest.mark.asyncio
async def test_a_new_thread_becomes_a_separate_conversation() -> None:
    """New chat means new thread: the second question must not join the first."""
    _install(_subject())
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            first = await _ask(client, QUESTION)
            second = await _ask(client, FOLLOW_UP)
            listed = (await client.get("/conversations")).json()
    finally:
        app.dependency_overrides.clear()

    assert first["thread_id"] != second["thread_id"]
    assert len(listed) == 2
    assert {c["thread_id"] for c in listed} == {
        first["thread_id"],
        second["thread_id"],
    }


@pytest.mark.asyncio
async def test_another_subject_cannot_read_the_conversation() -> None:
    """Guessing an id gets the same answer as a conversation that never was."""
    subject = _subject()
    _install(subject)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            await _ask(client, QUESTION)
            listed = (await client.get("/conversations")).json()
            conversation_id = listed[0]["id"]

        app.dependency_overrides[get_authenticated_identity] = lambda: _as("mallory")
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            stolen = await client.get(f"/conversations/{conversation_id}")
            theirs = await client.get("/conversations")
            archived = await client.delete(f"/conversations/{conversation_id}")
    finally:
        app.dependency_overrides.clear()

    assert stolen.status_code == 404
    assert theirs.json() == []
    assert archived.status_code == 404


@pytest.mark.asyncio
async def test_an_unknown_conversation_is_not_distinguishable() -> None:
    _install(_subject())
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            missing = await client.get(f"/conversations/{uuid4()}")
    finally:
        app.dependency_overrides.clear()

    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_archiving_hides_a_conversation_from_its_owner() -> None:
    _install(_subject())
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            await _ask(client, QUESTION)
            listed = (await client.get("/conversations")).json()
            removed = await client.delete(f"/conversations/{listed[0]['id']}")
            remaining = (await client.get("/conversations")).json()
    finally:
        app.dependency_overrides.clear()

    assert removed.status_code == 204
    assert remaining == []


@pytest.mark.asyncio
async def test_renaming_changes_only_the_title() -> None:
    _install(_subject())
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            await _ask(client, QUESTION)
            listed = (await client.get("/conversations")).json()
            renamed = await client.patch(
                f"/conversations/{listed[0]['id']}",
                json={"title": "Payroll by department"},
            )
            restored = (
                await client.get(f"/conversations/{listed[0]['id']}")
            ).json()
    finally:
        app.dependency_overrides.clear()

    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Payroll by department"
    assert len(restored["messages"]) == 2


@pytest.mark.asyncio
async def test_question_memory_does_not_become_a_transcript() -> None:
    """The learning store stays a learning store.

    Recording what a person asked and what they were told is a product
    concern with a product lifetime. Question memory exists to notice that a
    *shape* of question recurs, and must not start carrying answers, SQL or
    rows just because a transcript now exists next to it.
    """
    _install(_subject())
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            answered = await _ask(client, QUESTION)
    finally:
        app.dependency_overrides.clear()

    runtime = app.state.knowledge
    clusters = await runtime.memory.clusters(DEFAULT_DATA_SOURCE_ID)
    remembered = repr([cluster.__dict__ for cluster in clusters])
    assert answered["answer"] not in remembered
    for row in answered["rows"]:
        for value in row.values():
            assert f"'{value}'" not in remembered
    assert "SELECT" not in remembered.upper()
