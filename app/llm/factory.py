from app.config import Settings
from app.llm.fake import FakeLLMGateway
from app.llm.gateway import LLMGateway
from app.llm.litellm_gateway import LiteLLMGateway
from app.llm.profiles import ModelProfile


def build_llm_gateway(
    settings: Settings,
    *,
    model_profile: ModelProfile | None = None,
) -> LLMGateway:
    if settings.llm_provider == "fake":
        return FakeLLMGateway()
    if model_profile is not None:
        resolved = settings.resolve_model_profile(model_profile)
        return LiteLLMGateway(
            resolved.model_aliases,
            api_keys_by_alias=resolved.api_keys_by_alias,
            api_bases_by_alias=resolved.api_bases_by_alias,
            model_options_by_alias=resolved.model_options_by_alias,
            structured_output_modes_by_alias=resolved.structured_output_modes_by_alias,
            timeout_seconds=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
            max_output_tokens=settings.llm_max_output_tokens,
            reasoning_effort=settings.llm_reasoning_effort,
        )
    settings.validate_cloud_data_for_models(settings.model_aliases.values())
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
