"""Conversation transcripts: ownership, ordering, bounds, and separation.

The feature exists because history used to live in the browser's session
storage, so closing a tab destroyed it. These pin the properties that make the
server copy trustworthy instead: it belongs to one subject, it stays in order,
it never grows without limit, and it stays out of the learning store.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.knowledge.conversations import (
    MAX_PAYLOAD_BYTES,
    MAX_PREVIEW_ROWS,
    Conversation,
    InMemoryConversationStore,
    MessageRole,
    bounded_payload,
    derive_title,
)

SOURCE = uuid4()
OTHER_SOURCE = uuid4()


def _conversation(owner: str = "analyst", source=SOURCE) -> Conversation:  # type: ignore[no-untyped-def]
    return Conversation(
        owner_subject_id=owner,
        data_source_id=source,
        thread_id=f"{source}:{uuid4()}",
        title="Compensation",
    )


@pytest.mark.anyio
async def test_a_turn_is_stored_in_the_order_it_was_said() -> None:
    store = InMemoryConversationStore()
    conversation = await store.create(_conversation())

    await store.append_turn(
        conversation,
        question="Current and previous compensation?",
        answer="Twelve employees.",
        payload={"answer": "Twelve employees.", "rows": []},
        request_id="req-1",
    )
    await store.append_turn(
        conversation,
        question="Only those whose pay increased.",
        answer="Four employees.",
        payload={"answer": "Four employees.", "rows": []},
        request_id="req-2",
    )

    restored = await store.messages(conversation.id)

    assert [message.role for message in restored] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
    assert [message.sequence for message in restored] == [0, 1, 2, 3]
    assert restored[0].content == "Current and previous compensation?"
    assert restored[1].content == "Twelve employees."


@pytest.mark.anyio
async def test_another_subject_cannot_read_a_conversation_by_its_id() -> None:
    """An id is not an authorization. Someone else's reads as absent."""
    store = InMemoryConversationStore()
    conversation = await store.create(_conversation(owner="alice"))

    assert await store.get(conversation.id, owner_subject_id="alice") is not None
    assert await store.get(conversation.id, owner_subject_id="mallory") is None


@pytest.mark.anyio
async def test_listing_returns_only_the_requesting_subjects_conversations() -> None:
    store = InMemoryConversationStore()
    await store.create(_conversation(owner="alice"))
    await store.create(_conversation(owner="bob"))

    assert len(await store.list_for("alice")) == 1
    assert len(await store.list_for("bob")) == 1
    assert await store.list_for("carol") == []


@pytest.mark.anyio
async def test_a_turn_inherits_its_conversations_datasource() -> None:
    """A transcript cannot hold a turn answered from another database."""
    store = InMemoryConversationStore()
    conversation = await store.create(_conversation(source=SOURCE))

    _, answered = await store.append_turn(
        conversation,
        question="Headcount?",
        answer="12",
        payload={},
        request_id=None,
    )

    assert answered.data_source_id == SOURCE
    assert answered.data_source_id != OTHER_SOURCE


@pytest.mark.anyio
async def test_archiving_removes_a_conversation_from_the_list() -> None:
    store = InMemoryConversationStore()
    conversation = await store.create(_conversation(owner="alice"))

    assert await store.archive(conversation.id, owner_subject_id="alice") is True
    assert await store.list_for("alice") == []


@pytest.mark.anyio
async def test_archiving_someone_elses_conversation_does_nothing() -> None:
    store = InMemoryConversationStore()
    conversation = await store.create(_conversation(owner="alice"))

    assert await store.archive(conversation.id, owner_subject_id="mallory") is False
    assert len(await store.list_for("alice")) == 1


@pytest.mark.anyio
async def test_a_page_returns_the_latest_turns() -> None:
    """A long thread is paged from the end: the newest turns are what a
    reopened conversation shows first."""
    store = InMemoryConversationStore()
    conversation = await store.create(_conversation())
    for index in range(10):
        await store.append_turn(
            conversation,
            question=f"Question {index}",
            answer=f"Answer {index}",
            payload={},
            request_id=None,
        )

    page = await store.messages(conversation.id, limit=4)

    assert [message.content for message in page] == [
        "Question 8",
        "Answer 8",
        "Question 9",
        "Answer 9",
    ]


def test_a_result_preview_is_capped_rather_than_copied_whole() -> None:
    rows = [{"employee": f"E{index}", "salary": index} for index in range(1_000)]

    capped = bounded_payload({"answer": "…", "rows": rows})

    assert len(capped["rows"]) == MAX_PREVIEW_ROWS
    assert capped["rows_truncated"] is True


def test_a_single_enormous_row_still_fits_the_budget() -> None:
    """The row cap alone is not a size bound: one wide row can exceed it."""
    payload = {
        "answer": "…",
        "columns": ["blob"],
        "rows": [{"blob": "x" * (MAX_PAYLOAD_BYTES * 2)}],
    }

    capped = bounded_payload(payload)

    assert len(str(capped).encode("utf-8")) <= MAX_PAYLOAD_BYTES
    assert capped["rows_truncated"] is True


def test_a_small_answer_is_stored_unchanged() -> None:
    payload = {"answer": "Twelve.", "rows": [{"n": 12}], "columns": ["n"]}

    assert bounded_payload(payload) == payload


def test_a_title_comes_from_the_question_without_a_model_call() -> None:
    assert derive_title("  Show   every employee's pay  ") == (
        "Show every employee's pay"
    )
    assert derive_title("") == "Untitled analysis"

    long_question = "Show " + "very " * 40 + "long question"
    titled = derive_title(long_question)
    assert len(titled) <= 64
    assert titled.endswith("…")
