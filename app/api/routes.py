import asyncio
import logging
from collections.abc import AsyncIterator, Mapping
from functools import lru_cache
from typing import Annotated, Any, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Request
from langgraph.checkpoint.base import BaseCheckpointSaver

from app.agent.checkpointing import (
    ConversationCheckpointStore,
    build_conversation_checkpoint_store,
)
from app.agent.context import AnalysisPlan
from app.agent.graph import build_graph
from app.agent.lineage import lineage_from_sql
from app.agent.provenance import build_internal_provenance
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
    AnswerTraceView,
    ClarificationChoice,
    DataQualityWarning,
    ExecutionMetadata,
    HealthResponse,
    InternalProvenance,
    KnowledgeOriginView,
    KnowledgeUseView,
    LineageMetricNode,
    LineageTable,
    TimeInterpretationView,
)
from app.data.factory import build_database_gateway
from app.data.gateway import DatabaseGateway, DatabaseUnavailableError
from app.errors import ApplicationError, ErrorCode, ErrorResponse, normalize_error
from app.governance.factory import build_governance_gateway
from app.governance.gateway import GovernanceGateway
from app.knowledge.execution import DataSourceUnavailableError
from app.knowledge.factory import build_metric_intent_planner
from app.knowledge.quality import relevant_to
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
from app.semantic.entities import sampleable_columns, tables_for_question
from app.semantic.entity_values import DatabaseEntityValueGateway
from app.semantic.factory import build_semantic_gateway
from app.semantic.gateway import SemanticGateway
from app.timeintel.clock import Clock, FixedClock, SystemClock
from app.timeintel.planning import TimePlanning, plan_time

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


def _clarification_choices(result: Mapping[str, Any]) -> list[ClarificationChoice]:
    """Offer the options rather than making the user retype one.

    Only what a person already sees in the question: the business identifier
    and its label. Where the value lives stays inside.
    """
    pending = result.get("pending_entity_choice")
    if pending is None:
        return []
    return [
        ClarificationChoice(
            value=choice.canonical_key,
            label=f"{choice.display_value} ({choice.canonical_key})",
        )
        for choice in pending.choices
    ]


async def _data_quality_for(
    knowledge: KnowledgeRuntime, data_source_id: UUID, tables: list[str]
) -> list[DataQualityWarning]:
    """Concerns about the tables this answer actually read.

    Scoped to the tables in the answer's own provenance, because a payroll
    question has no business carrying an invoice freshness warning however true
    that warning is -- and a page that warns about everything is a page people
    stop reading.

    Never fails the request: an answer that is correct and unannotated is better
    than no answer.
    """
    store = knowledge.quality
    if store is None or not tables:
        return []
    try:
        assertions = await store.assertions(data_source_id, enabled_only=True)
        latest = await store.latest(data_source_id)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("quality lookup failed: %s", type(exc).__name__)
        return []
    return [
        DataQualityWarning(
            table=assertion.table_identifier,
            status=result.status.value,
            message=result.detail or f"{assertion.name} is {result.status.value}.",
        )
        for assertion, result in relevant_to(assertions, latest, set(tables))
    ]


