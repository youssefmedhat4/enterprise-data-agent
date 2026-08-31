from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent.context import AnalysisPlan
from app.contracts.analytics import ChartSpec, GroundedClaim

ResponseModelT = TypeVar("ResponseModelT", bound=BaseModel)


type ProviderErrorCategory = Literal[
    "authentication_failed",
    "permission_denied",
    "quota_exceeded",
    "payment_required",
    "rate_limited",
    "tool_use_failed",
    "structured_output_failed",
    "model_unavailable",
    "timeout",
    "connection_failed",
    "provider_unavailable",
    "unknown",
]


@dataclass(frozen=True)
class ProviderErrorInfo:
    exception_type: str
    category: ProviderErrorCategory
    http_status: int | None = None
    provider_code: str | None = None


class LLMGatewayError(RuntimeError):
    """Raised when an LLM provider cannot return a valid structured response."""

    def __init__(
        self,
        message: str,
        *,
        provider_error: ProviderErrorInfo | None = None,
        sanitized_structured_output: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.provider_error = provider_error
        self.sanitized_structured_output = sanitized_structured_output


class UnknownModelAliasError(LLMGatewayError):
    """Raised when application code requests an unconfigured logical model alias."""


class LLMRateLimitError(LLMGatewayError):
    """Raised when a provider rejects a request because of a rate limit."""


class LLMConnectionError(LLMGatewayError):
    """Raised when the configured model service cannot be reached."""


class LLMModelUnavailableError(LLMGatewayError):
    """Raised when the configured physical model is not available to the provider."""


class LLMOutOfMemoryError(LLMGatewayError):
    """Raised when local model inference cannot allocate sufficient memory."""


class LLMTimeoutError(LLMGatewayError):
    """Raised when model inference exceeds the configured timeout."""


class LLMAuthenticationError(LLMGatewayError):
    """Raised when a provider rejects its configured credential."""


class LLMPermissionDeniedError(LLMGatewayError):
    """Raised when valid credentials lack permission for the requested operation."""


class LLMQuotaExceededError(LLMGatewayError):
    """Raised when a provider account or project quota is exhausted."""


class LLMPaymentRequiredError(LLMGatewayError):
    """Raised when a provider requires account billing or model entitlement."""


class LLMToolUseError(LLMGatewayError):
    """Raised when provider-side structured tool generation fails."""


class LLMProviderUnavailableError(LLMGatewayError):
    """Raised when the provider reports a temporary server-side failure."""


class InvalidStructuredModelOutputError(LLMGatewayError):
    """Raised when a provider response does not satisfy the requested schema."""


class ModelOutputTruncatedError(InvalidStructuredModelOutputError):
    """Raised when the model ran out of output budget mid-response.

    A subclass rather than a flag because callers that record failures record
    the exception type. Reported as a generic schema failure, a model that
    simply ran long is indistinguishable from one returning malformed JSON, and
    the reader goes looking for a bad schema instead of raising the limit --
    which is exactly what happened to every background proposal on this
    deployment.
    """


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


@dataclass(frozen=True)
class LLMUsageSnapshot:
    call_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    usage_available_calls: int = 0
    cached_tokens: int = 0
    cached_tokens_available_calls: int = 0
    cost_usd: float = 0.0
    cost_available_calls: int = 0
    retry_count: int | None = None
    model_calls: dict[str, int] = field(default_factory=dict)
    provider_calls: dict[str, int] = field(default_factory=dict)


@runtime_checkable
class LLMGatewayWithUsage(Protocol):
    def usage_snapshot(self) -> LLMUsageSnapshot:
        """Return aggregate, non-content telemetry for completed gateway calls."""


class SQLGeneration(BaseModel):
    action: Literal["execute", "clarify", "block"]
    sql: str | None = None
    explanation: str = ""
    clarification_question: str | None = None
    block_reason: str | None = None
    analysis: AnalysisPlan = Field(default_factory=AnalysisPlan)

    @model_validator(mode="after")
    def validate_action(self) -> "SQLGeneration":
        if self.action == "execute":
            if not self.sql:
                raise ValueError("The execute action requires SQL.")
            if self.clarification_question or self.block_reason:
                raise ValueError("The execute action cannot contain clarify/block fields.")
        elif self.action == "clarify":
            if self.sql or not self.clarification_question or self.block_reason:
                raise ValueError("The clarify action requires a question and cannot contain SQL.")
        elif self.sql or self.clarification_question or not self.block_reason:
            raise ValueError("The block action requires a reason and cannot contain SQL.")
        return self


class AnswerGeneration(BaseModel):
    answer: str
    claims: list[GroundedClaim] = Field(default_factory=list)
    chart: ChartSpec | None = None


class SQLRepair(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repaired_sql: str = Field(min_length=1)
