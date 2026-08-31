import logging
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated, Any, cast
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, Request
from langgraph.checkpoint.base import BaseCheckpointSaver

from app.agent.checkpointing import (
    ConversationCheckpointStore,
    build_conversation_checkpoint_store,
)
from app.agent.graph import build_graph
from app.authentication.factory import build_authentication_gateway
from app.authentication.gateway import (
    AuthenticationCredentials,
    AuthenticationFailedError,
    AuthenticationGateway,
    UserIdentity,
)
from app.authorization.factory import build_authorization_gateway
from app.authorization.gateway import AuthorizationGateway
from app.config import Settings, get_settings
from app.contracts.analytics import (
    AnalyticalResult,
    AnalyticsRequest,
    AnalyticsResponse,
    HealthResponse,
    InternalProvenance,
)
from app.data.factory import build_database_gateway
from app.data.gateway import DatabaseGateway, DatabaseUnavailableError
from app.embeddings.gateway import EmbeddingError
from app.errors import ErrorResponse, normalize_error
from app.governance.factory import build_governance_gateway
from app.governance.gateway import GovernanceGateway
from app.knowledge.factory import (
    bootstrap_default_datasource,
    build_metric_intent_planner,
    build_metric_registry,
    build_metric_retriever,
)
from app.knowledge.retrieval import MetricRetriever
from app.llm.factory import build_llm_gateway
from app.llm.gateway import LLMGateway, LLMGatewayWithUsage, LLMUsageSnapshot
from app.metrics.factory import build_metric_gateway
from app.metrics.gateway import MetricGateway, MetricProviderUnavailableError
from app.observability.factory import build_trace_service
from app.observability.gateway import TraceService
from app.security.sql_validation import SQLValidator
from app.semantic.factory import build_semantic_gateway
from app.semantic.gateway import SemanticGateway

logger = logging.getLogger(__name__)

router = APIRouter()


@lru_cache
def _development_checkpoint_store() -> ConversationCheckpointStore:
    return build_conversation_checkpoint_store(Settings())


def get_database_gateway(settings: Annotated[Settings, Depends(get_settings)]) -> DatabaseGateway:
    return build_database_gateway(settings)