async def _answer_trace(
    *,
    knowledge: KnowledgeRuntime,
    data_source_id: UUID,
    semantic_model: Any,
    provenance: InternalProvenance,
    public_provenance: Any,
    result: Mapping[str, Any],
    model_profile: str,
    quality: list[DataQualityWarning],
    include_sql: bool,
    include_knowledge_details: bool,
    time_planning: TimePlanning | None = None,
) -> AnswerTraceView:
    """Assemble what can be said about how this answer was produced.

    Every field is read off something already recorded: the validated statement,
    the confirmed semantic model, a metric's registered dependencies. Nothing is
    asked of a model, because a model recounting its own reasoning produces a
    plausible story and a plausible story about lineage cannot be falsified.

    The statement itself appears only under the same policy that gates debug
    provenance.
    """
    lineage = lineage_from_sql(
        provenance.validated_sql,
        semantic_model=semantic_model,
        fallback_tables=tuple(provenance.tables),
    )
    metrics = tuple(key for key in (provenance.metric_id,) if key)
    metric_lineage: list[LineageMetricNode] = []
    for key in metrics:
        try:
            registered = await knowledge.registry.get(data_source_id, key)
        except Exception:  # pragma: no cover - defensive
            registered = None
        if registered is not None and registered.dependencies:
            metric_lineage.append(
                LineageMetricNode(
                    label=key,
                    kind="derived",
                    children=[
                        LineageMetricNode(label=dependency, kind="metric")
                        for dependency in registered.dependencies
                    ],
                )
            )
        else:
            metric_lineage.append(LineageMetricNode(label=key, kind="metric"))

    knowledge_used = await _knowledge_used(
        knowledge=knowledge,
        data_source_id=data_source_id,
        provenance=provenance,
        include_details=include_knowledge_details,
    )
    return AnswerTraceView(
        data_source=public_provenance.source,
        route=provenance.route,
        execution_source=provenance.execution_source,
        semantic_entities=sorted(
            {table.entity for table in lineage.tables if table.entity}
        ),
        metrics=list(metrics),
        business_instructions=list(provenance.applied_instruction_titles),
        query_examples=list(provenance.applied_example_ids),
        knowledge_used=knowledge_used,
        resolved_entities=[
            f"{entity.get('entity', '')}: {entity.get('display_value', '')}"
            f" ({entity.get('canonical_key', '')})"
            for entity in result.get("resolved_entity_context", [])
        ],
        tables=[
            LineageTable(
                table=item.table, columns=list(item.columns), entity=item.entity
            )
            for item in lineage.tables
        ],
        metric_lineage=metric_lineage,
        column_level=lineage.column_level,
        lineage_note=lineage.note,
        validation_status=provenance.final_validation_status,
        grounded=bool(result.get("claims")),
        data_quality=quality,
        model_profile=model_profile,
        total_latency_ms=round(
            provenance.routing_latency_ms
            + provenance.metric_planning_latency_ms
            + provenance.metric_execution_latency_ms
            + provenance.repair_latency_ms,
            1,
        ),
        time=_time_view(time_planning),
        generated_sql=provenance.validated_sql if include_sql else None,
    )


async def _knowledge_used(
    *,
    knowledge: KnowledgeRuntime,
    data_source_id: UUID,
    provenance: InternalProvenance,
    include_details: bool,
) -> list[KnowledgeUseView]:
    """Resolve only normalized knowledge selected by the completed runtime path.

    Candidate records are joined after execution for history and navigation.
    They are never queried for prompt content, SQL, metric formulas, or routing.
    """
    candidates = {
        candidate.id: candidate
        for candidate in await knowledge.candidates.list(data_source_id)
    }
    clusters = {
        cluster.id: cluster
        for cluster in await knowledge.memory.clusters(data_source_id)
    }

    def origin(
        *,
        source_candidate_id: UUID | None,
        destination_type: str,
        destination_id: UUID,
        approved_at: Any,
        fallback: str,
    ) -> KnowledgeOriginView:
        candidate = candidates.get(source_candidate_id)
        learned = bool(
            candidate is not None
            and candidate.status.value == "APPROVED"
            and candidate.promoted_to_type == destination_type
            and candidate.promoted_to_id == destination_id
        )
        if not learned or candidate is None:
            resolved = "UNKNOWN" if source_candidate_id is not None else fallback
            return KnowledgeOriginView(type=cast(Any, resolved), approved_at=approved_at)
        if not include_details:
            return KnowledgeOriginView(type="LEARNED", approved_at=approved_at)
        cluster = clusters.get(candidate.cluster_id)
        return KnowledgeOriginView(
            type="LEARNED",
            candidate_id=candidate.id,
            cluster_id=cluster.id if cluster is not None else None,
            candidate_name=candidate.display_name,
            candidate_status=candidate.status.value,
            evidence_count=candidate.evidence_count,
            successful_evidence_count=candidate.successful_evidence_count,
            review_decision="APPROVED",
            approved_by=candidate.reviewed_by,
            approved_at=approved_at,
        )

    used: list[KnowledgeUseView] = []
    guidance = knowledge.guidance
    if guidance is not None:
        example_ids = set(provenance.applied_example_ids)
        for example in await guidance.examples(data_source_id):
            if str(example.id) not in example_ids:
                continue
            used.append(
                KnowledgeUseView(
                    kind="QUERY_EXAMPLE",
                    id=str(example.id),
                    name=example.question,
                    summary=(
                        "The stored statement was not run. It was shown to the "
                        "planner, which wrote fresh SQL for this question."
                    ),
                    usage="PLANNING_CONTEXT",
                    destination_type="QUERY_EXAMPLE",
                    origin=origin(
                        source_candidate_id=example.source_candidate_id,
                        destination_type="QUERY_EXAMPLE",
                        destination_id=example.id,
                        approved_at=example.approved_at,
                        fallback="MANUAL",
                    ),
                )
            )

        instruction_ids = set(provenance.applied_instruction_ids)
        for instruction in await guidance.instructions(data_source_id):
            if str(instruction.id) not in instruction_ids:
                continue
            used.append(
                KnowledgeUseView(
                    kind="BUSINESS_RULE",
                    id=str(instruction.id),
                    name=instruction.title,
                    summary=instruction.instruction,
                    usage="BUSINESS_RULE",
                    destination_type="BUSINESS_RULE",
                    origin=origin(
                        source_candidate_id=instruction.source_candidate_id,
                        destination_type="BUSINESS_RULE",
                        destination_id=instruction.id,
                        approved_at=instruction.approved_at,
                        fallback="MANUAL",
                    ),
                )
            )

    if provenance.metric_id:
        metric = await knowledge.registry.get(data_source_id, provenance.metric_id)
        if metric is not None:
            used.append(
                KnowledgeUseView(
                    kind="CERTIFIED_METRIC",
                    id=str(metric.id),
                    name=metric.display_name,
                    summary=metric.business_meaning or metric.description,
                    usage="GOVERNED_METRIC",
                    destination_type="METRIC",
                    origin=origin(
                        source_candidate_id=metric.source_candidate_id,
                        destination_type="METRIC",
                        destination_id=metric.id,
                        approved_at=metric.approved_at,
                        fallback="SEEDED" if metric.owner == "bootstrap" else "MANUAL",
                    ),
                )
            )
    return used


