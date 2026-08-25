import json
from typing import Any

import pytest

from app.llm.gateway import (
    LLMAuthenticationError,
    LLMGatewayError,
    LLMOutOfMemoryError,
    LLMPaymentRequiredError,
    LLMPermissionDeniedError,
    LLMQuotaExceededError,
    LLMRateLimitError,
    LLMToolUseError,
    SQLGeneration,
    UnknownModelAliasError,
)
from app.llm.litellm_gateway import LiteLLMGateway


@pytest.mark.asyncio
async def test_litellm_gateway_routes_alias_and_validates_json() -> None:
    received: dict[str, Any] = {}

    async def completion(**kwargs: Any) -> dict[str, Any]:
        received.update(kwargs)
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "action": "execute",
                                "sql": "SELECT id FROM analytics.departments",
                                "explanation": "Lists departments.",
                            }
                        )
                    }
                }
            ]
        }

    gateway = LiteLLMGateway(
        {"sql-reasoner": "openai/test-model"},
        timeout_seconds=15,
        max_retries=3,
        completion=completion,
    )

    result = await gateway.generate_structured(
        model_alias="sql-reasoner",
        system="stable system",
        user="dynamic user",
        response_model=SQLGeneration,
    )

    assert result.sql == "SELECT id FROM analytics.departments"
    assert received == {
        "model": "openai/test-model",
        "messages": [
            {"role": "system", "content": "stable system"},
            {"role": "user", "content": "dynamic user"},
        ],
        "response_format": SQLGeneration,
        "temperature": 0,
        "timeout": 15,
        "max_retries": 3,
    }


@pytest.mark.asyncio
async def test_litellm_gateway_accepts_provider_parsed_output() -> None:
    async def completion(**kwargs: Any) -> dict[str, Any]:
        del kwargs
        return {
            "choices": [
                {
                    "message": {
                        "parsed": {
                            "action": "execute",
                            "sql": "SELECT id FROM analytics.departments",
                            "explanation": "Lists departments.",
                        }
                    }
                }
            ]
        }

    gateway = LiteLLMGateway({"sql-reasoner": "provider/model"}, completion=completion)

    result = await gateway.generate_structured(
        model_alias="sql-reasoner",
        system="system",
        user="user",
        response_model=SQLGeneration,
    )

    assert isinstance(result, SQLGeneration)


@pytest.mark.asyncio
async def test_litellm_gateway_uses_tool_call_structured_output_mode() -> None:
    received: dict[str, Any] = {}

    async def completion(**kwargs: Any) -> dict[str, Any]:
        received.update(kwargs)
        return {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "SQLGeneration",
                                    "arguments": json.dumps(
                                        {
                                            "action": "execute",
                                            "sql": "SELECT id FROM analytics.departments",
                                            "explanation": "Lists departments.",
                                        }
                                    ),
                                }
                            }
                        ]
                    }
                }
            ]
        }

    gateway = LiteLLMGateway(
        {"sql-reasoner": "zai/glm-4.5-flash"},
        structured_output_modes_by_alias={"sql-reasoner": "tool_call"},
        completion=completion,
    )

    result = await gateway.generate_structured(
        model_alias="sql-reasoner",
        system="system",
        user="user",
        response_model=SQLGeneration,
    )

    assert result.sql == "SELECT id FROM analytics.departments"
    assert "response_format" not in received
    assert received["tools"][0]["function"]["name"] == "SQLGeneration"
    assert received["tool_choice"] == "auto"


