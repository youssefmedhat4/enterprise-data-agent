"""Knowledge and datasource administration endpoints.

Every route here requires the `knowledge_review` capability, which is separate
from analytics access: being allowed to read data is not authority over what the
data is defined to mean. An ordinary analyst can ask questions all day and still
gets 403 from every route in this module.

Nothing here returns a secret. A datasource exposes its `connection_ref` -- the
name of the environment variable holding its DSN -- and never the DSN, the
password, or anything resolved from them.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.routes import (
    get_authenticated_identity,
    get_authorization_gateway,
    get_database_gateway,
    get_knowledge_runtime,
)
from app.authentication.gateway import UserIdentity
from app.authorization.gateway import AuthorizationGateway, build_authorization_request
from app.config import Settings, get_settings
from app.data.gateway import DatabaseGateway, TableMetadata
from app.knowledge.candidates import CandidateStatus
from app.knowledge.contracts import ApprovalStatus, DataSourceStatus
from app.knowledge.runtime import KnowledgeRuntime
from app.knowledge.seed import DEFAULT_DATA_SOURCE_ID
from app.llm.profiles import DEFAULT_MODEL_PROFILE
from app.security.sql_validation import SQLValidator

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

#: Candidate types whose approval writes into a normalized store rather than
#: into the metric registry or the guidance store.
_LEARNED_TYPES = frozenset(
    {"FILTER", "SYNONYM", "ENTITY_ALIAS", "JOIN_RULE", "DESCRIPTION_IMPROVEMENT"}
)


class StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DataSourceSummary(StrictPayload):
    """Safe projection of a datasource. Carries no credential of any kind."""

    id: UUID
    name: str
    database_type: str
    #: The *name* of the secret, never its value.
    connection_ref: str
    status: DataSourceStatus
    schema_fingerprint: str | None = None
    is_default: bool = False
    last_scanned_at: str | None = None
    confirmed_entity_count: int = 0
    proposed_entity_count: int = 0
    certified_metric_count: int = 0
    recurring_cluster_count: int = 0


class SemanticProposalView(StrictPayload):
    id: UUID
    kind: str
    physical: str
    proposed_concept: str
    confidence: float | None = None
    status: ApprovalStatus
    detail: str = ""


class ReviewDecision(StrictPayload):
    """Approve with an optional corrected name, or reject with a reason."""

    action: str = Field(pattern="^(approve|edit|reject)$")
    concept_name: str | None = Field(default=None, max_length=200)
    reason: str | None = Field(default=None, max_length=500)


class ClusterView(StrictPayload):
    id: UUID
    canonical_summary: str
    structural_fingerprint: str
    occurrence_count: int
    successful_count: int
    first_seen_at: str
    last_seen_at: str
    status: str


class CandidateDetail(StrictPayload):
    """One labelled fact about a proposal, for the review screen."""

    label: str
    value: str


class CandidateView(StrictPayload):
    id: UUID
    candidate_type: str
    display_name: str
    description: str
    status: CandidateStatus
    evidence_count: int
    successful_evidence_count: int
    expression: str | None = None
    grain: str | None = None
    dependencies: list[str] = Field(default_factory=list)
    rejection_reason: str | None = None
    #: Type-specific detail a reviewer needs in order to decide. A reviewer
    #: shown only a name is being asked to approve something they cannot see.
    detail: list[CandidateDetail] = Field(default_factory=list)


class CertifiedMetricView(StrictPayload):
    metric_key: str
    display_name: str
    description: str
    business_meaning: str
    version: int
    status: str
    grain: str | None = None
    unit: str | None = None
    dimensions: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    semantic_expression: str | None = None
    approved_at: str | None = None
    approved_by: str | None = None


class QueryExampleView(StrictPayload):
    id: UUID
    question: str
    semantic_plan: str
    status: ApprovalStatus
    schema_fingerprint: str | None = None
    approved_at: str | None = None
    #: Withheld unless the reviewer has debug authority: the statement can
    #: describe table and column names beyond what the viewer needs.
    query_pattern: str | None = None


class BusinessInstructionView(StrictPayload):
    id: UUID
    title: str
    instruction: str
    semantic_concepts: list[str] = Field(default_factory=list)
    metric_keys: list[str] = Field(default_factory=list)
    status: ApprovalStatus
    schema_fingerprint: str | None = None
    source_candidate_id: UUID | None = None
    approved_at: str | None = None


class AuthorBusinessInstruction(StrictPayload):
    """A reviewer's own statement of what a figure means.

    Business meaning does not only arrive from the worker. A reviewer who
    watched a generated answer disagree with the business definition needs a
    way to record the definition itself, and the candidate queue only carries
    what a model proposed. This is that path: same authority, same datasource
    scope, same relevance filtering at retrieval.
    """

    title: str = Field(min_length=1, max_length=200)
    instruction: str = Field(min_length=1, max_length=4000)
    #: Business wording the question is matched against. Physical table and
    #: column names are deliberately not required here -- an instruction states
    #: meaning, and the semantic model already says where that meaning lives.
    semantic_concepts: list[str] = Field(default_factory=list, max_length=20)
    metric_keys: list[str] = Field(default_factory=list, max_length=20)


async def require_knowledge_reviewer(
    identity: Annotated[UserIdentity, Depends(get_authenticated_identity)],
    authorization_gateway: Annotated[
        AuthorizationGateway, Depends(get_authorization_gateway)
    ],
    db_gateway: Annotated[DatabaseGateway, Depends(get_database_gateway)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> UserIdentity:
    """Gate every route in this module on explicit review authority.

    The message is identical whether the identity lacks the capability or the
    resource does not exist, so probing cannot map what is here.
    """
    del settings
    decision = await authorization_gateway.authorize(
        build_authorization_request(
            identity=identity,
            tables=await db_gateway.search_schema(""),
            metrics=(),
        )
    )
    if not decision.allowed or not decision.knowledge_review_allowed:
        raise HTTPException(
            status_code=403,
            detail="Knowledge administration requires review authority.",
        )
    return identity


def get_llm_gateway_for_scan(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Any:
    """A gateway for onboarding, using the default analysis model.

    Scanning is an admin workflow rather than a request, so there is no
    per-request model choice to honour here.
    """
    from app.llm.factory import build_llm_gateway

    return build_llm_gateway(settings, model_profile=DEFAULT_MODEL_PROFILE)


def _data_source_view(source: Any) -> DataSourceSummary:
    """Project a datasource for the API. Carries the reference, never a secret."""
    return DataSourceSummary(
        id=source.id,
        name=source.name,
        database_type=source.database_type,
        connection_ref=source.connection_ref,
        status=source.status,
        schema_fingerprint=source.schema_fingerprint,
        is_default=source.is_default,
        last_scanned_at=(
            source.last_scanned_at.isoformat()
            if source.last_scanned_at is not None
            else None
        ),
    )


class RegisterDataSource(StrictPayload):
    """What a reviewer supplies to register a database.

    No credential field exists. `connection_ref` names a server-side secret and
    must be one the server already allows, so this cannot be used to point the
    scanner at an arbitrary host or to probe which environment variables exist.
    """

    name: str = Field(min_length=1, max_length=200)
    database_type: str = Field(default="postgres", max_length=50)
    connection_ref: str = Field(min_length=1, max_length=200)
    #: Schemas this database exposes. Scoped here rather than globally so one
    #: datasource's configuration cannot govern another's.
    allowed_schemas: list[str] = Field(default_factory=lambda: ["analytics"])


class ScanSummaryView(StrictPayload):
    data_source_id: UUID
    schema_fingerprint: str
    schema_changed: bool
    table_count: int
    proposed_entities: int
    proposed_attributes: int
    proposed_relationships: int
    confirmed_preserved: int
    marked_stale: int


@router.get("/connection-refs")
async def list_connection_refs(
    _: Annotated[UserIdentity, Depends(require_knowledge_reviewer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[str]:
    """Reference names a reviewer may choose. Names only, never values."""
    return list(settings.allowed_connection_refs)


@router.post("/data-sources", status_code=201)
async def register_data_source(
    payload: RegisterDataSource,
    _: Annotated[UserIdentity, Depends(require_knowledge_reviewer)],
    knowledge: Annotated[KnowledgeRuntime, Depends(get_knowledge_runtime)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DataSourceSummary:
    from app.knowledge.datasources import (
        DataSourceConnectionResolver,
        DataSourceError,
    )

    if knowledge.data_sources is None:
        raise HTTPException(
            status_code=503,
            detail="Datasource registration requires persistent knowledge storage.",
        )
    resolver = DataSourceConnectionResolver(settings)
    if not resolver.is_allowed(payload.connection_ref):
        raise HTTPException(
            status_code=422,
            detail="That connection reference is not configured on this server.",
        )
    try:
        source = await knowledge.data_sources.register(
            name=payload.name,
            database_type=payload.database_type,
            connection_ref=payload.connection_ref,
            allowed_schemas=tuple(payload.allowed_schemas),
        )
    except (DataSourceError, ValueError) as exc:
        # Covers the contract validator refusing a pasted DSN.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _data_source_view(source)


@router.post("/data-sources/{data_source_id}/scan")
async def scan_data_source(
    data_source_id: UUID,
    _: Annotated[UserIdentity, Depends(require_knowledge_reviewer)],
    knowledge: Annotated[KnowledgeRuntime, Depends(get_knowledge_runtime)],
    settings: Annotated[Settings, Depends(get_settings)],
    db_gateway: Annotated[DatabaseGateway, Depends(get_database_gateway)],
    llm_gateway: Annotated[Any, Depends(get_llm_gateway_for_scan)],
) -> ScanSummaryView:
    """Scan a datasource and persist the semantic model it implies.

    Rescanning preserves confirmed mappings and marks only what broke, so a
    schema change never silently discards review.
    """
    from app.data.factory import build_database_gateway_for
    from app.knowledge.datasources import (
        DataSourceConnectionResolver,
        DataSourceError,
    )
    from app.knowledge.discovery import SemanticDiscoveryService
    from app.knowledge.onboarding import DataSourceOnboardingService

    if knowledge.data_sources is None or knowledge.semantics is None:
        raise HTTPException(
            status_code=503,
            detail="Scanning requires persistent knowledge storage.",
        )
    source = await knowledge.data_sources.get(data_source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="No such datasource.")

    # Sending schema metadata to a cloud model is database-derived content.
    settings.validate_cloud_data_for_models(
        settings.resolve_model_profile(DEFAULT_MODEL_PROFILE).model_aliases.values()
    )

    # Scan the datasource that was asked for, not whichever one this process
    # happens to be configured with. Resolution stays inside the process; the
    # DSN is never returned, logged, or stored.
    try:
        resolver = DataSourceConnectionResolver(settings)
        scoped = build_database_gateway_for(
            settings,
            database_url=resolver.resolve(source.connection_ref),
            allowed_schemas=source.allowed_schemas,
        )
    except DataSourceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        # Type only. A connection string can appear in a driver or validation
        # message, and this endpoint must never echo one back.
        raise HTTPException(
            status_code=422,
            detail=(
                "The datasource connection could not be prepared "
                f"({type(exc).__name__})."
            ),
        ) from exc

    try:
        tables = await scoped.search_schema("")
    except Exception as exc:
        # Type only: a driver message can contain a DSN.
        raise HTTPException(
            status_code=502,
            detail=f"The datasource could not be read ({type(exc).__name__}).",
        ) from exc
    finally:
        close = getattr(scoped, "close", None)
        if close is not None:
            await close()

    service = DataSourceOnboardingService(
        discovery=SemanticDiscoveryService(llm_gateway),
        semantics=knowledge.semantics,
    )
    try:
        summary = await service.scan(
            data_source_id=data_source_id,
            tables=tables,
            previous_fingerprint=source.schema_fingerprint,
        )
    except DataSourceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    await knowledge.data_sources.record_scan(
        data_source_id, schema_fingerprint=summary.schema_fingerprint
    )
    if summary.schema_changed and knowledge.guidance is not None:
        await knowledge.guidance.mark_stale_for_schema(
            data_source_id, new_schema_fingerprint=summary.schema_fingerprint
        )
    return ScanSummaryView(
        data_source_id=summary.data_source_id,
        schema_fingerprint=summary.schema_fingerprint,
        schema_changed=summary.schema_changed,
        table_count=summary.table_count,
        proposed_entities=summary.proposed_entities,
        proposed_attributes=summary.proposed_attributes,
        proposed_relationships=summary.proposed_relationships,
        confirmed_preserved=summary.confirmed_preserved,
        marked_stale=summary.marked_stale,
    )


class ReindexSummaryView(StrictPayload):
    data_source_id: UUID
    documents_indexed: int
    embedding_provider: str
    embedding_model: str
    embedding_dimension: int


@router.post("/data-sources/{data_source_id}/reindex")
async def reindex_data_source(
    data_source_id: UUID,
    _: Annotated[UserIdentity, Depends(require_knowledge_reviewer)],
    knowledge: Annotated[KnowledgeRuntime, Depends(get_knowledge_runtime)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ReindexSummaryView:
    """Rebuild semantic retrieval with the currently configured embedder.

    Needed after the embedding provider or model changes: vectors from
    different models are not comparable, so the old ones become unusable rather
    than merely stale. Relational definitions are the authority and are not
    touched -- only the vectors derived from them are regenerated.

    Deliberately explicit rather than automatic on startup: re-embedding every
    document costs real quota, and doing it silently on each restart would spend
    it repeatedly for nothing.
    """
    # Embedding metric documents sends database-derived text to a cloud model.
    settings.validate_cloud_data_for_models(
        settings.resolve_model_profile(DEFAULT_MODEL_PROFILE).model_aliases.values()
    )
    try:
        result = await knowledge.reindex(data_source_id)
    except Exception as exc:
        # Type only: a provider message can echo the text that was embedded.
        raise HTTPException(
            status_code=502,
            detail=f"The reindex could not be completed ({type(exc).__name__}).",
        ) from exc
    return ReindexSummaryView(
        data_source_id=result.data_source_id,
        documents_indexed=result.documents_indexed,
        embedding_provider=result.embedding_provider,
        embedding_model=result.embedding_model,
        embedding_dimension=result.embedding_dimension,
    )


@router.get("/data-sources", response_model=list[DataSourceSummary])
async def list_data_sources(
    _: Annotated[UserIdentity, Depends(require_knowledge_reviewer)],
    knowledge: Annotated[KnowledgeRuntime, Depends(get_knowledge_runtime)],
) -> list[DataSourceSummary]:
    """The registered datasources this deployment can answer from."""
    if knowledge.data_sources is None:
        certified = len(await knowledge.registry.certified(DEFAULT_DATA_SOURCE_ID))
        return [
            DataSourceSummary(
                id=DEFAULT_DATA_SOURCE_ID,
                name="Company Analytics",
                database_type="postgres",
                connection_ref="DATABASE_URL",
                status=DataSourceStatus.READY,
                is_default=True,
                certified_metric_count=certified,
            )
        ]
    summaries: list[DataSourceSummary] = []
    for source in await knowledge.data_sources.list():
        view = _data_source_view(source)
        view.certified_metric_count = len(
            await knowledge.registry.certified(source.id)
        )
        if knowledge.memory is not None:
            view.recurring_cluster_count = len(
                await knowledge.memory.clusters(source.id)
            )
        if knowledge.semantics is not None:
            model = await knowledge.semantics.load(source.id)
            view.confirmed_entity_count = len(model.confirmed_entities())
            view.proposed_entity_count = sum(
                1
                for entity in model.entities
                if entity.status is ApprovalStatus.PROPOSED
            )
        summaries.append(view)
    return summaries


@router.get("/data-sources/{data_source_id}/semantics")
async def list_semantics(
    data_source_id: UUID,
    _: Annotated[UserIdentity, Depends(require_knowledge_reviewer)],
    knowledge: Annotated[KnowledgeRuntime, Depends(get_knowledge_runtime)],
    status: ApprovalStatus | None = None,
) -> list[SemanticProposalView]:
    """Persisted semantic mappings for one datasource, in every review state.

    Reads the same rows EntityResolver reads, so a reviewer is always looking
    at runtime truth rather than a parallel copy.
    """
    if knowledge.semantics is None:
        return []
    model = await knowledge.semantics.load(data_source_id)
    entities = {entity.id: entity for entity in model.entities}
    views: list[SemanticProposalView] = []
    for entity in model.entities:
        views.append(
            SemanticProposalView(
                id=entity.id,
                kind="entity",
                physical=f"{entity.source_schema}.{entity.source_table}",
                proposed_concept=entity.entity_name,
                confidence=entity.confidence,
                status=entity.status,
                detail=entity.description or "",
            )
        )
    for attribute in model.attributes:
        owner = entities.get(attribute.entity_id)
        table = (
            f"{owner.source_schema}.{owner.source_table}"
            if owner is not None
            else "unknown"
        )
        views.append(
            SemanticProposalView(
                id=attribute.id,
                kind="attribute",
                physical=f"{table}.{attribute.source_column}",
                proposed_concept=attribute.concept_name,
                confidence=attribute.confidence,
                status=attribute.status,
                detail="canonical key" if attribute.is_identifier else "",
            )
        )
    for relationship in model.relationships:
        source = entities.get(relationship.from_entity_id)
        target = entities.get(relationship.to_entity_id)
        views.append(
            SemanticProposalView(
                id=relationship.id,
                kind="relationship",
                physical=(
                    f"{source.source_table if source else '?'}"
                    f".{relationship.from_column}"
                    f" -> {target.source_table if target else '?'}"
                    f".{relationship.to_column}"
                ),
                proposed_concept=relationship.relationship_name,
                confidence=relationship.confidence,
                status=relationship.status,
                detail=relationship.cardinality or "",
            )
        )
    if status is not None:
        views = [view for view in views if view.status is status]
    return views


@router.post("/data-sources/{data_source_id}/semantics/{proposal_id}/review")
async def review_semantic_proposal(
    data_source_id: UUID,
    proposal_id: UUID,
    decision: ReviewDecision,
    _: Annotated[UserIdentity, Depends(require_knowledge_reviewer)],
    knowledge: Annotated[KnowledgeRuntime, Depends(get_knowledge_runtime)],
) -> SemanticProposalView:
    """Approve, edit, or reject one mapping, persistently.

    Editing supplies a corrected concept name and approves in the same step:
    a reviewer correcting a name is confirming what it means, and forcing a
    second click would only invite approving without the correction.
    """
    from app.knowledge.discovery import SemanticReview, SemanticReviewError

    if knowledge.semantics is None:
        raise HTTPException(status_code=404, detail="No semantics to review.")

    model = await knowledge.semantics.load(data_source_id)
    review = SemanticReview()
    name = decision.concept_name
    kind = _kind_of(model, proposal_id)
    if kind is None:
        raise HTTPException(status_code=404, detail="No such proposal.")

    try:
        if decision.action == "reject":
            model = {
                "entity": review.reject_entity,
                "attribute": review.reject_attribute,
                "relationship": review.reject_relationship,
            }[kind](model, proposal_id)
        elif kind == "entity":
            model = review.approve_entity(model, proposal_id, entity_name=name)
        elif kind == "attribute":
            model = review.approve_attribute(model, proposal_id, concept_name=name)
        else:
            model = review.approve_relationship(
                model, proposal_id, relationship_name=name
            )
    except SemanticReviewError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    await knowledge.semantics.save(model)
    updated = await list_semantics(data_source_id, _, knowledge, None)
    for view in updated:
        if view.id == proposal_id:
            return view
    raise HTTPException(status_code=404, detail="No such proposal.")


def _kind_of(model: Any, proposal_id: UUID) -> str | None:
    if any(entity.id == proposal_id for entity in model.entities):
        return "entity"
    if any(attribute.id == proposal_id for attribute in model.attributes):
        return "attribute"
    if any(item.id == proposal_id for item in model.relationships):
        return "relationship"
    return None


@router.get("/data-sources/{data_source_id}/clusters")
async def list_clusters(
    data_source_id: UUID,
    _: Annotated[UserIdentity, Depends(require_knowledge_reviewer)],
    knowledge: Annotated[KnowledgeRuntime, Depends(get_knowledge_runtime)],
) -> list[ClusterView]:
    """Recurring question clusters. Summaries only, never raw question logs."""
    memory = knowledge.memory
    if memory is None:
        return []
    return [
        ClusterView(
            id=cluster.id,
            canonical_summary=cluster.canonical_summary,
            structural_fingerprint=cluster.structural_fingerprint,
            occurrence_count=cluster.occurrence_count,
            successful_count=cluster.successful_count,
            first_seen_at=cluster.first_seen_at.isoformat(),
            last_seen_at=cluster.last_seen_at.isoformat(),
            status=cluster.status,
        )
        for cluster in await memory.clusters(data_source_id)
    ]


@router.get("/data-sources/{data_source_id}/candidates")
async def list_candidates(
    data_source_id: UUID,
    _: Annotated[UserIdentity, Depends(require_knowledge_reviewer)],
    knowledge: Annotated[KnowledgeRuntime, Depends(get_knowledge_runtime)],
) -> list[CandidateView]:
    store = knowledge.candidates
    if store is None:
        return []
    return [_candidate_view(candidate) for candidate in await store.list(data_source_id)]


@router.post("/data-sources/{data_source_id}/candidates/{candidate_id}/review")
async def review_candidate(
    data_source_id: UUID,
    candidate_id: UUID,
    decision: ReviewDecision,
    identity: Annotated[UserIdentity, Depends(require_knowledge_reviewer)],
    knowledge: Annotated[KnowledgeRuntime, Depends(get_knowledge_runtime)],
) -> CandidateView:
    """Approve or reject a candidate. Approval runs full validation."""
    from app.knowledge.candidates import CandidateReview, CandidateReviewError

    store = knowledge.candidates
    registry = knowledge.registry
    if store is None or registry is None:
        raise HTTPException(status_code=404, detail="No candidate to review.")

    review = CandidateReview(
        store=store,
        registry=registry,
        guidance=knowledge.guidance,
        learned=knowledge.learned,
    )
    try:
        if decision.action == "reject":
            candidate = await review.reject(
                data_source_id,
                candidate_id,
                reason=decision.reason or "Rejected by reviewer.",
                reviewed_by=identity.subject_id,
            )
            return _candidate_view(candidate)
        candidate = await store.by_id(data_source_id, candidate_id)
        if candidate is None:
            raise CandidateReviewError("No such candidate in this datasource.")
        if candidate.candidate_type.value == "BUSINESS_RULE":
            await review.approve_business_rule(
                data_source_id, candidate_id, reviewed_by=identity.subject_id
            )
        elif candidate.candidate_type.value in _LEARNED_TYPES:
            # Filters, synonyms, aliases, join rules and descriptions all name
            # business concepts, so each is checked against what this datasource
            # has actually confirmed before it becomes knowledge.
            semantics = knowledge.semantics
            model = (
                await semantics.load(data_source_id) if semantics is not None else None
            )
            await review.approve_learned(
                data_source_id,
                candidate_id,
                semantic_model=model,
                reviewed_by=identity.subject_id,
            )
        elif candidate.candidate_type.value == "QUERY_EXAMPLE":
            # Approving an example is a claim it still works, so it is checked
            # against the schema this reviewer is authorized for right now --
            # not the one it ran against.
            validator, tables, fingerprint = await _current_schema_scope(
                knowledge, data_source_id
            )
            await review.approve_query_example(
                data_source_id,
                candidate_id,
                validator=validator,
                authorized_tables=tables,
                current_schema_fingerprint=fingerprint,
                reviewed_by=identity.subject_id,
            )
        else:
            await review.approve_metric(
                data_source_id, candidate_id, reviewed_by=identity.subject_id
            )
    except CandidateReviewError as exc:
        # The reason a promotion was refused is reviewer-facing and safe: it
        # names metric keys and dimensions the reviewer is already looking at.
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    candidate = await store.by_id(data_source_id, candidate_id)
    if candidate is None:  # pragma: no cover - just written
        raise HTTPException(status_code=404, detail="No candidate to review.")
    return _candidate_view(candidate)


@router.get("/data-sources/{data_source_id}/metrics")
async def list_certified_metrics(
    data_source_id: UUID,
    _: Annotated[UserIdentity, Depends(require_knowledge_reviewer)],
    knowledge: Annotated[KnowledgeRuntime, Depends(get_knowledge_runtime)],
) -> list[CertifiedMetricView]:
    registry = knowledge.registry
    if registry is None:
        return []
    return [
        CertifiedMetricView(
            metric_key=metric.metric_key,
            display_name=metric.display_name,
            description=metric.description,
            business_meaning=metric.business_meaning,
            version=metric.version,
            status=metric.status.value,
            grain=metric.grain,
            unit=metric.unit,
            dimensions=[spec.dimension_key for spec in metric.dimensions],
            dependencies=list(metric.dependencies),
            semantic_expression=metric.semantic_expression,
            approved_at=metric.approved_at.isoformat() if metric.approved_at else None,
            approved_by=metric.approved_by,
        )
        for metric in await registry.certified(data_source_id)
    ]


@router.get("/data-sources/{data_source_id}/examples")
async def list_query_examples(
    data_source_id: UUID,
    _: Annotated[UserIdentity, Depends(require_knowledge_reviewer)],
    knowledge: Annotated[KnowledgeRuntime, Depends(get_knowledge_runtime)],
) -> list[QueryExampleView]:
    guidance = knowledge.guidance
    if guidance is None:
        return []
    return [
        QueryExampleView(
            id=example.id,
            question=example.question,
            semantic_plan=example.semantic_plan,
            status=example.status,
            schema_fingerprint=example.schema_fingerprint,
            approved_at=example.approved_at.isoformat(),
        )
        for example in await guidance.examples(data_source_id)
    ]


@router.get("/data-sources/{data_source_id}/instructions")
async def list_business_instructions(
    data_source_id: UUID,
    _: Annotated[UserIdentity, Depends(require_knowledge_reviewer)],
    knowledge: Annotated[KnowledgeRuntime, Depends(get_knowledge_runtime)],
) -> list[BusinessInstructionView]:
    guidance = knowledge.guidance
    if guidance is None:
        return []
    return [
        _instruction_view(instruction)
        for instruction in await guidance.instructions(data_source_id)
    ]


@router.post("/data-sources/{data_source_id}/instructions", status_code=201)
async def author_business_instruction(
    data_source_id: UUID,
    payload: AuthorBusinessInstruction,
    identity: Annotated[UserIdentity, Depends(require_knowledge_reviewer)],
    knowledge: Annotated[KnowledgeRuntime, Depends(get_knowledge_runtime)],
) -> BusinessInstructionView:
    """Record reviewed business meaning for one datasource.

    Approval is the act of writing it: this route already requires review
    authority, which is the same bar the candidate queue applies before a
    proposal becomes guidance.
    """
    del identity
    guidance = knowledge.guidance
    if guidance is None:
        raise HTTPException(status_code=404, detail="No datasource to annotate.")
    sources = knowledge.data_sources
    if sources is not None and await sources.get(data_source_id) is None:
        raise HTTPException(status_code=404, detail="No datasource to annotate.")

    from app.knowledge.guidance import BusinessInstruction

    stored = await guidance.approve_instruction(
        BusinessInstruction(
            data_source_id=data_source_id,
            title=payload.title.strip(),
            instruction=payload.instruction.strip(),
            semantic_concepts=tuple(
                concept.strip() for concept in payload.semantic_concepts if concept.strip()
            ),
            metric_keys=tuple(key.strip() for key in payload.metric_keys if key.strip()),
        )
    )
    return _instruction_view(stored)


def _candidate_detail(proposal: Any) -> list[CandidateDetail]:
    """What a reviewer has to see to make a decision about this kind.

    A filter is meaningless without its predicate, a join rule without both
    sides, a description change without the wording it replaces. Approving on a
    name alone is not review.
    """
    from app.knowledge.candidates import BusinessRuleProposal, QueryExampleProposal
    from app.knowledge.learned import (
        DescriptionProposal,
        EntityAliasProposal,
        FilterProposal,
        JoinRuleProposal,
        SynonymProposal,
        describe_predicate,
    )

    if isinstance(proposal, FilterProposal):
        return [
            CandidateDetail(label="Population", value=describe_predicate(proposal.predicate))
        ]
    if isinstance(proposal, SynonymProposal):
        return [
            CandidateDetail(
                label="Points at", value=f"{proposal.target_kind}: {proposal.target}"
            ),
            CandidateDetail(label="Phrases", value=", ".join(proposal.phrases)),
        ]
    if isinstance(proposal, EntityAliasProposal):
        detail = [
            CandidateDetail(label="Entity", value=proposal.entity_name),
            CandidateDetail(label="Alias", value=proposal.alias),
        ]
        if proposal.canonical_key:
            detail.append(
                CandidateDetail(label="Canonical key", value=proposal.canonical_key)
            )
        return detail
    if isinstance(proposal, JoinRuleProposal):
        return [
            CandidateDetail(label="Left", value=proposal.left_concept),
            CandidateDetail(label="Right", value=proposal.right_concept),
            CandidateDetail(label="Cardinality", value=proposal.cardinality),
        ]
    if isinstance(proposal, DescriptionProposal):
        return [
            CandidateDetail(
                label="Subject", value=f"{proposal.subject_kind}: {proposal.subject}"
            ),
            CandidateDetail(label="Proposed description", value=proposal.description),
        ]
    if isinstance(proposal, QueryExampleProposal):
        return [CandidateDetail(label="Question", value=proposal.question)]
    if isinstance(proposal, BusinessRuleProposal):
        return [CandidateDetail(label="Instruction", value=proposal.instruction)]
    return []


def _instruction_view(instruction: Any) -> BusinessInstructionView:
    return BusinessInstructionView(
        id=instruction.id,
        title=instruction.title,
        instruction=instruction.instruction,
        semantic_concepts=list(instruction.semantic_concepts),
        metric_keys=list(instruction.metric_keys),
        status=instruction.status,
        schema_fingerprint=instruction.schema_fingerprint,
        source_candidate_id=instruction.source_candidate_id,
        approved_at=(
            instruction.approved_at.isoformat() if instruction.approved_at else None
        ),
    )


async def _current_schema_scope(
    knowledge: KnowledgeRuntime, data_source_id: UUID
) -> tuple[SQLValidator, list[TableMetadata], str | None]:
    """The datasource's schema as it is now, for re-validating a statement.

    Reads schema metadata only. Nothing here executes anything, and the
    statement being reviewed is never run: what is being asked is whether it
    would still pass the checks a live request has to pass.
    """
    from app.knowledge.execution import DataSourceUnavailableError

    if knowledge.execution is None:
        raise HTTPException(
            status_code=409,
            detail="This deployment cannot verify the datasource's schema.",
        )
    try:
        context = await knowledge.execution.context_for(data_source_id)
    except DataSourceUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail="The selected data source is unavailable.",
        ) from exc
    tables = await context.gateway.search_schema("")
    validator = SQLValidator(
        max_rows=get_settings().query_row_limit,
        allowed_schemas=frozenset(context.allowed_schemas),
    )
    return validator, tables, context.data_source.schema_fingerprint


def _candidate_view(candidate: Any) -> CandidateView:
    from app.knowledge.candidates import MetricProposal
    from app.knowledge.expressions import describe, referenced_metrics

    proposal = candidate.proposal
    expression = None
    dependencies: list[str] = []
    grain = None
    if isinstance(proposal, MetricProposal):
        expression = describe(proposal.expression)
        dependencies = sorted(referenced_metrics(proposal.expression))
        grain = proposal.grain
    return CandidateView(
        detail=_candidate_detail(proposal),
        id=candidate.id,
        candidate_type=candidate.candidate_type.value,
        display_name=candidate.display_name,
        description=candidate.description,
        status=candidate.status,
        evidence_count=candidate.evidence_count,
        successful_evidence_count=candidate.successful_evidence_count,
        expression=expression,
        grain=grain,
        dependencies=dependencies,
        rejection_reason=candidate.rejection_reason,
    )