def _time_view(planning: TimePlanning | None) -> TimeInterpretationView | None:
    """The time interpretation, as computed rather than as described."""
    if planning is None or planning.plan is None:
        return None
    plan = planning.plan
    dimension = planning.dimension
    return TimeInterpretationView(
        phrase=plan.intent.phrase.strip(),
        label=plan.label,
        timezone=plan.timezone,
        start=plan.primary.start.isoformat(),
        end=plan.primary.end.isoformat(),
        comparison_label=plan.comparison_label,
        comparison_start=(
            plan.comparison.start.isoformat() if plan.comparison else None
        ),
        comparison_end=plan.comparison.end.isoformat() if plan.comparison else None,
        grain=plan.grain.value,
        fiscal=plan.fiscal,
        # The reviewed business name, not the physical column: a reader needs
        # to know it was measured on the invoice date, not that the column is
        # called inv_dt_chr.
        time_dimension=(dimension.concept_name or "") if dimension else "",
        policy_status=plan.policy_status,
        as_of=plan.as_of.isoformat() if plan.as_of else None,
    )


def _clock_for(request: AnalyticsRequest) -> Clock:
    """The instant this request resolves relative periods against.

    Normally now. An evaluation supplies a fixed anchor instead, because
    "revenue year to date" otherwise means something different every month and
    the regression a benchmark was written to catch never fails twice the same
    way.
    """
    anchor = getattr(request, "as_of", None)
    return FixedClock(anchor) if anchor is not None else SystemClock()


async def _plan_time_for(
    knowledge: KnowledgeRuntime,
    data_source_id: UUID,
    question: str,
    *,
    clock: Clock,
    tables: set[str],
    metric_behavior: str | None = None,
    metric_dimension_id: UUID | None = None,
) -> TimePlanning:
    """Resolve the requested period, or say why it cannot be resolved.

    Never fails the request on its own: a datasource with no time intelligence
    configured answers exactly as it did before this layer existed.
    """
    store = knowledge.time_intelligence
    if store is None:
        return TimePlanning()
    try:
        policy = await store.policy(data_source_id)
        dimensions = await store.dimensions(data_source_id)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("time policy lookup failed: %s", type(exc).__name__)
        return TimePlanning()
    return plan_time(
        question,
        policy=policy,
        dimensions=dimensions,
        tables=tables,
        clock=clock,
        metric_behavior=metric_behavior,
        metric_dimension_id=metric_dimension_id,
    )


def _time_attention_response(
    *,
    request: AnalyticsRequest,
    request_id: str,
    thread_id: str,
    data_source_id: UUID,
    profile: Any,
    planning: TimePlanning,
    source: Any,
) -> AnalyticsResponse:
    """Ask, or say plainly that the period cannot be honoured.

    Both outcomes are better than the alternative they replace: answering
    without the filter, over all of history, in a way that looks like a result.
    """
    message = planning.clarification or planning.unsupported or ""
    execution = ExecutionMetadata(
        query_id=None,
        status="clarification_required",
        row_count=0,
        duration_ms=0,
        executed_at=None,
    )
    provenance = build_internal_provenance(
        request_id=request_id,
        trace_id=request_id,
        source=source,
        generated_sql=None,
        validated_sql=None,
        rows=[],
        analysis=AnalysisPlan(),
        execution=execution,
        model_aliases=[],
    ).public_view()
    return AnalyticsResponse(
        request_id=request_id,
        thread_id=thread_id,
        model_profile=profile.profile,
        data_source_id=data_source_id,
        model_display_name=profile.display_name,
        status="clarification_required",
        answer=message,
        columns=[],
        rows=[],
        sources=[provenance.source],
        provenance=provenance,
        freshness=provenance.freshness,
        chart=None,
        clarification_required=True,
        clarification_question=message,
        warnings=[],
        execution=execution,
    )


