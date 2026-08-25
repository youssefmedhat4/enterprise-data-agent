import json
import re
from collections import Counter
from collections.abc import Mapping
from typing import Any, Literal, Protocol, cast

from pydantic import ValidationError

from app.llm.gateway import (
    InvalidStructuredModelOutputError,
    LLMAuthenticationError,
    LLMConnectionError,
    LLMGateway,
    LLMGatewayError,
    LLMModelUnavailableError,
    LLMOutOfMemoryError,
    LLMPaymentRequiredError,
    LLMPermissionDeniedError,
    LLMProviderUnavailableError,
    LLMQuotaExceededError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMToolUseError,
    LLMUsageSnapshot,
    ProviderErrorCategory,
    ProviderErrorInfo,
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
        api_keys_by_alias: Mapping[str, str] | None = None,
        api_bases_by_alias: Mapping[str, str] | None = None,
        model_options_by_alias: Mapping[str, Mapping[str, Any]] | None = None,
        structured_output_modes_by_alias: Mapping[str, Literal["response_format", "tool_call"]]
        | None = None,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        max_output_tokens: int | None = None,
        reasoning_effort: str | None = None,
        completion: CompletionCallable | None = None,
    ) -> None:
        self._model_aliases = model_aliases
        self._api_keys_by_alias = dict(api_keys_by_alias or {})
        self._api_bases_by_alias = dict(api_bases_by_alias or {})
        self._model_options_by_alias = {
            alias: dict(options) for alias, options in (model_options_by_alias or {}).items()
        }
        self._structured_output_modes_by_alias = dict(structured_output_modes_by_alias or {})
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._max_output_tokens = max_output_tokens
        self._reasoning_effort = reasoning_effort
        self._completion = completion
        self._call_count = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._total_tokens = 0
        self._usage_available_calls = 0
        self._cached_tokens = 0
        self._cached_tokens_available_calls = 0
        self._cost_usd = 0.0
        self._cost_available_calls = 0
        self._retry_count: int | None = None
        self._model_calls: Counter[str] = Counter()
        self._provider_calls: Counter[str] = Counter()

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
        structured_output_mode = self._structured_output_modes_by_alias.get(
            model_alias, "response_format"
        )
        self._call_count += 1
        raw_structured: Any = None
        try:
            request: dict[str, Any] = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0,
                "timeout": self._timeout_seconds,
                "max_retries": self._max_retries,
            }
            if structured_output_mode == "tool_call":
                tool_name = response_model.__name__
                request["tools"] = [
                    {
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "description": (
                                "You must call this function to return the requested "
                                "structured response."
                            ),
                            "parameters": response_model.model_json_schema(),
                        },
                    }
                ]
                request["tool_choice"] = "auto"
            else:
                request["response_format"] = response_model
            if api_key := self._api_keys_by_alias.get(model_alias):
                request["api_key"] = api_key
            if api_base := self._api_bases_by_alias.get(model_alias):
                request["api_base"] = api_base
            if self._max_output_tokens is not None:
                request["max_completion_tokens"] = self._max_output_tokens
            if self._reasoning_effort is not None:
                request["reasoning_effort"] = self._reasoning_effort
            request.update(self._model_options_by_alias.get(model_alias, {}))
            response = await completion(
                **request,
            )
            self._record_response_metrics(response, configured_model=model)
            message = self._first_message(response)
            parsed = self._field(message, "parsed")
            if parsed is not None:
                raw_structured = parsed
                return response_model.model_validate(parsed)

            if structured_output_mode == "tool_call":
                arguments = self._first_tool_arguments(message, response_model.__name__)
                raw_structured = arguments
                if isinstance(arguments, Mapping):
                    return response_model.model_validate(arguments)
                if isinstance(arguments, str) and arguments.strip():
                    return response_model.model_validate_json(arguments)
                raise InvalidStructuredModelOutputError(
                    "LiteLLM returned no structured tool arguments."
                )

            content = self._field(message, "content")
            raw_structured = content
            if not isinstance(content, str) or not content.strip():
                raise InvalidStructuredModelOutputError(
                    "LiteLLM returned no structured response content."
                )
            return response_model.model_validate_json(content)
        except InvalidStructuredModelOutputError:
            raise
        except LLMGatewayError:
            raise
        except (KeyError, IndexError, TypeError, ValidationError) as exc:
            details = _provider_error_info(exc, forced_category="structured_output_failed")
            raise InvalidStructuredModelOutputError(
                "LiteLLM returned an invalid structured response.",
                provider_error=details,
                sanitized_structured_output=_sanitize_structured_output(raw_structured),
            ) from None
        except Exception as exc:
            details = _provider_error_info(exc)
            exception_name = type(exc).__name__.replace("_", "").lower()
            exception_text = str(exc).casefold()
            if details.category == "rate_limited" or "ratelimit" in exception_name:
                raise LLMRateLimitError(
                    "LiteLLM provider rate limit reached.",
                    provider_error=details,
                ) from None
            if details.category == "authentication_failed":
                raise LLMAuthenticationError(
                    "LiteLLM provider authentication failed.",
                    provider_error=details,
                ) from None
            if details.category == "permission_denied":
                raise LLMPermissionDeniedError(
                    "LiteLLM provider permission denied.",
                    provider_error=details,
                ) from None
            if details.category == "quota_exceeded":
                raise LLMQuotaExceededError(
                    "LiteLLM provider quota exceeded.",
                    provider_error=details,
                ) from None
            if details.category == "payment_required":
                raise LLMPaymentRequiredError(
                    "LiteLLM provider requires billing or model entitlement.",
                    provider_error=details,
                ) from None
            if details.category == "tool_use_failed":
                raise LLMToolUseError(
                    "LiteLLM provider structured tool use failed.",
                    provider_error=details,
                ) from None
            if details.category == "structured_output_failed":
                raise InvalidStructuredModelOutputError(
                    "LiteLLM provider structured output failed.",
                    provider_error=details,
                ) from None
            if details.category == "timeout" or "timeout" in exception_name:
                raise LLMTimeoutError(
                    "LiteLLM provider request timed out.",
                    provider_error=details,
                ) from None
            if details.category == "model_unavailable" or (
                "model" in exception_text
                and any(
                    marker in exception_text
                    for marker in ("not found", "not installed", "does not exist", "pull")
                )
            ):
                raise LLMModelUnavailableError(
                    "The configured LiteLLM model is unavailable.",
                    provider_error=details,
                ) from None
            if any(
                marker in exception_text
                for marker in (
                    "out of memory",
                    "out-of-memory",
                    "cannot allocate memory",
                    "unable to allocate",
                    "cuda error",
                )
            ):
                raise LLMOutOfMemoryError(
                    "The configured LiteLLM model could not allocate memory.",
                    provider_error=details,
                ) from None
            if (
                details.category == "connection_failed"
                or "connection" in exception_name
                or any(
                    marker in exception_text
                    for marker in (
                        "connection refused",
                        "failed to connect",
                        "all connection attempts",
                    )
                )
            ):
                raise LLMConnectionError(
                    "The configured LiteLLM provider is unavailable.",
                    provider_error=details,
                ) from None
            if details.category == "provider_unavailable":
                raise LLMProviderUnavailableError(
                    "The configured LiteLLM provider is temporarily unavailable.",
                    provider_error=details,
                ) from None
            raise LLMGatewayError(
                f"LiteLLM request failed with {type(exc).__name__}.",
                provider_error=details,
            ) from None

    def usage_snapshot(self) -> LLMUsageSnapshot:
        return LLMUsageSnapshot(
            call_count=self._call_count,
            prompt_tokens=self._prompt_tokens,
            completion_tokens=self._completion_tokens,
            total_tokens=self._total_tokens,
            usage_available_calls=self._usage_available_calls,
            cached_tokens=self._cached_tokens,
            cached_tokens_available_calls=self._cached_tokens_available_calls,
            cost_usd=self._cost_usd,
            cost_available_calls=self._cost_available_calls,
            retry_count=self._retry_count,
            model_calls=dict(self._model_calls),
            provider_calls=dict(self._provider_calls),
        )

    def _load_completion(self) -> CompletionCallable:
        from litellm import acompletion

        return cast(CompletionCallable, acompletion)

    def _first_message(self, response: Any) -> Any:
        choices = self._field(response, "choices")
        if not isinstance(choices, list) or not choices:
            raise InvalidStructuredModelOutputError("LiteLLM returned no response choices.")
        return self._field(choices[0], "message")

    def _first_tool_arguments(self, message: Any, expected_name: str) -> Any:
        tool_calls = self._field(message, "tool_calls")
        if not isinstance(tool_calls, list) or not tool_calls:
            raise InvalidStructuredModelOutputError("LiteLLM returned no structured tool call.")
        function = self._field(tool_calls[0], "function")
        if self._field(function, "name") != expected_name:
            raise InvalidStructuredModelOutputError(
                "LiteLLM returned an unexpected structured tool call."
            )
        return self._field(function, "arguments")

    def _field(self, value: Any, name: str) -> Any:
        if isinstance(value, Mapping):
            return value.get(name)
        return getattr(value, name, None)

    def _record_response_metrics(self, response: Any, *, configured_model: str) -> None:
        actual_model = self._field(response, "model")
        self._model_calls[str(actual_model or configured_model)] += 1

        hidden_params = self._field(response, "_hidden_params")
        provider = self._mapping_field(hidden_params, "custom_llm_provider")
        provider_name = str(provider or configured_model.partition("/")[0])
        self._provider_calls["ollama" if provider_name == "ollama_chat" else provider_name] += 1

        usage = self._field(response, "usage")
        if usage is not None:
            self._usage_available_calls += 1
        prompt_tokens = self._integer_field(usage, "prompt_tokens")
        completion_tokens = self._integer_field(usage, "completion_tokens")
        total_tokens = self._integer_field(usage, "total_tokens")
        self._prompt_tokens += prompt_tokens
        self._completion_tokens += completion_tokens
        self._total_tokens += total_tokens or prompt_tokens + completion_tokens

        prompt_details = self._field(usage, "prompt_tokens_details")
        cached_tokens = self._optional_integer_field(prompt_details, "cached_tokens")
        if cached_tokens is not None:
            self._cached_tokens += cached_tokens
            self._cached_tokens_available_calls += 1

        cost = self._numeric_mapping_field(hidden_params, "response_cost")
        if cost is not None:
            self._cost_usd += cost
            self._cost_available_calls += 1

        retries = self._integer_mapping_field(hidden_params, "attempted_retries")
        if retries is not None:
            self._retry_count = (self._retry_count or 0) + retries

    def _integer_field(self, value: Any, name: str) -> int:
        return self._optional_integer_field(value, name) or 0

    def _optional_integer_field(self, value: Any, name: str) -> int | None:
        raw = self._field(value, name)
        return int(raw) if isinstance(raw, int | float) else None

    def _mapping_field(self, value: Any, name: str) -> Any:
        return value.get(name) if isinstance(value, Mapping) else None

    def _numeric_mapping_field(self, value: Any, name: str) -> float | None:
        if not isinstance(value, Mapping):
            return None
        raw = value.get(name)
        return float(raw) if isinstance(raw, int | float) else None

    def _integer_mapping_field(self, value: Any, name: str) -> int | None:
        if not isinstance(value, Mapping):
            return None
        raw = value.get(name)
        return int(raw) if isinstance(raw, int | float) else None


