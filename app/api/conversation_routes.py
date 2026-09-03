"""Conversation transcripts: list, read, rename, archive.

Unlike the knowledge surface, these routes need no review authority. A
conversation belongs to the person who had it, and every read is scoped to the
authenticated subject in SQL rather than filtered afterwards -- a conversation
someone does not own answers 404, exactly as one that does not exist would, so
an id cannot be probed for.

Nothing here calls a model. Restoring a transcript reads rows.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.routes import get_authenticated_identity, get_knowledge_runtime
from app.authentication.gateway import UserIdentity
from app.knowledge.runtime import KnowledgeRuntime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conversations", tags=["conversations"])

#: Turns returned in one page. A long-running thread is paged rather than
#: shipped whole; a normal conversation fits well inside this.
DEFAULT_PAGE = 100
MAX_PAGE = 200


class StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConversationSummary(StrictPayload):
    """One row in the sidebar. Carries no message content."""

    id: UUID
    title: str
    data_source_id: UUID
    thread_id: str
    created_at: str
    updated_at: str
    message_count: int


class ConversationMessageView(StrictPayload):
    id: UUID
    role: str
    sequence: int
    content: str
    #: Assistant turns only: the bounded view the page is redrawn from.
    payload: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None
    created_at: str


class ConversationDetail(ConversationSummary):
    messages: list[ConversationMessageView] = Field(default_factory=list)
    #: True when older turns exist above the ones returned.
    has_more: bool = False


class RenameConversation(StrictPayload):
    title: str = Field(min_length=1, max_length=200)


def _require_store(knowledge: KnowledgeRuntime) -> Any:
    store = knowledge.conversations
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="This deployment has no conversation storage.",
        )
    return store


@router.get("", response_model=list[ConversationSummary])
async def list_conversations(
    identity: Annotated[UserIdentity, Depends(get_authenticated_identity)],
    knowledge: Annotated[KnowledgeRuntime, Depends(get_knowledge_runtime)],
    limit: int = 50,
) -> list[ConversationSummary]:
    """This subject's conversations, most recently updated first."""
    store = knowledge.conversations
    if store is None:
        return []
    return [
        _summary(conversation)
        for conversation in await store.list_for(
            identity.subject_id, limit=max(1, min(limit, MAX_PAGE))
        )
    ]


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def read_conversation(
    conversation_id: UUID,
    identity: Annotated[UserIdentity, Depends(get_authenticated_identity)],
    knowledge: Annotated[KnowledgeRuntime, Depends(get_knowledge_runtime)],
    limit: int = DEFAULT_PAGE,
    before: int | None = None,
) -> ConversationDetail:
    """One transcript, oldest turn first.

    Ownership is decided before any message, result preview or provenance
    reference is read, so an unauthorized caller never reaches the content.
    """
    store = _require_store(knowledge)
    conversation = await store.get(
        conversation_id, owner_subject_id=identity.subject_id
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="No such conversation.")

    page = max(1, min(limit, MAX_PAGE))
    messages = await store.messages(conversation.id, limit=page, before=before)
    logger.info(
        "conversation_restored",
        extra={
            "conversation_id": str(conversation.id),
            "message_count": len(messages),
        },
    )
    return ConversationDetail(
        **_summary(conversation).model_dump(),
        messages=[
            ConversationMessageView(
                id=message.id,
                role=message.role.value,
                sequence=message.sequence,
                content=message.content,
                payload=message.payload,
                request_id=message.request_id,
                created_at=message.created_at.isoformat(),
            )
            for message in messages
        ],
        has_more=bool(messages) and messages[0].sequence > 0,
    )


@router.patch("/{conversation_id}", response_model=ConversationSummary)
async def rename_conversation(
    conversation_id: UUID,
    payload: RenameConversation,
    identity: Annotated[UserIdentity, Depends(get_authenticated_identity)],
    knowledge: Annotated[KnowledgeRuntime, Depends(get_knowledge_runtime)],
) -> ConversationSummary:
    store = _require_store(knowledge)
    renamed = await store.rename(
        conversation_id,
        owner_subject_id=identity.subject_id,
        title=payload.title.strip(),
    )
    if renamed is None:
        raise HTTPException(status_code=404, detail="No such conversation.")
    return _summary(renamed)


@router.delete("/{conversation_id}", status_code=204)
async def archive_conversation(
    conversation_id: UUID,
    identity: Annotated[UserIdentity, Depends(get_authenticated_identity)],
    knowledge: Annotated[KnowledgeRuntime, Depends(get_knowledge_runtime)],
) -> None:
    """Archive rather than delete.

    The transcript stops appearing and stops being readable through these
    routes. Destroying the rows outright would also destroy the record that a
    question was asked of the database, which is not this endpoint's to make.
    """
    store = _require_store(knowledge)
    if not await store.archive(
        conversation_id, owner_subject_id=identity.subject_id
    ):
        raise HTTPException(status_code=404, detail="No such conversation.")


def _summary(conversation: Any) -> ConversationSummary:
    return ConversationSummary(
        id=conversation.id,
        title=conversation.title,
        data_source_id=conversation.data_source_id,
        thread_id=conversation.thread_id,
        created_at=conversation.created_at.isoformat(),
        updated_at=conversation.updated_at.isoformat(),
        message_count=conversation.message_count,
    )


__all__ = ["router"]
