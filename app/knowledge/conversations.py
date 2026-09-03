"""Conversation transcripts: what was actually said, and by whom.

This is a product concern, kept deliberately apart from the two systems it is
easy to confuse it with.

`QuestionMemory` records that a *shape* of question recurs -- a fingerprint, a
route, whether the run succeeded. It holds no answer text, no SQL and no rows,
because remembering what people ask must never become a record of what their
data contains. Nothing here writes to it.

The LangGraph checkpointer holds analytical state so a follow-up can resolve
"those employees" against the previous turn. That is reasoning state, not a
transcript: it is not readable as a conversation and was never meant to be.

So a transcript lives here. A conversation names the checkpoint thread it
continues, which is what lets reopening one restore both the history a person
can read and the context that answers their next question.

What an assistant turn stores is bounded on purpose. A full result set belongs
to the run that produced it; this keeps a capped preview so the page can be
redrawn, and says so when it had to truncate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID, uuid4

#: Rows kept for redrawing a past answer. Enough to recognise the table that
#: was shown, far short of retaining the query's output as a second copy.
MAX_PREVIEW_ROWS = 200

#: Hard ceiling on one stored turn, after row capping. A single wide row can
#: still be large, so the row cap alone is not a size bound.
MAX_PAYLOAD_BYTES = 256_000

#: How much of a question becomes the conversation's name.
MAX_TITLE_LENGTH = 64


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class ConversationError(RuntimeError):
    """Raised when a transcript cannot be read or written safely."""


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    """One turn, as it was shown."""

    conversation_id: UUID
    data_source_id: UUID
    role: MessageRole
    sequence: int
    content: str
    #: Assistant turns only: the bounded view the UI redraws from.
    payload: dict[str, Any] = field(default_factory=dict)
    request_id: str | None = None
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class Conversation:
    """One thread of questions against one database."""

    owner_subject_id: str
    data_source_id: UUID
    thread_id: str
    title: str
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    archived_at: datetime | None = None
    message_count: int = 0


class ConversationStore(Protocol):
    """Storage the API and the analytics route need."""

    async def create(self, conversation: Conversation) -> Conversation: ...

    async def get(
        self, conversation_id: UUID, *, owner_subject_id: str
    ) -> Conversation | None: ...

    async def by_thread(self, thread_id: str) -> Conversation | None: ...

    async def list_for(
        self, owner_subject_id: str, *, limit: int = 50
    ) -> list[Conversation]: ...

    async def messages(
        self, conversation_id: UUID, *, limit: int = 100, before: int | None = None
    ) -> list[ConversationMessage]: ...

    async def append_turn(
        self,
        conversation: Conversation,
        *,
        question: str,
        answer: str,
        payload: dict[str, Any],
        request_id: str | None,
    ) -> tuple[ConversationMessage, ConversationMessage]: ...

    async def rename(
        self, conversation_id: UUID, *, owner_subject_id: str, title: str
    ) -> Conversation | None: ...

    async def archive(
        self, conversation_id: UUID, *, owner_subject_id: str
    ) -> bool: ...


def derive_title(question: str) -> str:
    """Name a conversation from its first question.

    Deterministic and free. A model call to phrase this better would cost a
    provider request per conversation to improve a sidebar label, which is not
    a trade worth making.
    """
    collapsed = " ".join(question.split()).strip()
    if collapsed == "":
        return "Untitled analysis"
    if len(collapsed) <= MAX_TITLE_LENGTH:
        return collapsed
    return collapsed[: MAX_TITLE_LENGTH - 1].rstrip() + "…"


def bounded_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Cap what one assistant turn stores.

    Rows go first, because they are the bulk and the least necessary: a preview
    is enough to redraw the table, and the run that produced it remains the
    authority on the full result. If the turn is still too large after that,
    the rows are dropped entirely rather than storing a transcript that could
    grow without limit.
    """
    rows = payload.get("rows")
    truncated = False
    if isinstance(rows, list) and len(rows) > MAX_PREVIEW_ROWS:
        payload = {**payload, "rows": rows[:MAX_PREVIEW_ROWS]}
        truncated = True

    if truncated:
        payload = {**payload, "rows_truncated": True}

    if _size(payload) <= MAX_PAYLOAD_BYTES:
        return payload

    shrunk = dict(payload)
    kept = shrunk.get("rows")
    if isinstance(kept, list):
        # Halve until it fits, then give up on rows rather than on the answer.
        while len(kept) > 1 and _size(shrunk) > MAX_PAYLOAD_BYTES:
            kept = kept[: len(kept) // 2]
            shrunk = {**shrunk, "rows": kept, "rows_truncated": True}
    if _size(shrunk) > MAX_PAYLOAD_BYTES:
        shrunk = {**shrunk, "rows": [], "rows_truncated": True}
    if _size(shrunk) > MAX_PAYLOAD_BYTES:
        # Whatever remains is not a result preview -- keep only what the page
        # cannot be drawn without.
        return {
            "columns": shrunk.get("columns", []),
            "rows": [],
            "rows_truncated": True,
        }
    return shrunk


def _size(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, default=str).encode("utf-8"))


