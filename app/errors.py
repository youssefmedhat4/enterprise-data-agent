from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ErrorCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    AUTHENTICATION_FAILED = "authentication_failed"
    AUTHENTICATION_UNAVAILABLE = "authentication_unavailable"
    AUTHORIZATION_DENIED = "authorization_denied"
    AUTHORIZATION_UNAVAILABLE = "authorization_unavailable"
    CHECKPOINT_UNAVAILABLE = "checkpoint_unavailable"
    GOVERNANCE_PROVIDER_UNAVAILABLE = "governance_provider_unavailable"
    CLARIFICATION_REQUIRED = "clarification_required"
    UNSAFE_SQL = "unsafe_sql"
    SQL_VALIDATION_FAILED = "sql_validation_failed"
    SQL_SCHEMA_VALIDATION_FAILED = "sql_schema_validation_failed"
    SQL_REPAIR_FAILED = "sql_repair_failed"
    DATABASE_UNAVAILABLE = "database_unavailable"
    DATABASE_CONFIGURATION = "database_configuration_error"
    DATABASE_PERMISSION_DENIED = "database_permission_denied"
    QUERY_EXECUTION_FAILED = "query_execution_failed"
    RESULT_TOO_LARGE = "result_too_large"
    QUERY_TIMEOUT = "query_timeout"
    LLM_UNAVAILABLE = "llm_unavailable"
    LLM_RATE_LIMITED = "llm_rate_limited"
    INVALID_MODEL_OUTPUT = "invalid_structured_model_output"
    GROUNDING_FAILED = "grounding_failure"
    SEMANTIC_PROVIDER_UNAVAILABLE = "semantic_provider_unavailable"
    METRIC_PROVIDER_UNAVAILABLE = "metric_provider_unavailable"
    INVALID_METRIC_QUERY = "invalid_metric_query"
    ROUTER_FAILURE = "router_failure"
    METRIC_PLANNING_FAILED = "metric_planning_failure"
    INTERNAL_ERROR = "internal_unexpected_error"


class ApplicationError(RuntimeError):
    def __init__(
        self,
        code: ErrorCode,
        safe_message: str,
        *,
        status_code: int,
        retryable: bool,
        request_id: str | None = None,
    ) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.status_code = status_code
        self.retryable = retryable
        self.request_id = request_id

    def attach_request(self, request_id: str) -> "ApplicationError":
        self.request_id = request_id
        return self


class GroundingFailureError(ApplicationError):
    def __init__(self) -> None:
        super().__init__(
            ErrorCode.GROUNDING_FAILED,
            "The generated answer could not be verified against the query result.",
            status_code=422,
            retryable=False,
        )


class ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: ErrorCode
    message: str
    request_id: str
    retryable: bool


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: ErrorDetail


