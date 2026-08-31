from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.data.gateway import (
    ColumnMetadata,
    DatabaseExecutionMetadata,
    DatabaseGateway,
    DatabaseQueryResult,
    DatabaseSource,
    ResultColumnMetadata,
    TableMetadata,
)
from app.knowledge.contracts import ApprovalStatus, SemanticAttribute, SemanticEntity
from app.knowledge.discovery import SemanticModel
from app.semantic.entity_values import DatabaseEntityValueGateway, entity_lookup_bindings

SOURCE_ID = uuid4()
UNIT_ID = uuid4()
CUSTOMER_ID = uuid4()
PROJECT_ID = uuid4()


def _table(name: str, *columns: str) -> TableMetadata:
    return TableMetadata(
        schema_name="erp",
        table_name=name,
        columns=list(columns),
        description="fixture",
        column_metadata=[
            ColumnMetadata(
                name=column,
                data_type="varchar",
                nullable=False,
            )
            for column in columns
        ],
    )


TABLES = [
    _table("org_unit_lkp", "org_cd", "org_nm"),
    _table("cust_mst", "cust_cd", "cust_nm"),
    _table("prj_hdr", "prj_no", "prj_nm"),
]


def _model() -> SemanticModel:
    entities = (
        SemanticEntity(
            id=UNIT_ID,
            data_source_id=SOURCE_ID,
            source_schema="erp",
            source_table="org_unit_lkp",
            entity_name="Organizational Unit",
            status=ApprovalStatus.CONFIRMED,
        ),
        SemanticEntity(
            id=CUSTOMER_ID,
            data_source_id=SOURCE_ID,
            source_schema="erp",
            source_table="cust_mst",
            entity_name="Customer",
            status=ApprovalStatus.CONFIRMED,
        ),
        SemanticEntity(
            id=PROJECT_ID,
            data_source_id=SOURCE_ID,
            source_schema="erp",
            source_table="prj_hdr",
            entity_name="Project",
            status=ApprovalStatus.CONFIRMED,
        ),
    )
    attributes: list[SemanticAttribute] = []
    for entity_id, key, display in (
        (UNIT_ID, "org_cd", "org_nm"),
        (CUSTOMER_ID, "cust_cd", "cust_nm"),
        (PROJECT_ID, "prj_no", "prj_nm"),
    ):
        attributes.extend(
            (
                SemanticAttribute(
                    id=uuid4(),
                    data_source_id=SOURCE_ID,
                    entity_id=entity_id,
                    source_column=key,
                    concept_name="Business Key",
                    is_identifier=True,
                    status=ApprovalStatus.CONFIRMED,
                ),
                SemanticAttribute(
                    id=uuid4(),
                    data_source_id=SOURCE_ID,
                    entity_id=entity_id,
                    source_column=display,
                    concept_name="Business Name",
                    status=ApprovalStatus.CONFIRMED,
                ),
            )
        )
    return SemanticModel(
        data_source_id=SOURCE_ID,
        schema_fingerprint="erp-v1",
        entities=entities,
        attributes=tuple(attributes),
    )