@pytest.mark.asyncio
async def test_litellm_gateway_passes_ollama_api_base_without_api_key() -> None:
    received: dict[str, Any] = {}

    async def completion(**kwargs: Any) -> dict[str, Any]:
        received.update(kwargs)
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "action": "execute",
                                "sql": "SELECT id FROM analytics.departments",
                                "explanation": "Lists departments.",
                            }
                        )
                    }
                }
            ]
        }

    gateway = LiteLLMGateway(
        {"sql-reasoner": "ollama/qwen3.6:27b"},
        api_bases_by_alias={"sql-reasoner": "http://localhost:11434"},
        model_options_by_alias={"sql-reasoner": {"num_ctx": 8192}},
        max_output_tokens=2048,
        reasoning_effort="none",
        completion=completion,
    )
    await gateway.generate_structured(
        model_alias="sql-reasoner",
        system="system",
        user="user",
        response_model=SQLGeneration,
    )

    assert received["api_base"] == "http://localhost:11434"
    assert received["max_completion_tokens"] == 2048
    assert received["reasoning_effort"] == "none"
    assert received["num_ctx"] == 8192
    assert "api_key" not in received


@pytest.mark.asyncio
async def test_litellm_gateway_passes_vertex_adc_options_without_api_key() -> None:
    received: dict[str, Any] = {}

    async def completion(**kwargs: Any) -> dict[str, Any]:
        received.update(kwargs)
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "action": "execute",
                                "sql": "SELECT id FROM analytics.departments",
                                "explanation": "Lists departments.",
                            }
                        )
                    }
                }
            ]
        }

    gateway = LiteLLMGateway(
        {"sql-reasoner": "vertex_ai/gemini-2.5-flash"},
        model_options_by_alias={
            "sql-reasoner": {
                "vertex_project": "test-project",
                "vertex_location": "global",
            }
        },
        completion=completion,
    )
    await gateway.generate_structured(
        model_alias="sql-reasoner",
        system="system",
        user="user",
        response_model=SQLGeneration,
    )

    assert received["model"] == "vertex_ai/gemini-2.5-flash"
    assert received["vertex_project"] == "test-project"
    assert received["vertex_location"] == "global"
    assert "api_key" not in received


@pytest.mark.asyncio
async def test_litellm_gateway_rejects_unknown_alias_without_provider_call() -> None:
    gateway = LiteLLMGateway({})

    with pytest.raises(UnknownModelAliasError, match="missing"):
        await gateway.generate_structured(
            model_alias="missing",
            system="system",
            user="user",
            response_model=SQLGeneration,
        )


@pytest.mark.asyncio
async def test_litellm_gateway_wraps_invalid_provider_response() -> None:
    async def completion(**kwargs: Any) -> dict[str, Any]:
        del kwargs
        return {"choices": []}

    gateway = LiteLLMGateway({"sql-reasoner": "provider/model"}, completion=completion)

    with pytest.raises(LLMGatewayError, match="no response choices"):
        await gateway.generate_structured(
            model_alias="sql-reasoner",
            system="system",
            user="user",
            response_model=SQLGeneration,
        )


@pytest.mark.asyncio
async def test_litellm_gateway_classifies_local_model_out_of_memory() -> None:
    async def completion(**kwargs: Any) -> dict[str, Any]:
        del kwargs
        raise RuntimeError("llama-server reported out-of-memory; unable to allocate buffer")

    gateway = LiteLLMGateway(
        {"sql-reasoner": "ollama/qwen3.6:27b"},
        completion=completion,
    )

    with pytest.raises(LLMOutOfMemoryError):
        await gateway.generate_structured(
            model_alias="sql-reasoner",
            system="system",
            user="user",
            response_model=SQLGeneration,
        )


@pytest.mark.asyncio
async def test_litellm_gateway_classifies_exhausted_provider_rate_limit() -> None:
    class RateLimitError(Exception):
        status_code = 429
        body = {"error": {"code": "rate_limit_exceeded"}}

    async def completion(**kwargs: Any) -> dict[str, Any]:
        del kwargs
        raise RateLimitError("429")

    gateway = LiteLLMGateway(
        {"sql-reasoner": "groq/qwen/qwen3.6-27b"},
        completion=completion,
    )

    with pytest.raises(LLMRateLimitError) as caught:
        await gateway.generate_structured(
            model_alias="sql-reasoner",
            system="system",
            user="user",
            response_model=SQLGeneration,
        )

    assert caught.value.provider_error is not None
    assert caught.value.provider_error.category == "rate_limited"
    assert caught.value.provider_error.http_status == 429
    assert caught.value.provider_error.provider_code == "rate_limit_exceeded"


