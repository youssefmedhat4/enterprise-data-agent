"""PostgreSQL-backed conversation transcripts.

A transcript has to outlive the tab it was typed in, the browser session, and
the process that answered. That is the whole point of the table: the previous
implementation kept history in `sessionStorage`, so closing the tab destroyed it
while the server still held enough context to answer a follow-up -- an empty
page above a composer that worked.

Ownership is enforced in the `WHERE` clause of every read, not by the caller.
A conversation the requesting subject does not own is indistinguishable from
one that does not exist, so an id cannot be probed for.

Both messages of a turn are written in one transaction with the conversation's
`updated_at`. A visible answer with no recorded question, or a question whose
answer was lost, would both be worse than reporting that the turn was not saved.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Json
from psycopg_pool import AsyncConnectionPool

from app.knowledge.conversations import (
    Conversation,
    ConversationMessage,
    ConversationStore,
    MessageRole,
    bounded_payload,
)

_INSERT_CONVERSATION = """
    INSERT INTO knowledge.conversations
        (id, owner_subject_id, data_source_id, thread_id, title,
         created_at, updated_at)
    VALUES
        (%(id)s, %(owner)s, %(data_source_id)s, %(thread_id)s, %(title)s,
         %(created_at)s, %(updated_at)s)
    ON CONFLICT (thread_id) DO NOTHING
"""

_COLUMNS = """
    c.id, c.owner_subject_id, c.data_source_id, c.thread_id, c.title,
    c.created_at, c.updated_at, c.archived_at,
    (SELECT count(*) FROM knowledge.conversation_messages m
      WHERE m.conversation_id = c.id) AS message_count
"""

_SELECT_OWNED = f"""
    SELECT {_COLUMNS}
    FROM knowledge.conversations c
    WHERE c.id = %(id)s AND c.owner_subject_id = %(owner)s
"""

_SELECT_BY_THREAD = f"""
    SELECT {_COLUMNS}
    FROM knowledge.conversations c
    WHERE c.thread_id = %(thread_id)s
"""

_LIST_OWNED = f"""
    SELECT {_COLUMNS}
    FROM knowledge.conversations c
    WHERE c.owner_subject_id = %(owner)s AND c.archived_at IS NULL
    ORDER BY c.updated_at DESC
    LIMIT %(limit)s
"""

_SELECT_MESSAGES = """
    SELECT id, conversation_id, data_source_id, role, sequence, content,
           payload, request_id, created_at
    FROM knowledge.conversation_messages
    WHERE conversation_id = %(conversation_id)s
      AND (%(before)s::integer IS NULL OR sequence < %(before)s::integer)
    ORDER BY sequence DESC
    LIMIT %(limit)s
"""

_NEXT_SEQUENCE = """
    SELECT coalesce(max(sequence) + 1, 0) AS next
    FROM knowledge.conversation_messages
    WHERE conversation_id = %(conversation_id)s
"""

_INSERT_MESSAGE = """
    INSERT INTO knowledge.conversation_messages
        (id, conversation_id, data_source_id, role, sequence, content,
         payload, request_id, created_at)
    VALUES
        (%(id)s, %(conversation_id)s, %(data_source_id)s, %(role)s,
         %(sequence)s, %(content)s, %(payload)s, %(request_id)s, %(created_at)s)
"""

_TOUCH = """
    UPDATE knowledge.conversations
    SET updated_at = now()
    WHERE id = %(id)s
"""

_RENAME = f"""
    UPDATE knowledge.conversations c
    SET title = %(title)s, updated_at = now()
    WHERE c.id = %(id)s AND c.owner_subject_id = %(owner)s
    RETURNING {_COLUMNS}
"""

_ARCHIVE = """
    UPDATE knowledge.conversations
    SET archived_at = now(), updated_at = now()
    WHERE id = %(id)s AND owner_subject_id = %(owner)s AND archived_at IS NULL