class _RecordingGateway(DatabaseGateway):
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def source(self) -> DatabaseSource:
        return DatabaseSource(identifier="legacy", dialect="postgres")

    async def health_check(self) -> bool:
        return True

    async def search_schema(self, question: str) -> list[TableMetadata]:
        del question
        return TABLES

    async def execute_readonly(
        self, sql: str, parameters: Sequence[object] = ()
    ) -> DatabaseQueryResult:
        self.calls.append((sql, tuple(parameters)))
        term = str(parameters[0]).casefold() if parameters else ""
        rows: list[dict[str, str]] = []
        if "org_unit_lkp" in sql and "ou2100" in term:
            rows = [{"canonical_key": "OU2100", "display_value": "Operations"}]
        elif "org_unit_lkp" in sql and "operations" in term:
            rows = [
                {"canonical_key": "OU2100", "display_value": "Operations"},
                {"canonical_key": "OU2200", "display_value": "Operations"},
            ]
        elif "org_unit_lkp" in sql and "platform engineering" in term:
            rows = [
                {
                    "canonical_key": "OU1000",
                    "display_value": "Platform Engineering",
                }
            ]
        elif "cust_mst" in sql and "acme holdings" in term:
            rows = [{"canonical_key": "C0001", "display_value": "ACME Holdings"}]
        elif "cust_mst" in sql and "acme holding co" in term:
            rows = [{"canonical_key": "C0002", "display_value": "ACME Holding Co."}]
        elif "cust_mst" in sql and term == "c0022":
            rows = [{"canonical_key": "C0022", "display_value": "Customer 22"}]
        elif "prj_hdr" in sql and "atlas migration" in term:
            rows = [
                {"canonical_key": "5007", "display_value": "Atlas Migration"},
                {
                    "canonical_key": "5008",
                    "display_value": "Atlas Migration Phase 2",
                },
            ]
        elif "prj_hdr" in sql and "project 040" in term:
            rows = [{"canonical_key": "5040", "display_value": "Project 040"}]
        return DatabaseQueryResult(
            rows=rows,
            columns=[
                ResultColumnMetadata(name="canonical_key", data_type="text"),
                ResultColumnMetadata(name="display_value", data_type="text"),
            ],
            metadata=DatabaseExecutionMetadata(
                duration_ms=1,
                executed_at=datetime.now(UTC),
                row_count=len(rows),
                result_bytes=0,
                truncated=False,
                live=False,
            ),
        )

    async def close(self) -> None:
        return None


@pytest.mark.anyio
async def test_live_lookup_preserves_canonical_identity_and_ambiguity() -> None:
    database = _RecordingGateway()
    resolver = DatabaseEntityValueGateway(database)

    exact = await resolver.resolve(
        user_text="Show payroll for OU2100",
        semantic_model=_model(),
        authorized_tables=TABLES,
        concept="Organizational Unit",
    )
    assert exact.resolved is not None
    assert exact.resolved.canonical_key == "OU2100"
    assert exact.resolved.display_value == "Operations"
    assert exact.resolved.semantic_entity_id == UNIT_ID

    ambiguous = await resolver.resolve(
        user_text="Show payroll for Operations",
        semantic_model=_model(),
        authorized_tables=TABLES,
        concept="Organizational Unit",
    )
    assert ambiguous.is_ambiguous
    assert {(item.canonical_key, item.display_value) for item in ambiguous.candidates} == {
        ("OU2100", "Operations"),
        ("OU2200", "Operations"),
    }


@pytest.mark.anyio
async def test_live_lookup_handles_large_entities_without_scanner_samples() -> None:
    resolver = DatabaseEntityValueGateway(_RecordingGateway())
    customer = await resolver.resolve(
        user_text="C0022",
        semantic_model=_model(),
        authorized_tables=TABLES,
        concept="Customer",
    )
    project = await resolver.resolve(
        user_text="Project 040",
        semantic_model=_model(),
        authorized_tables=TABLES,
        concept="Project",
    )
    assert customer.resolved is not None
    assert customer.resolved.canonical_key == "C0022"
    assert project.resolved is not None
    assert project.resolved.canonical_key == "5040"


@pytest.mark.anyio
async def test_live_lookup_never_queries_unapproved_or_unauthorized_columns() -> None:
    database = _RecordingGateway()
    resolver = DatabaseEntityValueGateway(database)
    restricted = [_table("org_unit_lkp", "org_cd")]

    resolution = await resolver.resolve(
        user_text="Operations",
        semantic_model=_model(),
        authorized_tables=restricted,
        concept="Organizational Unit",
    )

    assert resolution.is_unresolved
    assert database.calls == []
    assert entity_lookup_bindings(_model(), restricted) == ()
