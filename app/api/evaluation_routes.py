"""Evaluation sets: known-answer questions, and the runs that check them.

Everything here is gated on review authority, like the rest of the knowledge
surface: an evaluation set is an assertion about what the right answer is, which
is a governance decision rather than an analytics one.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.knowledge_routes import require_knowledge_reviewer
from app.api.routes import get_knowledge_runtime
from app.authentication.gateway import UserIdentity
from app.config import Settings, get_settings
from app.knowledge.evaluation import (
    CaseStatus,
    EvaluationCase,
    EvaluationError,
    EvaluationRun,
    ExpectationKind,
    Movement,
    movements,
    validate_expected,
)
from app.knowledge.evaluation_runner import EvaluationRunner
from app.knowledge.runtime import KnowledgeRuntime
from app.knowledge.seed import DEFAULT_DATA_SOURCE_ID
from app.llm.profiles import DEFAULT_MODEL_PROFILE

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge", tags=["evaluation"])


class StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvaluationCaseView(StrictPayload):
    id: UUID
    name: str
    question: str
    expectation: ExpectationKind
    expected: dict[str, Any]
    tolerance: str
    ordered: bool
    expected_route: str | None = None
    expected_metric_ids: list[str] = Field(default_factory=list)
    status: CaseStatus
    created_by: str | None = None
    created_at: str
    updated_at: str


class SaveEvaluationCase(StrictPayload):
    """A reviewer's statement of what the right answer is.

    The expected value is always supplied explicitly, even when the UI
    pre-filled it from a successful run: a benchmark that records whatever the
    system happened to answer only asserts that nothing changed, not that
    anything is right.
    """

    name: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=2000)
    expectation: ExpectationKind
    expected: dict[str, Any] = Field(default_factory=dict)
    tolerance: str = "0"
    ordered: bool = False
    expected_route: str | None = Field(default=None, max_length=64)
    expected_metric_ids: list[str] = Field(default_factory=list, max_length=20)
    status: CaseStatus = CaseStatus.ACTIVE


class CaseResultView(StrictPayload):
    case_id: UUID
    name: str
    question: str
    expected: str
    outcome: str
    movement: Movement
    actual: str | None = None
    detail: str | None = None
    route: str | None = None
    latency_ms: float


class EvaluationRunView(StrictPayload):
    id: UUID
    data_source_id: UUID
    model_profile: str
    started_at: str
    finished_at: str | None = None
    case_count: int
    passed: int
    failed: int
    errored: int
    pass_rate: float
    average_latency_ms: float
    regressions: int
    improvements: int
    configuration: dict[str, Any] = Field(default_factory=dict)
    results: list[CaseResultView] = Field(default_factory=list)


def _require_store(knowledge: KnowledgeRuntime) -> Any:
    store = knowledge.evaluations
    if store is None:
        raise HTTPException(
            status_code=409,
            detail="This deployment has no persistent evaluation storage.",
        )
    return store


@router.get("/data-sources/{data_source_id}/evaluation-cases")
async def list_evaluation_cases(
    data_source_id: UUID,
    _: Annotated[UserIdentity, Depends(require_knowledge_reviewer)],
    knowledge: Annotated[KnowledgeRuntime, Depends(get_knowledge_runtime)],
    include_archived: bool = False,
) -> list[EvaluationCaseView]:
    store = knowledge.evaluations
    if store is None:
        return []
    return [
        _case_view(case)
        for case in await store.cases(
            data_source_id, include_archived=include_archived
        )
    ]


@router.post("/data-sources/{data_source_id}/evaluation-cases", status_code=201)
async def create_evaluation_case(
    data_source_id: UUID,
    payload: SaveEvaluationCase,
    identity: Annotated[UserIdentity, Depends(require_knowledge_reviewer)],
    knowledge: Annotated[KnowledgeRuntime, Depends(get_knowledge_runtime)],
) -> EvaluationCaseView:
    store = _require_store(knowledge)
    return _case_view(
        await store.upsert_case(
            _build_case(data_source_id, payload, created_by=identity.subject_id)
        )
    )


@router.put("/data-sources/{data_source_id}/evaluation-cases/{case_id}")
async def update_evaluation_case(
    data_source_id: UUID,
    case_id: UUID,
    payload: SaveEvaluationCase,
    identity: Annotated[UserIdentity, Depends(require_knowledge_reviewer)],
    knowledge: Annotated[KnowledgeRuntime, Depends(get_knowledge_runtime)],
) -> EvaluationCaseView:
    store = _require_store(knowledge)
    existing = await store.case(data_source_id, case_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="No such evaluation case.")
    updated = _build_case(
        data_source_id,
        payload,
        case_id=case_id,
        created_by=existing.created_by or identity.subject_id,
        created_at=existing.created_at,
    )
    return _case_view(await store.upsert_case(updated))


@router.post("/data-sources/{data_source_id}/evaluation-runs", status_code=201)
async def start_evaluation_run(
    data_source_id: UUID,
    identity: Annotated[UserIdentity, Depends(require_knowledge_reviewer)],
    knowledge: Annotated[KnowledgeRuntime, Depends(get_knowledge_runtime)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> EvaluationRunView:
    """Run the set now. Never on startup: every case costs a model call."""
    from app.api.analysis_client import InProcessAnalysisRunner

    store = _require_store(knowledge)
    profile = settings.resolve_model_profile(DEFAULT_MODEL_PROFILE)
    source = (
        await knowledge.data_sources.get(data_source_id)
        if knowledge.data_sources is not None
        else None
    )
    runner = EvaluationRunner(
        store=store, analysis=InProcessAnalysisRunner(settings=settings)
    )
    run = await runner.run(
        data_source_id,
        model_profile=profile.profile,
        triggered_by=identity.subject_id,
        configuration={
            "model_profile": profile.profile,
            # Which shape of the database the answers were produced against, so
            # two runs are comparable. Never anything about the connection.
            "schema_fingerprint": getattr(source, "schema_fingerprint", None),
        },
    )
    previous = await _previous_run(store, data_source_id, run.id)
    return await _run_view(store, data_source_id, run, previous)


@router.get("/data-sources/{data_source_id}/evaluation-runs")
async def list_evaluation_runs(
    data_source_id: UUID,
    _: Annotated[UserIdentity, Depends(require_knowledge_reviewer)],
    knowledge: Annotated[KnowledgeRuntime, Depends(get_knowledge_runtime)],
    limit: int = 10,
) -> list[EvaluationRunView]:
    store = knowledge.evaluations
    if store is None:
        return []
    runs = await store.runs(data_source_id, limit=max(1, min(limit, 50)))
    views: list[EvaluationRunView] = []
    for index, run in enumerate(runs):
        previous = runs[index + 1] if index + 1 < len(runs) else None
        views.append(await _run_view(store, data_source_id, run, previous))
    return views


def _build_case(
    data_source_id: UUID,
    payload: SaveEvaluationCase,
    *,
    case_id: UUID | None = None,
    created_by: str | None = None,
    created_at: Any = None,
) -> EvaluationCase:
    try:
        expected = validate_expected(payload.expectation, payload.expected)
        tolerance = Decimal(payload.tolerance)
    except (EvaluationError, ArithmeticError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if tolerance < 0:
        raise HTTPException(status_code=422, detail="Tolerance cannot be negative.")
    fields: dict[str, Any] = {
        "data_source_id": data_source_id,
        "name": payload.name.strip(),
        "question": payload.question.strip(),
        "expectation": payload.expectation,
        "expected": expected,
        "tolerance": tolerance,
        "ordered": payload.ordered,
        "expected_route": payload.expected_route,
        "expected_metric_ids": tuple(payload.expected_metric_ids),
        "status": payload.status,
        "created_by": created_by,
    }
    if case_id is not None:
        fields["id"] = case_id
    if created_at is not None:
        fields["created_at"] = created_at
    return EvaluationCase(**fields)


async def _previous_run(
    store: Any, data_source_id: UUID, current_id: UUID
) -> EvaluationRun | None:
    recent = await store.runs(data_source_id, limit=5)
    return next((run for run in recent if run.id != current_id), None)


async def _run_view(
    store: Any,
    data_source_id: UUID,
    run: EvaluationRun,
    previous: EvaluationRun | None,
) -> EvaluationRunView:
    moved = movements(run, previous)
    cases = {
        case.id: case
        for case in await store.cases(data_source_id, include_archived=True)
    }
    results = [
        CaseResultView(
            case_id=result.case_id,
            name=cases[result.case_id].name
            if result.case_id in cases
            else "(deleted case)",
            question=cases[result.case_id].question
            if result.case_id in cases
            else "",
            expected=_expected_summary(cases.get(result.case_id)),
            outcome=result.outcome.value,
            movement=moved.get(result.case_id, Movement.NEW),
            actual=result.actual,
            detail=result.detail,
            route=result.route,
            latency_ms=result.latency_ms,
        )
        for result in run.results
    ]
    return EvaluationRunView(
        id=run.id,
        data_source_id=run.data_source_id,
        model_profile=run.model_profile,
        started_at=run.started_at.isoformat(),
        finished_at=run.finished_at.isoformat() if run.finished_at else None,
        case_count=run.case_count,
        passed=run.passed,
        failed=run.failed,
        errored=run.errored,
        pass_rate=round(run.pass_rate, 4),
        average_latency_ms=round(run.average_latency_ms, 1),
        regressions=sum(
            1 for item in results if item.movement is Movement.REGRESSION
        ),
        improvements=sum(1 for item in results if item.movement is Movement.IMPROVED),
        configuration=run.configuration,
        results=sorted(results, key=lambda item: (item.movement.value, item.name)),
    )


def _expected_summary(case: EvaluationCase | None) -> str:
    if case is None:
        return ""
    if case.expectation is ExpectationKind.EMPTY:
        return "(no rows)"
    if case.expectation in {ExpectationKind.SCALAR, ExpectationKind.ROW_COUNT}:
        return str(case.expected.get("value", ""))
    rows = case.expected.get("rows") or []
    return f"{len(rows)} rows"


def _case_view(case: EvaluationCase) -> EvaluationCaseView:
    return EvaluationCaseView(
        id=case.id,
        name=case.name,
        question=case.question,
        expectation=case.expectation,
        expected=case.expected,
        tolerance=str(case.tolerance),
        ordered=case.ordered,
        expected_route=case.expected_route,
        expected_metric_ids=list(case.expected_metric_ids),
        status=case.status,
        created_by=case.created_by,
        created_at=case.created_at.isoformat(),
        updated_at=case.updated_at.isoformat(),
    )


__all__ = ["DEFAULT_DATA_SOURCE_ID", "router"]
