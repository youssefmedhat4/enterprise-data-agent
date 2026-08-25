from app.config import Settings
from app.llm.fake import FakeLLMGateway
from app.llm.gateway import LLMGateway
from app.llm.litellm_gateway import LiteLLMGateway


def build_llm_gateway(settings: Settings) -> LLMGateway:
    if settings.llm_provider == "fake":
        return FakeLLMGateway()
    return LiteLLMGateway(
        settings.model_aliases,
        api_keys_by_alias=settings.api_keys_by_alias,
        api_bases_by_alias=settings.api_bases_by_alias,
        model_options_by_alias=settings.model_options_by_alias,
        structured_output_modes_by_alias=settings.structured_output_modes_by_alias,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        max_output_tokens=settings.llm_max_output_tokens,
        reasoning_effort=settings.llm_reasoning_effort,
    )
