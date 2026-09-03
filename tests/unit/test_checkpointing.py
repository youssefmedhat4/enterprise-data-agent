import pytest

from app.agent.checkpointing import (
    CheckpointConfigurationError,
    InMemoryConversationCheckpointStore,
    PostgresConversationCheckpointStore,
    build_conversation_checkpoint_store,
)
from app.config import Settings


def test_development_uses_explicit_in_memory_checkpoint_store() -> None:
    store = build_conversation_checkpoint_store(
        Settings(APP_ENV="development", CONVERSATION_CHECKPOINT_PROVIDER="memory")
    )

    assert isinstance(store, InMemoryConversationCheckpointStore)


def test_production_configuration_rejects_in_memory_checkpointing() -> None:
    with pytest.raises(ValueError, match="CONVERSATION_CHECKPOINT_PROVIDER=memory"):
        Settings(
            APP_ENV="production",
            AUTHENTICATION_PROVIDER="local",
            AUTHORIZATION_PROVIDER="opa",
            DATABASE_PROVIDER="postgres",
            LLM_PROVIDER="litellm",
            LLM_MODEL_ANALYTICS_GENERAL="ollama_chat/local-model",
            LLM_MODEL_SQL_REASONER="ollama_chat/local-model",
            CONVERSATION_CHECKPOINT_PROVIDER="memory",
        )


def test_postgres_checkpoint_selection_is_explicit_and_requires_initialization() -> None:
    settings = Settings(
        APP_ENV="development",
        CONVERSATION_CHECKPOINT_PROVIDER="postgres",
        CHECKPOINT_DATABASE_URL="postgresql://checkpoint:test@localhost:5433/checkpoints",
    )
    store = build_conversation_checkpoint_store(settings)

    assert isinstance(store, PostgresConversationCheckpointStore)
    with pytest.raises(CheckpointConfigurationError):
        store.saver()