def _provider_error_info(
    exc: Exception,
    *,
    forced_category: ProviderErrorCategory | None = None,
) -> ProviderErrorInfo:
    exception_type = _safe_identifier(type(exc).__name__, fallback="ProviderError")
    status = _http_status(exc)
    provider_code = _provider_code(exc)
    signal = " ".join(
        value.casefold()
        for value in (provider_code, _body_error_type(exc), exception_type)
        if value
    )
    category = forced_category or _provider_error_category(status, signal)
    return ProviderErrorInfo(
        exception_type=exception_type or "ProviderError",
        category=category,
        http_status=status,
        provider_code=provider_code,
    )


def _http_status(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and 100 <= status <= 599:
        return status
    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    return response_status if isinstance(response_status, int) else None


def _provider_code(exc: Exception) -> str | None:
    body = _error_body(exc)
    error = body.get("error") if isinstance(body, Mapping) else None
    candidates = [
        error.get("code") if isinstance(error, Mapping) else None,
        error.get("status") if isinstance(error, Mapping) else None,
        body.get("code") if isinstance(body, Mapping) else None,
        getattr(exc, "code", None),
        _message_provider_code(exc),
    ]
    for candidate in candidates:
        if isinstance(candidate, str):
            sanitized = _safe_identifier(candidate)
            if sanitized and not sanitized.isdigit():
                return sanitized
    return None


def _message_provider_code(exc: Exception) -> str | None:
    message = str(exc).casefold()
    known_codes = (
        "tool_use_failed",
        "rate_limit_exceeded",
        "invalid_api_key",
        "unauthenticated",
        "permission_denied",
        "quota_exceeded",
        "resource_exhausted",
        "payment_required",
        "model_not_found",
        "structured_output_failed",
    )
    return next((code for code in known_codes if code in message), None)


def _body_error_type(exc: Exception) -> str | None:
    body = _error_body(exc)
    error = body.get("error") if isinstance(body, Mapping) else None
    value = error.get("type") if isinstance(error, Mapping) else None
    return _safe_identifier(value) if isinstance(value, str) else None


def _error_body(exc: Exception) -> Mapping[str, Any]:
    body = getattr(exc, "body", None)
    if isinstance(body, Mapping):
        return body
    response = getattr(exc, "response", None)
    try:
        payload = response.json() if response is not None else None
    except Exception:
        return {}
    return payload if isinstance(payload, Mapping) else {}


def _provider_error_category(status: int | None, signal: str) -> ProviderErrorCategory:
    if status == 401 or any(
        marker in signal for marker in ("authentication", "unauthenticated", "invalid_api_key")
    ):
        return "authentication_failed"
    if "quota" in signal or "resource_exhausted" in signal:
        return "quota_exceeded"
    if status == 403 or "permissiondenied" in signal or "permission_denied" in signal:
        return "permission_denied"
    if status == 402 or "payment_required" in signal:
        return "payment_required"
    if status == 429 or "ratelimit" in signal or "rate_limit" in signal:
        return "rate_limited"
    if "tool_use_failed" in signal or "tooluse" in signal:
        return "tool_use_failed"
    if any(
        marker in signal
        for marker in (
            "structured_output",
            "json_schema",
            "json_validate",
            "unsupportedparams",
        )
    ):
        return "structured_output_failed"
    if status == 404 or "notfound" in signal or "model_not_found" in signal:
        return "model_unavailable"
    if status in {408, 504} or "timeout" in signal:
        return "timeout"
    if "connection" in signal:
        return "connection_failed"
    if status is not None and status >= 500:
        return "provider_unavailable"
    return "unknown"


def _safe_identifier(value: str, *, fallback: str | None = None) -> str | None:
    normalized = value.strip()
    if re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", normalized):
        return normalized
    return fallback


def _sanitize_structured_output(raw: Any) -> dict[str, Any] | None:
    value = raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {"response_was_json": False}
    if not isinstance(value, Mapping):
        return None
    allowed = (
        "action",
        "sql",
        "needs_clarification",
        "clarification_question",
        "block_reason",
    )
    sanitized: dict[str, Any] = {}
    for key in allowed:
        item = value.get(key)
        if isinstance(item, (str, int, float, bool)) or item is None:
            sanitized[key] = item[:10000] if isinstance(item, str) else item
    return sanitized or None
