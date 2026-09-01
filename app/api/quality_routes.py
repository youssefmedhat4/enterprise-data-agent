"""Data quality assertions: what a reviewer configured, and what it found.

Checks run against the datasource they describe, through the same
`DataSourceRuntimeProvider` and read-only role an analytical query uses. Nothing
here exposes a connection: an assertion names a table and a column, never a host,
a role, or a reference value.
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
from app.config import Settings, get_settings
from app.knowledge.quality import (
    AssertionType,
    QualityAssertion,
    QualityError,
    QualityStatus,
    validate_configuration,
)
from app.knowledge.quality_runner import QualityRunner
from app.knowledge.runtime import KnowledgeRuntime
from app.security.sql_validation import SQLValidator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge", tags=["quality"])


class StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QualityAssertionView(StrictPayload):
    id: UUID
    name: str
    assertion_type: AssertionType
    table: str
    column_name: str | None = None
    configuration: dict[str, Any] = Field(default_factory=dict)
    enabled: bool
    status: QualityStatus = QualityStatus.UNKNOWN
    observed: float | None = None
    detail: str | None = None
    checked_at: str | None = None


class SaveQualityAssertion(StrictPayload):
    name: str = Field(min_length=1, max_length=200)
    assertion_type: AssertionType
    schema_name: str = Field(min_length=1, max_length=128)
    table_name: str = Field(min_length=1, max_length=128)
    column_name: str | None = Field(default=None, max_length=128)
    configuration: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


def _require(knowledge: KnowledgeRuntime) -> Any:
    store = knowledge.quality
    if store is None:
        raise HTTPException(
            status_code=409,
            detail="This deployment has no persistent quality storage.",
        )
    return store


@router.get("/data-sources/{data_source_id}/quality")
async def list_quality_assertions(
    data_source_id: UUID,
    _: Annotated[UserIdentity, Depends(require_knowledge_reviewer)],
    knowledge: Annotated[KnowledgeRuntime, Depends(get_knowledge_runtime)],
) -> list[QualityAssertionView]:
    store = knowledge.quality
    if store is None:
        return []
    assertions = await store.assertions(data_source_id)
    latest = await store.latest(data_source_id)
    return [_view(assertion, latest.get(assertion.id)) for assertion in assertions]


@router.post("/data-sources/{data_source_id}/quality", status_code=201)
async def create_quality_assertion(
    data_source_id: UUID,
    payload: SaveQualityAssertion,
    identity: Annotated[UserIdentity, Depends(require_knowledge_reviewer)],
    knowledge: Annotated[KnowledgeRuntime, Depends(get_knowledge_runtime)],
) -> QualityAssertionView:
    store = _require(knowledge)
    try:
        configuration = validate_configuration(
            payload.assertion_type, payload.column_name, payload.configuration
        )
    except QualityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    assertion = QualityAssertion(
        data_source_id=data_source_id,
        name=payload.name.strip(),
        assertion_type=payload.assertion_type,
        schema_name=payload.schema_name.strip(),
        table_name=payload.table_name.strip(),
        column_name=payload.column_name,
        configuration=configuration,
        enabled=payload.enabled,
        created_by=identity.subject_id,
    )
    return _view(await store.upsert(assertion), None)


@router.post("/data-sources/{data_source_id}/quality/{assertion_id}/toggle")
async def toggle_quality_assertion(
    data_source_id: UUID,
    assertion_id: UUID,
    _: Annotated[UserIdentity, Depends(require_knowledge_reviewer)],
    knowledge: Annotated[KnowledgeRuntime, Depends(get_knowledge_runtime)],
) -> QualityAssertionView:
    from dataclasses import replace as dataclass_replace

    store = _require(knowledge)
    existing = await store.assertion(data_source_id, assertion_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="No such assertion.")
    updated = await store.upsert(
        dataclass_replace(existing, enabled=not existing.enabled)
    )
    latest = await store.latest(data_source_id)
    return _view(updated, latest.get(updated.id))


@router.post("/data-sources/{data_source_id}/quality/run", status_code=201)
async def run_quality_checks(
    data_source_id: UUID,
    _: Annotated[UserIdentity, Depends(require_knowledge_reviewer)],
    knowledge: Annotated[KnowledgeRuntime, Depends(get_knowledge_runtime)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[QualityAssertionView]:
    """Run every enabled assertion now, against the selected datasource."""
    from app.knowledge.execution import DataSourceUnavailableError

    store = _require(knowledge)
    if knowledge.execution is None:
        raise HTTPException(
            status_code=409, detail="This deployment cannot reach the datasource."
        )
    try:
        context = await knowledge.execution.context_for(data_source_id)
    except DataSourceUnavailableError as exc:
        raise HTTPException(
            status_code=503, detail="The selected data source is unavailable."
        ) from exc
    tables = await context.gateway.search_schema("")
    runner = QualityRunner(
        store=store,
        gateway=context.gateway,
        validator=SQLValidator(
            max_rows=settings.query_row_limit,
            allowed_schemas=frozenset(context.allowed_schemas),
        ),
        authorized_tables=tables,
    )
    await runner.run_all(data_source_id)
    assertions = await store.assertions(data_source_id)
    latest = await store.latest(data_source_id)
    return [_view(assertion, latest.get(assertion.id)) for assertion in assertions]


def _view(assertion: QualityAssertion, result: Any) -> QualityAssertionView:
    return QualityAssertionView(
        id=assertion.id,
        name=assertion.name,
        assertion_type=assertion.assertion_type,
        table=assertion.table_identifier,
        column_name=assertion.column_name,
        # Configuration is a reviewer's own thresholds; a custom statement is
        # withheld for the same reason approved example SQL is.
        configuration={
            key: value
            for key, value in assertion.configuration.items()
            if key != "sql"
        },
        enabled=assertion.enabled,
        status=result.status if result is not None else QualityStatus.UNKNOWN,
        observed=result.observed if result is not None else None,
        detail=result.detail if result is not None else None,
        checked_at=result.checked_at.isoformat() if result is not None else None,
    )
