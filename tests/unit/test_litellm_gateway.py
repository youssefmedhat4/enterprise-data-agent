import json
from typing import Any

import pytest

from app.llm.gateway import LLMGatewayError, SQLGeneration, UnknownModelAliasError
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