async def _metric_time_binding(
    knowledge: KnowledgeRuntime, data_source_id: UUID, question: str
) -> dict[str, Any]:
    """A certified metric's own temporal binding, when one clearly applies.

    Only used when exactly one certified metric names itself in the question.
    Guessing which metric a sentence means is the router's job, and borrowing a
    binding from the wrong one would silently measure against the wrong date.
    """
    registry = knowledge.registry
    try:
        certified = await registry.certified(data_source_id)
    except Exception:  # pragma: no cover - defensive
        return {}
    folded = question.casefold()
    named = [
        metric
        for metric in certified
        if metric.display_name.casefold() in folded
        or metric.metric_key.casefold() in folded
    ]
    if len(named) != 1:
        return {}
    metric = named[0]
    return {
        "metric_behavior": metric.temporal_behavior.value,
        "metric_dimension_id": metric.temporal_dimension_id,
    }


def _temporal_table_scope(
    available: set[str], semantic_model: Any, question: str
) -> set[str]:
    """The tables a temporal column may be chosen from.

    The authorized schema is the wrong answer here. A small database offers all
    of its tables to the model deliberately -- narrowing a nine-table schema
    only hides things it might need -- but handing that same set to the temporal
    chooser makes every question look ambiguous: an invoice question sees the
    compensation effective date and the cost transaction date as rival
    candidates and asks which is meant, when only one of them is on a table the
    question is about.

    So the candidates come from what the question is *about*, using the same
    confirmed-meaning matching that already selects tables. Where that narrows
    to nothing the full set stands, because being asked which date is meant is
    better than picking one at random.
    """
    if semantic_model is None:
        return available
    wanted = {
        identifier
        for identifier in tables_for_question(semantic_model, question)
        if identifier in available
    }
    return wanted or available


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
    active_schema_fingerprint: str | None = None
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
        # The schema this answer was produced against, recorded with any
        # execution evidence so a reviewer can see which version it came from.
        active_schema_fingerprint = context.data_source.schema_fingerprint
    # Thread identity carries the datasource, so a thread started against one
    # database cannot supply prior context to another. Switching datasource in
    # the client yields a different thread key and therefore a fresh context.
    # What period this question covers, decided from this datasource's own
    # calendar rather than by the model. No extra model call: the phrase is
    # recognised deterministically and the boundaries are computed.
    time_planning = await _plan_time_for(
        knowledge,
        active_data_source_id,
        request.question,
        clock=_clock_for(request),
        tables=_temporal_table_scope(
            {
                table.identifier
                for table in await execution_gateway.search_schema(request.question)
            },
            semantic_model,
            request.question,
        ),
        # A certified metric already says which column it measures against and
        # whether it accumulates. Both beat inference: an approved binding is a
        # decision, and "headcount year to date" is a category error rather
        # than a query to attempt.
        **await _metric_time_binding(knowledge, active_data_source_id, request.question),
    )
    if time_planning.needs_attention:
        return _time_attention_response(
            request=request,
            request_id=request_id,
            thread_id=request.thread_id
            or f"{active_data_source_id}:{uuid4()}",
            data_source_id=active_data_source_id,
            profile=settings.resolve_model_profile(request.model_profile),
            planning=time_planning,
            source=execution_gateway.source(),
        )
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
        # The validated statement of a run that succeeded and grounded. Kept
        # apart from question memory, which stays free of SQL.
        execution_evidence=(
            knowledge.evidence if settings.question_memory_enabled else None
        ),
        schema_fingerprint=active_schema_fingerprint,
        time_planning=time_planning,
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
    quality_warnings = await _data_quality_for(
        knowledge, active_data_source_id, internal_provenance.tables
    )
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
        clarification_choices=_clarification_choices(result),
        data_quality=quality_warnings,
        trace=await _answer_trace(
            knowledge=knowledge,
            data_source_id=active_data_source_id,
            semantic_model=semantic_model,
            provenance=internal_provenance,
            public_provenance=public_provenance,
            result=result,
            model_profile=selected_model_profile.display_name,
            quality=quality_warnings,
            include_sql=include_debug,
            include_knowledge_details=bool(
                decision is not None and decision.knowledge_review_allowed
            ),
            time_planning=time_planning,
        ),
        warnings=result.get("warnings", []),
        execution=execution,
    )
