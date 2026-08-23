from collections.abc import Mapping
from typing import Any, Protocol, cast

from pydantic import ValidationError

from app.llm.gateway import (
    LLMGateway,
    LLMGatewayError,
    ResponseModelT,
    UnknownModelAliasError,
)


class CompletionCallable(Protocol):
    async def __call__(self, **kwargs: Any) -> Any: ...


class LiteLLMGateway(LLMGateway):
    def __init__(
        self,
        model_aliases: dict[str, str],
        *,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        completion: CompletionCallable | None = None,
    ) -> None:
        self._model_aliases = model_aliases
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._completion = completion

    async def generate_structured(
        self,
        *,
        model_alias: str,
        system: str,
        user: str,
        response_model: type[ResponseModelT],
    ) -> ResponseModelT:
        model = self._model_aliases.get(model_alias)
        if model is None:
            raise UnknownModelAliasError(f"Unknown logical model alias: {model_alias}")

        completion = self._completion or self._load_completion()
        try:
            response = await completion(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format=response_model,
                temperature=0,
                timeout=self._timeout_seconds,
                max_retries=self._max_retries,
            )
            message = self._first_message(response)
            parsed = self._field(message, "parsed")
            if parsed is not None:
                return response_model.model_validate(parsed)

            content = self._field(message, "content")
            if not isinstance(content, str) or not content.strip():
                raise LLMGatewayError("LiteLLM returned no structured response content.")
            return response_model.model_validate_json(content)
        except LLMGatewayError:
            raise
        except (KeyError, IndexError, TypeError, ValidationError) as exc:
            raise LLMGatewayError("LiteLLM returned an invalid structured response.") from exc
        except Exception as exc:
            raise LLMGatewayError(
                f"LiteLLM request failed with {type(exc).__name__}."
            ) from exc

    def _load_completion(self) -> CompletionCallable:
        from litellm import acompletion

        return cast(CompletionCallable, acompletion)

    def _first_message(self, response: Any) -> Any:
        choices = self._field(response, "choices")
        if not isinstance(choices, list) or not choices:
            raise LLMGatewayError("LiteLLM returned no response choices.")
        return self._field(choices[0], "message")

    def _field(self, value: Any, name: str) -> Any:
        if isinstance(value, Mapping):
            return value.get(name)
        return getattr(value, name, None)
