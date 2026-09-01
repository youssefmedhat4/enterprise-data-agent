"""Configuring one datasource's calendar and its temporal columns.

Structured configuration only. An administrator picks a timezone, a week start
and a fiscal start; they never write `DATE_TRUNC` or `TO_DATE`, because a
calendar expressed as SQL is a calendar nobody can review and nothing else can
reason about.

A preview endpoint resolves a phrase without answering a question, so a reviewer
can see that "fiscal YTD" means July to September here before trusting an answer
that depends on it. It costs no model call.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.knowledge_routes import require_knowledge_reviewer
from app.api.routes import get_knowledge_runtime
from app.authentication.gateway import UserIdentity
from app.knowledge.contracts import ApprovalStatus
from app.knowledge.runtime import KnowledgeRuntime
from app.timeintel.clock import SystemClock
from app.timeintel.dimensions import (
    TemporalDimension,
    TemporalRole,
    TemporalStorage,
)
from app.timeintel.parser import parse_time_phrase
from app.timeintel.policy import (
    FiscalYearLabel,
    PolicyStatus,
    TimePolicy,
    TimePolicyError,
    WeekStart,
)
from app.timeintel.resolver import TimeResolutionError, resolve

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge", tags=["time"])


class StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimePolicyView(StrictPayload):
    timezone: str
    week_start: WeekStart
    fiscal_year_start_month: int
    fiscal_year_start_day: int
    fiscal_year_label: FiscalYearLabel
    status: PolicyStatus
    version: int
    updated_by: str | None = None
    updated_at: str


class SaveTimePolicy(StrictPayload):
    timezone: str = Field(min_length=1, max_length=64)
    week_start: WeekStart = WeekStart.MONDAY
    fiscal_year_start_month: int = Field(default=1, ge=1, le=12)
    #: Capped at 28 so the fiscal year start exists in every month.
    fiscal_year_start_day: int = Field(default=1, ge=1, le=28)
    fiscal_year_label: FiscalYearLabel = FiscalYearLabel.START_YEAR
    #: Saving through this route is the act of confirming: a reviewer stating
    #: the calendar is what makes fiscal questions answerable.
    status: PolicyStatus = PolicyStatus.CONFIRMED


class TemporalDimensionView(StrictPayload):
    id: UUID
    semantic_attribute_id: UUID
    entity: str
    concept: str
    table: str
    column: str
    role: TemporalRole
    storage: TemporalStorage
    is_default_for_entity: bool
    status: ApprovalStatus


class SaveTemporalDimension(StrictPayload):
    semantic_attribute_id: UUID
    role: TemporalRole
    storage: TemporalStorage
    is_default_for_entity: bool = False
    #: Review is what makes a mapping usable; proposing one leaves it PROPOSED.
    status: ApprovalStatus = ApprovalStatus.CONFIRMED


class TimePreview(StrictPayload):
    phrase: str = Field(min_length=1, max_length=200)


class TimePreviewView(StrictPayload):
    recognised: bool
    label: str = ""
    timezone: str = ""
    start: str = ""
    end: str = ""
    comparison_label: str = ""
    comparison_start: str | None = None
    comparison_end: str | None = None
    detail: str = ""


def _require(knowledge: KnowledgeRuntime) -> Any:
    store = knowledge.time_intelligence
    if store is None:
        raise HTTPException(
            status_code=409,
            detail="This deployment has no persistent time intelligence storage.",
        )
    return store


@router.get("/data-sources/{data_source_id}/time-policy")
async def read_time_policy(
    data_source_id: UUID,
    _: Annotated[UserIdentity, Depends(require_knowledge_reviewer)],
    knowledge: Annotated[KnowledgeRuntime, Depends(get_knowledge_runtime)],
) -> TimePolicyView:
    store = _require(knowledge)
    return _policy_view(await store.policy(data_source_id))


@router.put("/data-sources/{data_source_id}/time-policy")
async def save_time_policy(
    data_source_id: UUID,
    payload: SaveTimePolicy,
    identity: Annotated[UserIdentity, Depends(require_knowledge_reviewer)],
    knowledge: Annotated[KnowledgeRuntime, Depends(get_knowledge_runtime)],
) -> TimePolicyView:
    store = _require(knowledge)
    try:
        policy = TimePolicy(
            data_source_id=data_source_id,
            timezone=payload.timezone,
            week_start=payload.week_start,
            fiscal_year_start_month=payload.fiscal_year_start_month,
            fiscal_year_start_day=payload.fiscal_year_start_day,
            fiscal_year_label=payload.fiscal_year_label,
            status=payload.status,
            updated_by=identity.subject_id,
        )
    except TimePolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _policy_view(await store.save_policy(policy))


@router.get("/data-sources/{data_source_id}/temporal-dimensions")
async def list_temporal_dimensions(
    data_source_id: UUID,
    _: Annotated[UserIdentity, Depends(require_knowledge_reviewer)],
    knowledge: Annotated[KnowledgeRuntime, Depends(get_knowledge_runtime)],
) -> list[TemporalDimensionView]:
    store = knowledge.time_intelligence
    if store is None:
        return []
    return [_dimension_view(item) for item in await store.dimensions(data_source_id)]


@router.put("/data-sources/{data_source_id}/temporal-dimensions")
async def save_temporal_dimension(
    data_source_id: UUID,
    payload: SaveTemporalDimension,
    identity: Annotated[UserIdentity, Depends(require_knowledge_reviewer)],
    knowledge: Annotated[KnowledgeRuntime, Depends(get_knowledge_runtime)],
) -> TemporalDimensionView:
    """Record what one confirmed attribute means in time.

    The attribute must already be confirmed semantics: a temporal role on a
    column nobody reviewed would be a mapping onto a guess.
    """
    store = _require(knowledge)
    semantics = knowledge.semantics
    if semantics is None:
        raise HTTPException(
            status_code=409, detail="This datasource has no semantic model."
        )
    model = await semantics.load(data_source_id)
    attribute = next(
        (
            item
            for item in model.confirmed_attributes()
            if item.id == payload.semantic_attribute_id
        ),
        None,
    )
    if attribute is None:
        raise HTTPException(
            status_code=422,
            detail="That attribute is not confirmed for this datasource.",
        )
    stored = await store.save_dimension(
        TemporalDimension(
            data_source_id=data_source_id,
            semantic_attribute_id=payload.semantic_attribute_id,
            role=payload.role,
            storage=payload.storage,
            is_default_for_entity=payload.is_default_for_entity,
            status=payload.status,
            schema_fingerprint=model.schema_fingerprint,
            reviewed_by=identity.subject_id,
        )
    )
    return _dimension_view(stored)


@router.post("/data-sources/{data_source_id}/time-preview")
async def preview_time_phrase(
    data_source_id: UUID,
    payload: TimePreview,
    _: Annotated[UserIdentity, Depends(require_knowledge_reviewer)],
    knowledge: Annotated[KnowledgeRuntime, Depends(get_knowledge_runtime)],
) -> TimePreviewView:
    """Show what a phrase means here, before an answer depends on it."""
    store = _require(knowledge)
    intent = parse_time_phrase(payload.phrase)
    if intent is None or not intent.is_temporal:
        return TimePreviewView(
            recognised=False,
            detail="That phrase names no period this system recognises.",
        )
    policy = await store.policy(data_source_id)
    try:
        plan = resolve(intent, policy, clock=SystemClock())
    except TimeResolutionError as exc:
        return TimePreviewView(recognised=True, detail=str(exc))
    return TimePreviewView(
        recognised=True,
        label=plan.label,
        timezone=plan.timezone,
        start=plan.primary.start.isoformat(),
        end=plan.primary.end.isoformat(),
        comparison_label=plan.comparison_label,
        comparison_start=plan.comparison.start.isoformat() if plan.comparison else None,
        comparison_end=plan.comparison.end.isoformat() if plan.comparison else None,
        detail=plan.describe(),
    )


def _policy_view(policy: TimePolicy) -> TimePolicyView:
    return TimePolicyView(
        timezone=policy.timezone,
        week_start=policy.week_start,
        fiscal_year_start_month=policy.fiscal_year_start_month,
        fiscal_year_start_day=policy.fiscal_year_start_day,
        fiscal_year_label=policy.fiscal_year_label,
        status=policy.status,
        version=policy.version,
        updated_by=policy.updated_by,
        updated_at=policy.updated_at.isoformat(),
    )


def _dimension_view(dimension: TemporalDimension) -> TemporalDimensionView:
    return TemporalDimensionView(
        id=dimension.id,
        semantic_attribute_id=dimension.semantic_attribute_id,
        entity=dimension.entity_name,
        concept=dimension.concept_name,
        table=dimension.table_identifier,
        column=dimension.column_name,
        role=dimension.role,
        storage=dimension.storage,
        is_default_for_entity=dimension.is_default_for_entity,
        status=dimension.status,
    )