def get_llm_gateway(
    request: AnalyticsRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> LLMGateway:
    return build_llm_gateway(settings, model_profile=request.model_profile)


def get_trace_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> TraceService:
    return build_trace_service(settings)


async def get_authentication_gateway(
    settings: Annotated[Settings, Depends(get_settings)],
) -> AsyncIterator[AuthenticationGateway]:
    gateway = build_authentication_gateway(settings)
    try:
        yield gateway
    finally:
        await gateway.close()


def get_authorization_gateway(
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthorizationGateway:
    return build_authorization_gateway(settings)


def get_governance_gateway(
    settings: Annotated[Settings, Depends(get_settings)],
) -> GovernanceGateway:
    return build_governance_gateway(settings)


async def get_authenticated_identity(
    request: Request,
    gateway: Annotated[AuthenticationGateway, Depends(get_authentication_gateway)],
    trace_service: Annotated[TraceService, Depends(get_trace_service)],
    authorization: Annotated[str | None, Header()] = None,
) -> UserIdentity:
    span = trace_service.start_span(
        "authentication",
        {"request_id": getattr(request.state, "request_id", None)},
    )
    try:
        token = None
        if authorization is not None:
            scheme, separator, value = authorization.partition(" ")
            if not separator or scheme.casefold() != "bearer" or not value.strip():
                raise AuthenticationFailedError("The authorization header is invalid.")
            token = value.strip()
        identity = await gateway.authenticate(AuthenticationCredentials(bearer_token=token))
        span.set_attribute("authentication_provider", identity.provider)
        return identity
    except Exception as exc:
        span.record_error(exc)
        normalized = normalize_error(
            exc,
            request_id=getattr(request.state, "request_id", str(uuid4())),
        )
        span.set_attribute("error_code", normalized.code.value)
        raise normalized from exc
    finally:
        span.end()


def get_sql_validator(settings: Annotated[Settings, Depends(get_settings)]) -> SQLValidator:
    return SQLValidator(
        max_rows=settings.query_row_limit,
        allowed_schemas=frozenset(settings.database_allowed_schemas),
    )


def get_semantic_gateway(
    settings: Annotated[Settings, Depends(get_settings)],
) -> SemanticGateway:
    return build_semantic_gateway(settings)


def get_metric_gateway(
    settings: Annotated[Settings, Depends(get_settings)],
    db_gateway: Annotated[DatabaseGateway, Depends(get_database_gateway)],
) -> MetricGateway:
    return build_metric_gateway(settings, database=db_gateway)


#: Built once per process. Indexing embeds every certified metric document, so
#: rebuilding it per request would re-embed the whole catalog on every question.
_metric_knowledge: dict[str, Any] = {}


async def get_metric_retriever(
    settings: Annotated[Settings, Depends(get_settings)],
) -> MetricRetriever | None:
    """The retriever over the default datasource's certified metrics.

    Returns None when the knowledge layer cannot be built -- no embedding
    provider, or cloud embeddings without approval. Governed routing then falls
    back to the previous behaviour rather than failing the request, because a
    missing knowledge layer is a configuration state, not a caller error.
    """
    cached = _metric_knowledge.get("retriever")
    if cached is not None:
        return cast(MetricRetriever, cached)
    try:
        registry = build_metric_registry(settings)
        await bootstrap_default_datasource(registry)
        retriever = await build_metric_retriever(settings, registry)
    except (ValueError, EmbeddingError) as exc:
        logger.warning(
            "semantic metric routing unavailable: %s", type(exc).__name__
        )
        return None
    _metric_knowledge["registry"] = registry
    _metric_knowledge["retriever"] = retriever
    return retriever


async def get_conversation_checkpointer(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    trace_service: Annotated[TraceService, Depends(get_trace_service)],
) -> AsyncIterator[BaseCheckpointSaver[Any]]:
    if (
        settings.conversation_checkpoint_provider == "memory"
        and settings.app_env.casefold() not in {"production", "staging"}
    ):
        yield _development_checkpoint_store().saver()
        return
    span = trace_service.start_span(
        "checkpoint.initialize",
        {
            "request_id": getattr(request.state, "request_id", None),
            "checkpoint_provider": settings.conversation_checkpoint_provider,
        },
    )
    store: ConversationCheckpointStore | None = None
    try:
        try:
            store = build_conversation_checkpoint_store(settings)
            await store.initialize()
        except Exception as exc:
            span.record_error(exc)
            normalized = normalize_error(
                exc,
                request_id=getattr(request.state, "request_id", str(uuid4())),
            )
            span.set_attribute("error_code", normalized.code.value)
            raise normalized from exc
        yield store.saver()
    finally:
        span.end()
        if store is not None:
            await store.close()


@router.get("/health", response_model=HealthResponse, response_model_exclude_defaults=True)
@router.get(
    "/health/live",
    response_model=HealthResponse,
    response_model_exclude_defaults=True,
)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/health/ready",
    response_model=HealthResponse,
    responses={503: {"model": ErrorResponse}},
)
async def readiness(
    settings: Annotated[Settings, Depends(get_settings)],
    db_gateway: Annotated[DatabaseGateway, Depends(get_database_gateway)],
) -> HealthResponse:
    request_id = str(uuid4())
    metric_gateway: MetricGateway | None = None
    checkpoint_store: ConversationCheckpointStore | None = None
    try:
        if not await db_gateway.health_check():
            raise DatabaseUnavailableError("Database readiness check failed.")
        checks: dict[str, str] = {"database": "ok"}
        if settings.conversation_checkpoint_provider == "postgres":
            checkpoint_store = build_conversation_checkpoint_store(settings)
            await checkpoint_store.initialize()
            checks["checkpoint"] = "ok"
        else:
            checks["checkpoint"] = "skipped"
        if settings.readiness_require_metric_provider:
            metric_gateway = build_metric_gateway(settings, database=db_gateway)
            if not await metric_gateway.health_check():
                raise MetricProviderUnavailableError(
                    "Metric provider readiness check failed."
                )
            checks["metric_provider"] = "ok"
        else:
            checks["metric_provider"] = "skipped"
        return HealthResponse.model_validate({"status": "ready", "checks": checks})
    except Exception as exc:
        raise normalize_error(exc, request_id=request_id) from exc
    finally:
        if checkpoint_store is not None:
            await checkpoint_store.close()
        if metric_gateway is not None:
            await metric_gateway.close()
        await db_gateway.close()


@router.post(
    "/analytics/query",
    response_model=AnalyticsResponse,
    responses={
        401: {"model": ErrorResponse},
        400: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
        504: {"model": ErrorResponse},
    },
)
async def query_analytics(
    request: AnalyticsRequest,
    http_request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    identity: Annotated[UserIdentity, Depends(get_authenticated_identity)],
    db_gateway: Annotated[DatabaseGateway, Depends(get_database_gateway)],
    llm_gateway: Annotated[LLMGateway, Depends(get_llm_gateway)],
    sql_validator: Annotated[SQLValidator, Depends(get_sql_validator)],
    semantic_gateway: Annotated[SemanticGateway, Depends(get_semantic_gateway)],
    metric_gateway: Annotated[MetricGateway, Depends(get_metric_gateway)],
    checkpointer: Annotated[
        BaseCheckpointSaver[Any],
        Depends(get_conversation_checkpointer),
    ],
    authorization_gateway: Annotated[
        AuthorizationGateway,
        Depends(get_authorization_gateway),
    ],
    governance_gateway: Annotated[
        GovernanceGateway,
        Depends(get_governance_gateway),
    ],
    trace_service: Annotated[TraceService, Depends(get_trace_service)],
    metric_retriever: Annotated[
        MetricRetriever | None, Depends(get_metric_retriever)
    ],
) -> AnalyticsResponse:
    request_id = getattr(http_request.state, "request_id", str(uuid4()))
    thread_id = request.thread_id or str(uuid4())
    selected_model_profile = settings.resolve_model_profile(request.model_profile)
    # Per request, because the planner must use the model this request selected.
    intent_planner = (
        build_metric_intent_planner(metric_retriever, llm_gateway)
        if metric_retriever is not None
        else None
    )
    graph = build_graph(
        db_gateway=db_gateway,
        llm_gateway=llm_gateway,
        sql_validator=sql_validator,
        checkpointer=checkpointer,
        semantic_gateway=semantic_gateway,
        sql_generation_provider=settings.sql_generation_provider,
        metric_gateway=metric_gateway,
        metric_registry=cast(Any, _metric_knowledge.get("registry")),
        metric_intent_planner=intent_planner,
        enable_query_router=True,
        authorization_gateway=authorization_gateway,
        governance_gateway=governance_gateway,
        trace_service=trace_service,
    )
    request_span = trace_service.start_span(
        "analytics.request",
        {
            "request_id": request_id,
            "thread_id": thread_id,
            "llm_provider": settings.llm_provider,
            "model_profile": selected_model_profile.profile,
            "database_provider": settings.database_provider,
        },
    )
    try:
        result = await graph.ainvoke(
            {
                "request_id": request_id,
                "trace_id": request_id,
                "thread_id": thread_id,
                "question": request.question,
                "user_identity": identity,
            },
            config={"configurable": {"thread_id": thread_id}},
        )
        request_span.set_attribute("route", result.get("execution_route"))
        request_span.set_attribute(
            "llm_models",
            ",".join(selected_model_profile.physical_models),
        )
        if isinstance(llm_gateway, LLMGatewayWithUsage):
            usage_snapshot = llm_gateway.usage_snapshot()
            request_span.set_attribute("llm_call_count", usage_snapshot.call_count)
            request_span.set_attribute("llm_prompt_tokens", usage_snapshot.prompt_tokens)
            request_span.set_attribute(
                "llm_completion_tokens",
                usage_snapshot.completion_tokens,
            )
    except Exception as exc:
        request_span.record_error(exc)
        normalized = normalize_error(exc, request_id=request_id)
        request_span.set_attribute("error_code", normalized.code.value)
        raise normalized from exc
    finally:
        request_span.end()
        await governance_gateway.close()
        await authorization_gateway.close()
        await metric_gateway.close()
        await db_gateway.close()
    analytical_result = (
        AnalyticalResult.model_validate(result["analytical_result"])
        if result.get("analytical_result") is not None
        else None
    )
    rows = analytical_result.rows if analytical_result is not None else result["query_result"]
    columns = analytical_result.columns if analytical_result is not None else []
    internal_provenance = InternalProvenance.model_validate(result["internal_provenance"])
    usage = (
        llm_gateway.usage_snapshot()
        if isinstance(llm_gateway, LLMGatewayWithUsage)
        else LLMUsageSnapshot()
    )
    internal_provenance = internal_provenance.model_copy(
        update={
            "llm_provider": settings.llm_provider,
            "llm_models": selected_model_profile.physical_models,
            "llm_call_count": usage.call_count,
            "llm_prompt_tokens": usage.prompt_tokens,
            "llm_completion_tokens": usage.completion_tokens,
            "llm_total_tokens": usage.total_tokens,
            "model_profile": selected_model_profile.profile,
            "model_display_name": selected_model_profile.display_name,
        }
    )
    decision = result.get("authorization_decision")
    include_debug = bool(
        request.include_debug
        and settings.api_debug_provenance_enabled
        and decision is not None
        and decision.debug_allowed
    )
    public_provenance = internal_provenance.public_view(include_debug=include_debug)
    execution = result["execution_metadata"]
    return AnalyticsResponse(
        request_id=request_id,
        thread_id=thread_id,
        model_profile=selected_model_profile.profile,
        model_display_name=selected_model_profile.display_name,
        status=execution.status,
        answer=result["final_answer"],
        columns=columns,
        rows=rows,
        sources=(
            analytical_result.source_identifiers
            if analytical_result is not None
            else [public_provenance.source]
        ),
        provenance=public_provenance,
        freshness=public_provenance.freshness,
        chart=result.get("chart_spec"),
        clarification_required=result.get("needs_clarification", False),
        clarification_question=result.get("clarification_question"),
        warnings=result.get("warnings", []),
        execution=execution,
    )