class InMemoryConversationStore(ConversationStore):
    """Development storage, with the same ownership and ordering rules.

    Present so the app runs without the internal database configured. It is not
    a production fallback: `knowledge_storage=postgres` builds the persistent
    store, and a transcript that vanishes on restart would not be one.
    """

    def __init__(self) -> None:
        self._conversations: dict[UUID, Conversation] = {}
        self._messages: dict[UUID, list[ConversationMessage]] = {}

    async def create(self, conversation: Conversation) -> Conversation:
        self._conversations[conversation.id] = conversation
        self._messages.setdefault(conversation.id, [])
        return conversation

    async def get(
        self, conversation_id: UUID, *, owner_subject_id: str
    ) -> Conversation | None:
        found = self._conversations.get(conversation_id)
        if found is None or found.owner_subject_id != owner_subject_id:
            return None
        return replace(found, message_count=len(self._messages.get(found.id, [])))

    async def by_thread(self, thread_id: str) -> Conversation | None:
        return next(
            (
                conversation
                for conversation in self._conversations.values()
                if conversation.thread_id == thread_id
            ),
            None,
        )

    async def list_for(
        self, owner_subject_id: str, *, limit: int = 50
    ) -> list[Conversation]:
        owned = [
            replace(
                conversation,
                message_count=len(self._messages.get(conversation.id, [])),
            )
            for conversation in self._conversations.values()
            if conversation.owner_subject_id == owner_subject_id
            and conversation.archived_at is None
        ]
        owned.sort(key=lambda conversation: conversation.updated_at, reverse=True)
        return owned[:limit]

    async def messages(
        self, conversation_id: UUID, *, limit: int = 100, before: int | None = None
    ) -> list[ConversationMessage]:
        stored = self._messages.get(conversation_id, [])
        if before is not None:
            stored = [message for message in stored if message.sequence < before]
        return stored[-limit:]

    async def append_turn(
        self,
        conversation: Conversation,
        *,
        question: str,
        answer: str,
        payload: dict[str, Any],
        request_id: str | None,
    ) -> tuple[ConversationMessage, ConversationMessage]:
        stored = self._messages.setdefault(conversation.id, [])
        start = stored[-1].sequence + 1 if stored else 0
        asked = ConversationMessage(
            conversation_id=conversation.id,
            data_source_id=conversation.data_source_id,
            role=MessageRole.USER,
            sequence=start,
            content=question,
            request_id=request_id,
        )
        answered = ConversationMessage(
            conversation_id=conversation.id,
            data_source_id=conversation.data_source_id,
            role=MessageRole.ASSISTANT,
            sequence=start + 1,
            content=answer,
            payload=bounded_payload(payload),
            request_id=request_id,
        )
        stored.extend((asked, answered))
        self._conversations[conversation.id] = replace(
            conversation, updated_at=datetime.now(UTC)
        )
        return asked, answered

    async def rename(
        self, conversation_id: UUID, *, owner_subject_id: str, title: str
    ) -> Conversation | None:
        found = await self.get(conversation_id, owner_subject_id=owner_subject_id)
        if found is None:
            return None
        renamed = replace(found, title=title, updated_at=datetime.now(UTC))
        self._conversations[conversation_id] = renamed
        return renamed

    async def archive(
        self, conversation_id: UUID, *, owner_subject_id: str
    ) -> bool:
        found = await self.get(conversation_id, owner_subject_id=owner_subject_id)
        if found is None:
            return False
        self._conversations[conversation_id] = replace(
            found, archived_at=datetime.now(UTC)
        )
        return True


__all__ = [
    "MAX_PAYLOAD_BYTES",
    "MAX_PREVIEW_ROWS",
    "Conversation",
    "ConversationError",
    "ConversationMessage",
    "ConversationStore",
    "InMemoryConversationStore",
    "MessageRole",
    "bounded_payload",
    "derive_title",
]