"""


class PostgresConversationStore(ConversationStore):
    def __init__(self, pool: AsyncConnectionPool[Any]) -> None:
        self._pool = pool

    async def create(self, conversation: Conversation) -> Conversation:
        async with (
            self._pool.connection() as connection,
            connection.cursor() as cursor,
        ):
            await cursor.execute(
                _INSERT_CONVERSATION,
                {
                    "id": conversation.id,
                    "owner": conversation.owner_subject_id,
                    "data_source_id": conversation.data_source_id,
                    "thread_id": conversation.thread_id,
                    "title": conversation.title,
                    "created_at": conversation.created_at,
                    "updated_at": conversation.updated_at,
                },
            )
        # A concurrent first turn on the same thread may have won the insert.
        # Return whichever row now owns the thread so both callers agree.
        existing = await self.by_thread(conversation.thread_id)
        return existing if existing is not None else conversation

    async def get(
        self, conversation_id: UUID, *, owner_subject_id: str
    ) -> Conversation | None:
        return await self._one(
            _SELECT_OWNED, {"id": conversation_id, "owner": owner_subject_id}
        )

    async def by_thread(self, thread_id: str) -> Conversation | None:
        return await self._one(_SELECT_BY_THREAD, {"thread_id": thread_id})

    async def list_for(
        self, owner_subject_id: str, *, limit: int = 50
    ) -> list[Conversation]:
        async with (
            self._pool.connection() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(
                _LIST_OWNED, {"owner": owner_subject_id, "limit": limit}
            )
            rows = cast("list[dict[str, Any]]", await cursor.fetchall())
        return [_conversation(row) for row in rows]

    async def messages(
        self, conversation_id: UUID, *, limit: int = 100, before: int | None = None
    ) -> list[ConversationMessage]:
        async with (
            self._pool.connection() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(
                _SELECT_MESSAGES,
                {
                    "conversation_id": conversation_id,
                    "limit": limit,
                    "before": before,
                },
            )
            rows = cast("list[dict[str, Any]]", await cursor.fetchall())
        # Selected newest-first so the limit keeps the *latest* turns; returned
        # oldest-first because that is the order a transcript is read in.
        return [_message(row) for row in reversed(rows)]

    async def append_turn(
        self,
        conversation: Conversation,
        *,
        question: str,
        answer: str,
        payload: dict[str, Any],
        request_id: str | None,
    ) -> tuple[ConversationMessage, ConversationMessage]:
        capped = bounded_payload(payload)
        async with self._pool.connection() as connection, connection.transaction():
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(
                    _NEXT_SEQUENCE, {"conversation_id": conversation.id}
                )
                row = cast("dict[str, Any]", await cursor.fetchone())
            start = int(row["next"])
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
                payload=capped,
                request_id=request_id,
            )
            async with connection.cursor() as cursor:
                for message in (asked, answered):
                    await cursor.execute(
                        _INSERT_MESSAGE,
                        {
                            "id": message.id,
                            "conversation_id": message.conversation_id,
                            "data_source_id": message.data_source_id,
                            "role": message.role.value,
                            "sequence": message.sequence,
                            "content": message.content,
                            "payload": Json(message.payload),
                            "request_id": message.request_id,
                            "created_at": message.created_at,
                        },
                    )
                await cursor.execute(_TOUCH, {"id": conversation.id})
        return asked, answered

    async def rename(
        self, conversation_id: UUID, *, owner_subject_id: str, title: str
    ) -> Conversation | None:
        return await self._one(
            _RENAME,
            {"id": conversation_id, "owner": owner_subject_id, "title": title},
        )

    async def archive(
        self, conversation_id: UUID, *, owner_subject_id: str
    ) -> bool:
        async with (
            self._pool.connection() as connection,
            connection.cursor() as cursor,
        ):
            await cursor.execute(
                _ARCHIVE, {"id": conversation_id, "owner": owner_subject_id}
            )
            return bool(cursor.rowcount > 0)

    async def _one(
        self, sql: str, parameters: dict[str, Any]
    ) -> Conversation | None:
        async with (
            self._pool.connection() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(sql, parameters)
            row = cast("dict[str, Any] | None", await cursor.fetchone())
        return None if row is None else _conversation(row)


def _conversation(row: dict[str, Any]) -> Conversation:
    return Conversation(
        id=row["id"],
        owner_subject_id=row["owner_subject_id"],
        data_source_id=row["data_source_id"],
        thread_id=row["thread_id"],
        title=row["title"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        archived_at=row["archived_at"],
        message_count=int(row.get("message_count") or 0),
    )


def _message(row: dict[str, Any]) -> ConversationMessage:
    return ConversationMessage(
        id=row["id"],
        conversation_id=row["conversation_id"],
        data_source_id=row["data_source_id"],
        role=MessageRole(row["role"]),
        sequence=int(row["sequence"]),
        content=row["content"],
        payload=row["payload"] or {},
        request_id=row["request_id"],
        created_at=row["created_at"],
    )


__all__ = ["PostgresConversationStore"]