@pytest.mark.asyncio
async def test_litellm_gateway_classifies_groq_tool_use_failure() -> None:
    class BadRequestError(Exception):
        status_code = 400
        body = None

    async def completion(**kwargs: Any) -> dict[str, Any]:
        del kwargs
        raise BadRequestError(
            "Error code: 400, code=tool_use_failed, sensitive failed_generation content"
        )

    gateway = LiteLLMGateway(
        {"sql-reasoner": "groq/qwen/qwen3.6-27b"},
        completion=completion,
    )

    with pytest.raises(LLMToolUseError) as caught:
        await gateway.generate_structured(
            model_alias="sql-reasoner",
            system="system",
            user="user",
            response_model=SQLGeneration,
        )

    detail = caught.value.provider_error
    assert detail is not None
    assert detail.exception_type == "BadRequestError"
    assert detail.http_status == 400
    assert detail.provider_code == "tool_use_failed"
    assert detail.category == "tool_use_failed"
    assert "sensitive" not in str(caught.value)
    assert "sensitive" not in repr(detail)


@pytest.mark.asyncio
async def test_litellm_gateway_classifies_authentication_failure() -> None:
    class AuthenticationError(Exception):
        status_code = 401
        body = {"error": {"code": "invalid_api_key"}}

    async def completion(**kwargs: Any) -> dict[str, Any]:
        del kwargs
        raise AuthenticationError("credential rejected")

    gateway = LiteLLMGateway(
        {"sql-reasoner": "groq/qwen/qwen3.6-27b"},
        completion=completion,
    )

    with pytest.raises(LLMAuthenticationError) as caught:
        await gateway.generate_structured(
            model_alias="sql-reasoner",
            system="system",
            user="user",
            response_model=SQLGeneration,
        )

    detail = caught.value.provider_error
    assert detail is not None
    assert detail.category == "authentication_failed"
    assert detail.http_status == 401
    assert detail.provider_code == "invalid_api_key"


@pytest.mark.asyncio
async def test_litellm_gateway_classifies_vertex_permission_denied() -> None:
    class PermissionDeniedError(Exception):
        status_code = 403
        body = {"error": {"status": "PERMISSION_DENIED"}}

    async def completion(**kwargs: Any) -> dict[str, Any]:
        del kwargs
        raise PermissionDeniedError("sensitive resource details")

    gateway = LiteLLMGateway(
        {"sql-reasoner": "vertex_ai/gemini-2.5-flash"},
        completion=completion,
    )

    with pytest.raises(LLMPermissionDeniedError) as caught:
        await gateway.generate_structured(
            model_alias="sql-reasoner",
            system="system",
            user="user",
            response_model=SQLGeneration,
        )

    detail = caught.value.provider_error
    assert detail is not None
    assert detail.category == "permission_denied"
    assert detail.http_status == 403
    assert detail.provider_code == "PERMISSION_DENIED"
    assert "sensitive" not in str(caught.value)


@pytest.mark.asyncio
async def test_litellm_gateway_classifies_vertex_quota_exceeded() -> None:
    class ResourceExhaustedError(Exception):
        status_code = 429
        body = {"error": {"status": "RESOURCE_EXHAUSTED"}}

    async def completion(**kwargs: Any) -> dict[str, Any]:
        del kwargs
        raise ResourceExhaustedError("sensitive quota details")

    gateway = LiteLLMGateway(
        {"sql-reasoner": "vertex_ai/gemini-2.5-flash"},
        completion=completion,
    )

    with pytest.raises(LLMQuotaExceededError) as caught:
        await gateway.generate_structured(
            model_alias="sql-reasoner",
            system="system",
            user="user",
            response_model=SQLGeneration,
        )

    detail = caught.value.provider_error
    assert detail is not None
    assert detail.category == "quota_exceeded"
    assert detail.http_status == 429
    assert detail.provider_code == "RESOURCE_EXHAUSTED"
    assert "sensitive" not in str(caught.value)


