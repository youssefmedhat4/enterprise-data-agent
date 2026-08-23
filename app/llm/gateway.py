from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, model_validator

ResponseModelT = TypeVar("ResponseModelT", bound=BaseModel)


class LLMGatewayError(RuntimeError):
    """Raised when an LLM provider cannot return a valid structured response."""


class UnknownModelAliasError(LLMGatewayError):
    """Raised when application code requests an unconfigured logical model alias."""


class LLMGateway(Protocol):
    async def generate_structured(
        self,
        *,
        model_alias: str,
        system: str,
        user: str,
        response_model: type[ResponseModelT],
    ) -> ResponseModelT:
        """Generate a Pydantic-validated structured response."""


class SQLGeneration(BaseModel):
    sql: str | None = None
    explanation: str
    needs_clarification: bool = False
    clarification_question: str | None = None

    @model_validator(mode="after")
    def validate_action(self) -> "SQLGeneration":
        if self.needs_clarification:
            if not self.clarification_question:
                raise ValueError("A clarification question is required.")
        elif not self.sql:
            raise ValueError("SQL is required when clarification is not needed.")
        return self


class AnswerGeneration(BaseModel):
    answer: str
    chart: dict[str, Any] | None = None
