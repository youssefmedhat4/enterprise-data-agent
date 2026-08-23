from app.config import Settings
from app.llm.fake import FakeLLMGateway
from app.llm.gateway import LLMGateway
from app.llm.litellm_gateway import LiteLLMGateway


def build_llm_gateway(settings: Settings) -> LLMGateway:
    if settings.llm_provider == "fake":
        return FakeLLMGateway()
    return LiteLLMGateway(
        settings.model_aliases,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )
