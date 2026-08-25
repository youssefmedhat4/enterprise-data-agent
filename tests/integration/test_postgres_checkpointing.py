from __future__ import annotations

import asyncio
import selectors
import sys
from typing import Any, cast

import pytest

from app.agent.checkpointing import (
    CheckpointProviderUnavailableError,
    build_conversation_checkpoint_store,
)
from app.agent.graph import build_graph
from app.config import Settings
from app.data.fake import FakeDatabaseGateway
from app.llm.fake import FakeLLMGateway
from app.security.sql_validation import SQLValidator


@pytest.mark.postgres
def test_postgres_checkpoint_survives_store_restart_and_resumes_thread() -> None:
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=_windows_selector_loop) as runner:
            runner.run(_exercise_checkpoint_restart())
        return
    asyncio.run(_exercise_checkpoint_restart())


def _windows_selector_loop() -> asyncio.AbstractEventLoop:
    return asyncio.SelectorEventLoop(selectors.SelectSelector())


async def _exercise_checkpoint_restart() -> None:
    settings = Settings(CONVERSATION_CHECKPOINT_PROVIDER="postgres")
    first_store = build_conversation_checkpoint_store(settings)
    try:
        await first_store.initialize()
    except CheckpointProviderUnavailableError:
        pytest.skip("Dedicated PostgreSQL checkpoint database is unavailable")
    first_graph = build_graph(
        db_gateway=FakeDatabaseGateway(),
        llm_gateway=FakeLLMGateway(),
        sql_validator=SQLValidator(),
        checkpointer=first_store.saver(),
    )
    thread_id = "persistent-checkpoint-test"
    await first_graph.ainvoke(
        {
            "request_id": "first-request",
            "trace_id": "first-request",
            "thread_id": thread_id,
            "question": "Which department has the highest payroll?",
        },
        config={"configurable": {"thread_id": thread_id}},
    )
    await first_store.close()

    second_store = build_conversation_checkpoint_store(settings)
    await second_store.initialize()
    try:
        second_graph = build_graph(
            db_gateway=FakeDatabaseGateway(),
            llm_gateway=FakeLLMGateway(),
            sql_validator=SQLValidator(),
            checkpointer=second_store.saver(),
        )
        result = cast(
            dict[str, Any],
            await second_graph.ainvoke(
                {
                    "request_id": "second-request",
                    "trace_id": "second-request",
                    "thread_id": thread_id,
                    "question": "What about last year?",
                },
                config={"configurable": {"thread_id": thread_id}},
            ),
        )
    finally:
        await second_store.close()

    assert "2025-01-01" in result["validated_sql"]
    assert result["analytical_context"].previous_question == "What about last year?"
