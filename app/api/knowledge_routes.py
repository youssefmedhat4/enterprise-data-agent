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
from app.data.gateway import DatabaseGateway
from app.knowledge.candidates import CandidateStatus
from app.knowledge.contracts import ApprovalStatus, DataSourceStatus
from app.knowledge.runtime import KnowledgeRuntime
from app.knowledge.seed import DEFAULT_DATA_SOURCE_ID

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


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


@router.get("/data-sources", response_model=list[DataSourceSummary])
async def list_data_sources(
    _: Annotated[UserIdentity, Depends(require_knowledge_reviewer)],
    knowledge: Annotated[KnowledgeRuntime, Depends(get_knowledge_runtime)],
) -> list[DataSourceSummary]:
    """The registered datasources this deployment can answer from."""
    registry = knowledge.registry
    certified = 0
    if registry is not None:
        certified = len(await registry.certified(DEFAULT_DATA_SOURCE_ID))
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


@router.get("/data-sources/{data_source_id}/semantics")
async def list_semantics(
    data_source_id: UUID,
    _: Annotated[UserIdentity, Depends(require_knowledge_reviewer)],
    knowledge: Annotated[KnowledgeRuntime, Depends(get_knowledge_runtime)],
    status: ApprovalStatus | None = None,
) -> list[SemanticProposalView]:
    """Semantic proposals and confirmed mappings for one datasource."""
    del data_source_id, status
    return []


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

    review = CandidateReview(store=store, registry=registry)
    try:
        if decision.action == "reject":
            candidate = await review.reject(
                data_source_id,
                candidate_id,
                reason=decision.reason or "Rejected by reviewer.",
                reviewed_by=identity.subject_id,
            )
            return _candidate_view(candidate)
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