@pytest.mark.asyncio
async def test_litellm_gateway_classifies_payment_required_without_exception_chain() -> None:
    class APIError(Exception):
        status_code = 402
        body = None

    async def completion(**kwargs: Any) -> dict[str, Any]:
        del kwargs
        raise APIError("code=payment_required, sensitive provider request details")

    gateway = LiteLLMGateway(
        {"sql-reasoner": "cerebras/gpt-oss-120b"},
        completion=completion,
    )

    with pytest.raises(LLMPaymentRequiredError) as caught:
        await gateway.generate_structured(
            model_alias="sql-reasoner",
            system="system",
            user="user",
            response_model=SQLGeneration,
        )

    detail = caught.value.provider_error
    assert detail is not None
    assert detail.category == "payment_required"
    assert detail.http_status == 402
    assert detail.provider_code == "payment_required"
    assert caught.value.__suppress_context__ is True
    assert "sensitive" not in str(caught.value)


@pytest.mark.asyncio
async def test_litellm_gateway_preserves_sanitized_unknown_provider_error() -> None:
    class UnexpectedProviderError(Exception):
        status_code = 400
        body = {"error": {"message": "sensitive provider payload"}}

    async def completion(**kwargs: Any) -> dict[str, Any]:
        del kwargs
        raise UnexpectedProviderError("secret request content")

    gateway = LiteLLMGateway(
        {"sql-reasoner": "groq/qwen/qwen3.6-27b"},
        completion=completion,
    )

    with pytest.raises(LLMGatewayError) as caught:
        await gateway.generate_structured(
            model_alias="sql-reasoner",
            system="system",
            user="user",
            response_model=SQLGeneration,
        )

    detail = caught.value.provider_error
    assert detail is not None
    assert detail.exception_type == "UnexpectedProviderError"
    assert detail.http_status == 400
    assert detail.provider_code is None
    assert detail.category == "unknown"
    assert "secret" not in str(caught.value)
    assert "sensitive" not in repr(detail)


@pytest.mark.asyncio
async def test_litellm_gateway_tracks_non_content_usage_metadata() -> None:
    received: dict[str, Any] = {}

    async def completion(**kwargs: Any) -> dict[str, Any]:
        received.update(kwargs)
        return {
            "model": "provider-reported-model",
            "usage": {
                "prompt_tokens": 21,
                "completion_tokens": 7,
                "total_tokens": 28,
                "prompt_tokens_details": {"cached_tokens": 5},
            },
            "_hidden_params": {
                "custom_llm_provider": "gemini",
                "response_cost": 0.00125,
                "attempted_retries": 1,
            },
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "action": "execute",
                                "sql": "SELECT id FROM analytics.departments",
                                "explanation": "Lists departments.",
                            }
                        )
                    }
                }
            ],
        }

    gateway = LiteLLMGateway(
        {"sql-reasoner": "gemini/gemini-2.5-flash"},
        api_keys_by_alias={"sql-reasoner": "unit-test-credential"},
        completion=completion,
    )
    await gateway.generate_structured(
        model_alias="sql-reasoner",
        system="system",
        user="user",
        response_model=SQLGeneration,
    )

    usage = gateway.usage_snapshot()
    assert received["api_key"] == "unit-test-credential"
    assert usage.call_count == 1
    assert usage.prompt_tokens == 21
    assert usage.completion_tokens == 7
    assert usage.total_tokens == 28
    assert usage.cached_tokens == 5
    assert usage.cost_usd == pytest.approx(0.00125)
    assert usage.retry_count == 1
    assert usage.model_calls == {"provider-reported-model": 1}
    assert usage.provider_calls == {"gemini": 1}
    assert "unit-test-credential" not in repr(usage)
