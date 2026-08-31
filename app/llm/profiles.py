from dataclasses import dataclass
from typing import Any, Literal

type ModelProfile = Literal["qwen", "gemini", "gemini_pro"]


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
    "gemini_pro": "Gemini 3.1 Pro Preview",
}

#: Profiles the public API and UI may select for this milestone. `qwen` stays
#: configurable for existing deployments but is deliberately not offered.
SELECTABLE_MODEL_PROFILES: tuple[ModelProfile, ...] = ("gemini_pro", "gemini")

DEFAULT_MODEL_PROFILE: ModelProfile = "gemini_pro"
