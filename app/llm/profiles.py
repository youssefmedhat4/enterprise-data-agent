from dataclasses import dataclass
from typing import Any, Literal

type ModelProfile = Literal["qwen", "gemini"]


@dataclass(frozen=True, slots=True)
class ResolvedModelProfile:
    profile: ModelProfile
    display_name: str
    model_aliases: dict[str, str]
    model_options_by_alias: dict[str, dict[str, Any]]
    api_keys_by_alias: dict[str, str]
    api_bases_by_alias: dict[str, str]
    structured_output_modes_by_alias: dict[
        str, Literal["response_format", "tool_call"]
    ]

    @property
    def physical_models(self) -> list[str]:
        return sorted(set(self.model_aliases.values()))


MODEL_PROFILE_DISPLAY_NAMES: dict[ModelProfile, str] = {
    "qwen": "Qwen 3.6 27B",
    "gemini": "Gemini 2.5 Flash",
}
