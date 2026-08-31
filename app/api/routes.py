import asyncio
import logging
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated, Any, cast
from uuid import UUID, uuid4

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
from app.errors import ApplicationError, ErrorCode, ErrorResponse, normalize_error
from app.governance.factory import build_governance_gateway
from app.governance.gateway import GovernanceGateway
from app.knowledge.execution import DataSourceUnavailableError
from app.knowledge.factory import build_metric_intent_planner
from app.knowledge.runtime import KnowledgeRuntime, build_knowledge_runtime
from app.knowledge.seed import DEFAULT_DATA_SOURCE_ID
from app.knowledge.triggers import CandidateTrigger
from app.llm.factory import build_llm_gateway
from app.llm.gateway import LLMGateway, LLMGatewayWithUsage, LLMUsageSnapshot
from app.metrics.factory import build_metric_gateway
from app.metrics.gateway import MetricGateway, MetricProviderUnavailableError
from app.observability.factory import build_trace_service
from app.observability.gateway import TraceService
from app.routing.planner import MetricRequestPlanner
from app.security.sql_validation import SQLValidator
from app.semantic.entities import sampleable_columns
from app.semantic.entity_values import DatabaseEntityValueGateway
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


_knowledge_lock = asyncio.Lock()


async def get_knowledge_runtime(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> KnowledgeRuntime:
    """The process-wide knowledge layer.

    Normally built by the startup hook so a misconfiguration is discovered
    before the first request. Built here on first use when the application was
    mounted without running its lifespan, which is how the test transports and
    some embedding hosts work.

    Either way the shape comes from `KNOWLEDGE_STORAGE`: this never downgrades
    to in-memory because a database was unreachable, it raises.
    """
    runtime = getattr(request.app.state, "knowledge", None)
    if runtime is not None:
        return cast(KnowledgeRuntime, runtime)
    async with _knowledge_lock:
        runtime = getattr(request.app.state, "knowledge", None)
        if runtime is None:
            runtime = await build_knowledge_runtime(settings)
            request.app.state.knowledge = runtime
    return cast(KnowledgeRuntime, runtime)


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


async def _semantic_model_for(
    knowledge: KnowledgeRuntime, data_source_id: UUID
) -> Any:
    """The datasource's confirmed semantic model, or None if there is none."""
    if knowledge.semantics is None:
        return None
    try:
        return await knowledge.semantics.load(data_source_id)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("semantic load failed: %s", type(exc).__name__)
        return None


async def _sample_columns_for(
    knowledge: KnowledgeRuntime, data_source_id: UUID
) -> tuple[str, ...]:
    """Columns the scanner may sample, from this datasource's confirmed model.

    Empty when nothing has been reviewed yet, which leaves the configured name
    list in charge -- the only sensible default for a database whose meaning
    nobody has agreed yet.
    """
    if knowledge.semantics is None:
        return ()
    try:
        model = await knowledge.semantics.load(data_source_id)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("semantic load failed: %s", type(exc).__name__)
        return ()
    return sampleable_columns(model)


def _sample_columns_from_model(model: Any) -> tuple[str, ...]:
    """Use reviewed mappings for discovery sampling, never for live lookup."""
    return () if model is None else sampleable_columns(model)


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
    knowledge: Annotated[KnowledgeRuntime, Depends(get_knowledge_runtime)],
) -> AnalyticsResponse:
    request_id = getattr(http_request.state, "request_id", str(uuid4()))
    active_data_source_id = request.data_source_id or DEFAULT_DATA_SOURCE_ID
    semantic_model = await _semantic_model_for(knowledge, active_data_source_id)
    # The selected datasource decides which database is read, not just which
    # knowledge applies. The client supplies an id and nothing else; the
    # connection reference and its DSN are looked up server-side, so a request
    # cannot point execution at a database of its choosing.
    execution_gateway = db_gateway
    execution_schemas = settings.database_allowed_schemas
    if knowledge.execution is not None:
        try:
            context = await knowledge.execution.context_for(
                active_data_source_id,
                sample_columns=_sample_columns_from_model(semantic_model),
            )
        except DataSourceUnavailableError as exc:
            # Never fall back to the default database: answering from a
            # different source than the one asked for is worse than failing.
            raise ApplicationError(
                ErrorCode.DATABASE_UNAVAILABLE,
                "The selected data source is unavailable.",
                status_code=503,
                retryable=True,
                request_id=request_id,
            ) from exc
        execution_gateway = context.gateway
        execution_schemas = context.allowed_schemas
    # Thread identity carries the datasource, so a thread started against one
    # database cannot supply prior context to another. Switching datasource in
    # the client yields a different thread key and therefore a fresh context.
    thread_id = request.thread_id or f"{active_data_source_id}:{uuid4()}"
    selected_model_profile = settings.resolve_model_profile(request.model_profile)
    # Per request, because the planner must use the model this request selected.
    intent_planner = build_metric_intent_planner(knowledge.retriever, llm_gateway)
    graph = build_graph(
        db_gateway=execution_gateway,
        llm_gateway=llm_gateway,
        sql_validator=SQLValidator(
            max_rows=settings.query_row_limit,
            allowed_schemas=frozenset(execution_schemas),
        ),
        checkpointer=checkpointer,
        semantic_gateway=semantic_gateway,
        sql_generation_provider=settings.sql_generation_provider,
        metric_gateway=metric_gateway,
        metric_planner=MetricRequestPlanner(
            entity_value_gateway=(
                DatabaseEntityValueGateway(execution_gateway)
                if semantic_model is not None
                else None
            ),
            semantic_model=semantic_model,
        ),
        metric_registry=knowledge.registry,
        data_source_id=active_data_source_id,
        metric_intent_planner=intent_planner,
        question_memory=(
            knowledge.memory if settings.question_memory_enabled else None
        ),
        # Confirmed meanings select tables when physical names do not
        # resemble how anyone asks the question.
        semantic_model=semantic_model,
        guidance_store=knowledge.guidance,
        # Only when learning is enabled and the queue is persistent: an
        # in-memory queue would neither coordinate workers nor survive restart.
        candidate_trigger=(
            CandidateTrigger(
                settings=settings,
                jobs=knowledge.jobs,
                candidates=knowledge.candidates,
            )
            if settings.question_memory_enabled and knowledge.jobs is not None
            else None
        ),
        enable_query_router=True,
        authorization_gateway=authorization_gateway,
        governance_gateway=governance_gateway,
        trace_service=trace_service,
        entity_value_gateway=(
            DatabaseEntityValueGateway(execution_gateway)
            if semantic_model is not None
            else None
        ),
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
        # A datasource gateway belongs to the provider's pool and is
        # reused across requests; only the process-default one built for
        # this request is closed here.
        if execution_gateway is db_gateway:
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
        data_source_id=active_data_source_id,
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