def normalize_error(exc: Exception, *, request_id: str) -> ApplicationError:
    from app.agent.checkpointing import (
        CheckpointConfigurationError,
        CheckpointProviderUnavailableError,
    )
    from app.authentication.gateway import (
        AuthenticationFailedError,
        AuthenticationProviderUnavailableError,
    )
    from app.authorization.gateway import (
        AuthorizationDeniedError,
        AuthorizationProviderUnavailableError,
        InvalidAuthorizationDecisionError,
    )
    from app.data.gateway import (
        DatabasePermissionError,
        DatabaseQueryExecutionError,
        DatabaseQueryTimeoutError,
        DatabaseReadOnlyConfigurationError,
        DatabaseResultTooLargeError,
        DatabaseUnavailableError,
    )
    from app.governance.gateway import (
        GovernanceMetadataNotFoundError,
        GovernanceProviderUnavailableError,
        InvalidGovernanceMetadataError,
    )
    from app.llm.gateway import (
        InvalidStructuredModelOutputError,
        LLMGatewayError,
        LLMRateLimitError,
    )
    from app.metrics.gateway import (
        MetricProviderUnavailableError,
        MetricQueryValidationError,
    )
    from app.routing.contracts import MetricPlanningError, QueryRouterError
    from app.security.sql_validation import (
        SQLRepairFailedError,
        SQLSchemaValidationError,
        SQLValidationError,
    )
    from app.semantic.gateway import SemanticProviderUnavailableError

    if isinstance(exc, ApplicationError):
        return exc.attach_request(request_id)
    if isinstance(exc, AuthenticationFailedError):
        return ApplicationError(
            ErrorCode.AUTHENTICATION_FAILED,
            "Authentication failed.",
            status_code=401,
            retryable=False,
            request_id=request_id,
        )
    if isinstance(exc, AuthenticationProviderUnavailableError):
        return ApplicationError(
            ErrorCode.AUTHENTICATION_UNAVAILABLE,
            "The configured identity provider is temporarily unavailable.",
            status_code=503,
            retryable=True,
            request_id=request_id,
        )
    if isinstance(exc, AuthorizationDeniedError):
        return ApplicationError(
            ErrorCode.AUTHORIZATION_DENIED,
            "The authenticated identity is not authorized for this analytics request.",
            status_code=403,
            retryable=False,
            request_id=request_id,
        )
    if isinstance(exc, AuthorizationProviderUnavailableError | InvalidAuthorizationDecisionError):
        return ApplicationError(
            ErrorCode.AUTHORIZATION_UNAVAILABLE,
            "The authorization policy service could not make a valid decision.",
            status_code=503,
            retryable=True,
            request_id=request_id,
        )
    if isinstance(exc, CheckpointConfigurationError | CheckpointProviderUnavailableError):
        return ApplicationError(
            ErrorCode.CHECKPOINT_UNAVAILABLE,
            "Conversation persistence is temporarily unavailable.",
            status_code=503,
            retryable=True,
            request_id=request_id,
        )
    if isinstance(
        exc,
        GovernanceProviderUnavailableError
        | GovernanceMetadataNotFoundError
        | InvalidGovernanceMetadataError,
    ):
        return ApplicationError(
            ErrorCode.GOVERNANCE_PROVIDER_UNAVAILABLE,
            "The configured governance catalog could not provide required metadata.",
            status_code=503,
            retryable=True,
            request_id=request_id,
        )
    if isinstance(exc, SQLRepairFailedError):
        return ApplicationError(
            ErrorCode.SQL_REPAIR_FAILED,
            "The generated query remained incompatible with the allowed schema after repair.",
            status_code=422,
            retryable=False,
            request_id=request_id,
        )
    if isinstance(exc, SQLSchemaValidationError):
        return ApplicationError(
            ErrorCode.SQL_SCHEMA_VALIDATION_FAILED,
            "The generated query is incompatible with the allowed analytics schema.",
            status_code=422,
            retryable=False,
            request_id=request_id,
        )
    if isinstance(exc, SQLValidationError):
        unsafe_markers = ("prohibited", "read-only", "exactly one", "not allowed")
        code = (
            ErrorCode.UNSAFE_SQL
            if any(marker in str(exc).lower() for marker in unsafe_markers)
            else ErrorCode.SQL_VALIDATION_FAILED
        )
        return ApplicationError(
            code,
            "The generated query did not pass read-only SQL validation.",
            status_code=400,
            retryable=False,
            request_id=request_id,
        )
    if isinstance(exc, DatabaseQueryTimeoutError | TimeoutError):
        return ApplicationError(
            ErrorCode.QUERY_TIMEOUT,
            "The analytics query timed out.",
            status_code=504,
            retryable=True,
            request_id=request_id,
        )
    if isinstance(exc, DatabaseReadOnlyConfigurationError):
        return ApplicationError(
            ErrorCode.DATABASE_CONFIGURATION,
            "The analytics database credentials are not safely configured as read-only.",
            status_code=503,
            retryable=False,
            request_id=request_id,
        )
    if isinstance(exc, DatabasePermissionError):
        return ApplicationError(
            ErrorCode.DATABASE_PERMISSION_DENIED,
            "The analytics database role cannot read the requested data.",
            status_code=403,
            retryable=False,
            request_id=request_id,
        )
    if isinstance(exc, DatabaseResultTooLargeError):
        return ApplicationError(
            ErrorCode.RESULT_TOO_LARGE,
            "The analytics result exceeds the configured response size limit.",
            status_code=422,
            retryable=False,
            request_id=request_id,
        )
    if isinstance(exc, DatabaseQueryExecutionError):
        return ApplicationError(
            ErrorCode.QUERY_EXECUTION_FAILED,
            "The analytics database could not execute the validated query.",
            status_code=422,
            retryable=False,
            request_id=request_id,
        )
    if isinstance(exc, DatabaseUnavailableError):
        return ApplicationError(
            ErrorCode.DATABASE_UNAVAILABLE,
            "The analytics database is temporarily unavailable.",
            status_code=503,
            retryable=True,
            request_id=request_id,
        )
    if isinstance(exc, LLMRateLimitError):
        return ApplicationError(
            ErrorCode.LLM_RATE_LIMITED,
            "The language model is temporarily rate limited.",
            status_code=429,
            retryable=True,
            request_id=request_id,
        )
    if isinstance(exc, InvalidStructuredModelOutputError):
        return ApplicationError(
            ErrorCode.INVALID_MODEL_OUTPUT,
            "The language model returned an invalid structured response.",
            status_code=502,
            retryable=True,
            request_id=request_id,
        )
    if isinstance(exc, LLMGatewayError):
        return ApplicationError(
            ErrorCode.LLM_UNAVAILABLE,
            "The language model is temporarily unavailable.",
            status_code=503,
            retryable=True,
            request_id=request_id,
        )
    if isinstance(exc, SemanticProviderUnavailableError):
        return ApplicationError(
            ErrorCode.SEMANTIC_PROVIDER_UNAVAILABLE,
            "The configured semantic context provider is temporarily unavailable.",
            status_code=503,
            retryable=True,
            request_id=request_id,
        )
    if isinstance(exc, MetricProviderUnavailableError):
        return ApplicationError(
            ErrorCode.METRIC_PROVIDER_UNAVAILABLE,
            "The configured governed metric provider is temporarily unavailable.",
            status_code=503,
            retryable=True,
            request_id=request_id,
        )
    if isinstance(exc, MetricQueryValidationError):
        return ApplicationError(
            ErrorCode.INVALID_METRIC_QUERY,
            "The governed metric request contains an unsupported member.",
            status_code=400,
            retryable=False,
            request_id=request_id,
        )
    if isinstance(exc, QueryRouterError):
        return ApplicationError(
            ErrorCode.ROUTER_FAILURE,
            "The analytics request could not be routed safely.",
            status_code=422,
            retryable=False,
            request_id=request_id,
        )
    if isinstance(exc, MetricPlanningError):
        return ApplicationError(
            ErrorCode.METRIC_PLANNING_FAILED,
            "The governed metric request could not be planned safely.",
            status_code=422,
            retryable=False,
            request_id=request_id,
        )
    return ApplicationError(
        ErrorCode.INTERNAL_ERROR,
        "The analytics request could not be completed.",
        status_code=500,
        retryable=False,
        request_id=request_id,
    )
