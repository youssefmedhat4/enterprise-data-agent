"""Focused live checks for the fixed Legacy ERP fixture.

This is intentionally separate from model tests.  It proves fixture integrity,
the independent canonical values used to assess generated SQL, and that runtime
entity resolution reaches the real database rather than a scan sample.  It makes
no LLM call and does not treat a plausible answer as a passing result.

Test code may know the physical schema; production code may not.  The semantic
model below is built here rather than read from a developer's knowledge
database so the harness is deterministic and self-contained.
"""

from __future__ import annotations

import asyncio
import selectors
import sys
from collections.abc import Generator
from decimal import Decimal
from typing import Any
from uuid import uuid4

import psycopg
import pytest

from app.config import Settings, get_settings
from app.data.factory import build_database_gateway_for
from app.knowledge.contracts import ApprovalStatus, SemanticAttribute, SemanticEntity
from app.knowledge.datasources import DataSourceConnectionResolver, DataSourceError
from app.knowledge.discovery import SemanticModel
from app.security.sql_validation import SQLValidationCode, SQLValidator
from app.semantic.entity_values import DatabaseEntityValueGateway

pytestmark = pytest.mark.legacy

LEGACY_SCHEMA = "erp"

#: (table, key column, display column, entity name) for each entity a reviewer
#: would confirm on this fixture.
_ENTITIES = (
    ("org_unit_lkp", "org_cd", "org_nm", "Organizational Unit"),
    ("cust_mst", "cust_cd", "cust_nm", "Customer"),
    ("prj_hdr", "prj_no", "prj_nm", "Project"),
    ("emp_mst", "emp_no", "emp_nm", "Employee"),
)


def _legacy_dsn() -> str:
    try:
        return DataSourceConnectionResolver(get_settings()).resolve(
            "LEGACY_DATABASE_URL"
        )
    except DataSourceError:
        pytest.skip("LEGACY_DATABASE_URL is not configured.")


@pytest.fixture
def legacy_connection() -> Generator[psycopg.Connection[tuple[object, ...]]]:
    dsn = _legacy_dsn()
    try:
        connection = psycopg.connect(dsn, connect_timeout=5)
    except psycopg.Error:
        pytest.skip("Legacy ERP fixture is unavailable.")
    try:
        yield connection
    finally:
        connection.close()


def _scalar(connection: psycopg.Connection[tuple[object, ...]], sql: str) -> object:
    with connection.cursor() as cursor:
        cursor.execute(sql)
        row = cursor.fetchone()
    assert row is not None
    return row[0]


def _rows(
    connection: psycopg.Connection[tuple[object, ...]], sql: str
) -> list[tuple[Any, ...]]:
    with connection.cursor() as cursor:
        cursor.execute(sql)
        return list(cursor.fetchall())


def test_legacy_fixture_counts_and_decisive_business_values(
    legacy_connection: psycopg.Connection[tuple[object, ...]],
) -> None:
    assert _scalar(legacy_connection, "SELECT count(*) FROM erp.org_unit_lkp") == 10
    assert _scalar(legacy_connection, "SELECT count(*) FROM erp.emp_mst") == 60
    assert _scalar(legacy_connection, "SELECT count(*) FROM erp.emp_comp_hist") == 159
    assert _scalar(legacy_connection, "SELECT count(*) FROM erp.cust_mst") == 22
    assert _scalar(legacy_connection, "SELECT count(*) FROM erp.prj_hdr") == 40
    assert _scalar(legacy_connection, "SELECT count(*) FROM erp.ar_inv_hdr") == 102
    assert _scalar(legacy_connection, "SELECT count(*) FROM erp.ar_inv_ln") == 408
    assert _scalar(legacy_connection, "SELECT count(*) FROM erp.gl_cost_txn") == 350
    assert (
        _scalar(
            legacy_connection,
            "SELECT count(*) FROM erp.emp_mst WHERE stat_cd = 'A'",
        )
        == 42
    )
    assert _scalar(
        legacy_connection,
        "SELECT sum(ann_sal_amt) FROM erp.emp_comp_hist WHERE curr_flg = 'Y'",
    ) == Decimal("6345000.00")
    assert _scalar(
        legacy_connection,
        "SELECT sum(l.qty * l.unit_amt - l.disc_amt) "
        "FROM erp.ar_inv_hdr h JOIN erp.ar_inv_ln l ON l.inv_no = h.inv_no "
        "WHERE h.void_flg = 'N'",
    ) == Decimal("839700.00")
    assert _scalar(
        legacy_connection,
        "SELECT sum(cost_amt) FROM erp.gl_cost_txn "
        "WHERE posted_flg = 'Y' AND reversal_flg = 'N'",
    ) == Decimal("1042500.00")


def test_legacy_traps_would_produce_different_numbers(
    legacy_connection: psycopg.Connection[tuple[object, ...]],
) -> None:
    """The wrong answers are wrong by a wide margin, so a pass means something."""
    assert _scalar(
        legacy_connection, "SELECT sum(ann_sal_amt) FROM erp.emp_comp_hist"
    ) == Decimal("15595000.00")
    assert _scalar(legacy_connection, "SELECT count(*) FROM erp.emp_comp_hist") == 159
    assert _scalar(legacy_connection, "SELECT count(*) FROM erp.emp_mst") == 60
    assert _scalar(
        legacy_connection,
        "SELECT sum(l.qty * l.unit_amt - l.disc_amt) "
        "FROM erp.ar_inv_hdr h JOIN erp.ar_inv_ln l ON l.inv_no = h.inv_no",
    ) == Decimal("1283400.00")
    assert _scalar(
        legacy_connection, "SELECT sum(cost_amt) FROM erp.gl_cost_txn"
    ) == Decimal("1439250.00")


def test_legacy_identity_ambiguity_and_large_entity_targets(
    legacy_connection: psycopg.Connection[tuple[object, ...]],
) -> None:
    with legacy_connection.cursor() as cursor:
        cursor.execute(
            "SELECT org_cd, org_nm FROM erp.org_unit_lkp "
            "WHERE org_nm = %s ORDER BY org_cd",
            ("Operations",),
        )
        operations = cursor.fetchall()
        cursor.execute(
            "SELECT cust_cd FROM erp.cust_mst WHERE cust_cd = %s", ("C0022",)
        )
        customer = cursor.fetchone()
        cursor.execute("SELECT prj_no FROM erp.prj_hdr WHERE prj_no = %s", (5040,))
        project = cursor.fetchone()
    assert operations == [("OU2100", "Operations"), ("OU2200", "Operations")]
    assert customer == ("C0022",)
    assert project == (5040,)


def test_the_multi_metric_reference_is_stable(
    legacy_connection: psycopg.Connection[tuple[object, ...]],
) -> None:
    """Per-unit measures, each aggregated independently of the others.

    Computed here without DISTINCT and without deduplication, so a generated
    query that fans invoice lines or cost postings out across a join cannot
    match these numbers by accident.
    """
    rows = _rows(
        legacy_connection,
        """
        SELECT o.org_cd,
               coalesce(h.headcount, 0),
               coalesce(p.payroll, 0),
               coalesce(r.revenue, 0),
               coalesce(k.cost, 0),
               coalesce(r.revenue, 0) - coalesce(k.cost, 0)
        FROM erp.org_unit_lkp o
        LEFT JOIN (SELECT org_cd, count(*) headcount FROM erp.emp_mst
                   WHERE stat_cd = 'A' GROUP BY org_cd) h ON h.org_cd = o.org_cd
        LEFT JOIN (SELECT e.org_cd, sum(c.ann_sal_amt) payroll
                   FROM erp.emp_mst e
                   JOIN erp.emp_comp_hist c
                     ON c.emp_no = e.emp_no AND c.curr_flg = 'Y'
                   GROUP BY e.org_cd) p ON p.org_cd = o.org_cd
        LEFT JOIN (SELECT j.own_org_cd, sum(l.qty * l.unit_amt - l.disc_amt) revenue
                   FROM erp.prj_hdr j
                   JOIN erp.ar_inv_hdr i ON i.prj_no = j.prj_no AND i.void_flg = 'N'
                   JOIN erp.ar_inv_ln l ON l.inv_no = i.inv_no
                   GROUP BY j.own_org_cd) r ON r.own_org_cd = o.org_cd
        LEFT JOIN (SELECT j.own_org_cd, sum(g.cost_amt) cost
                   FROM erp.prj_hdr j
                   JOIN erp.gl_cost_txn g
                     ON g.prj_no = j.prj_no
                    AND g.posted_flg = 'Y' AND g.reversal_flg = 'N'
                   GROUP BY j.own_org_cd) k ON k.own_org_cd = o.org_cd
        ORDER BY o.org_cd
        """,
    )

    expected = {
        "OU1000": (4, "645000.00", "114850.0000", "0", "114850.0000"),
        "OU1100": (4, "760000.00", "125500.0000", "177750.00", "-52250.0000"),
        "OU2000": (5, "675000.00", "0", "0", "0"),
        "OU2100": (4, "795000.00", "129200.0000", "181500.00", "-52300.0000"),
        "OU2200": (4, "710000.00", "112450.0000", "173250.00", "-60800.0000"),
        "OU3000": (5, "745000.00", "110500.0000", "153000.00", "-42500.0000"),
        "OU3100": (5, "675000.00", "0", "0", "0"),
        "OU4000": (6, "695000.00", "133900.0000", "177750.00", "-43850.0000"),
        "OU4100": (5, "645000.00", "113300.0000", "179250.00", "-65950.0000"),
        "OU9000": (0, "0", "0", "0", "0"),
    }
    actual = {
        str(code): (headcount, *(str(value) for value in measures))
        for code, headcount, *measures in rows
    }

    assert actual == {
        code: (headcount, *(str(Decimal(value)) for value in measures))
        for code, (headcount, *measures) in expected.items()
    }, "the two units both named Operations merged, or a measure fanned out"


# --- runtime entity resolution against the real database --------------------


def _confirmed_model() -> SemanticModel:
    """What a reviewer would have confirmed for this fixture."""
    entities: list[SemanticEntity] = []
    attributes: list[SemanticAttribute] = []
    source = uuid4()
    for table, key, display, name in _ENTITIES:
        entity_id = uuid4()
        entities.append(
            SemanticEntity(
                id=entity_id,
                data_source_id=source,
                source_schema=LEGACY_SCHEMA,
                source_table=table,
                entity_name=name,
                status=ApprovalStatus.CONFIRMED,
            )
        )
        attributes.append(
            SemanticAttribute(
                id=uuid4(),
                data_source_id=source,
                entity_id=entity_id,
                source_column=key,
                concept_name=f"{name} Key",
                is_identifier=True,
                status=ApprovalStatus.CONFIRMED,
            )
        )
        attributes.append(
            SemanticAttribute(
                id=uuid4(),
                data_source_id=source,
                entity_id=entity_id,
                source_column=display,
                concept_name=f"{name} Name",
                status=ApprovalStatus.CONFIRMED,
            )
        )
    return SemanticModel(
        data_source_id=source,
        schema_fingerprint="legacy-harness",
        entities=tuple(entities),
        attributes=tuple(attributes),
    )


def _run(coroutine: Any) -> Any:
    if sys.platform == "win32":
        with asyncio.Runner(
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
        ) as runner:
            return runner.run(coroutine)
    return asyncio.run(coroutine)


async def _resolve_all(terms: list[str]) -> dict[str, list[tuple[str, str]]]:
    dsn = _legacy_dsn()
    gateway = build_database_gateway_for(
        Settings(), database_url=dsn, allowed_schemas=(LEGACY_SCHEMA,)
    )
    try:
        tables = await gateway.search_schema("entities")
        model = _confirmed_model()
        values = DatabaseEntityValueGateway(gateway)
        resolved: dict[str, list[tuple[str, str]]] = {}
        for term in terms:
            outcome = await values.resolve(
                user_text=term, semantic_model=model, authorized_tables=tables
            )
            resolved[term] = [
                (candidate.canonical_key or "", candidate.display_value or "")
                for candidate in outcome.candidates
            ]
        return resolved
    finally:
        await gateway.close()


def test_entity_values_resolve_against_the_live_datasource() -> None:
    """Scan samples are for discovery; runtime resolution asks the database.

    Sampling is capped, and these customers and projects sit past that cap --
    on the sampled path they were simply unreachable, whatever the user typed.
    """
    try:
        resolved = _run(
            _resolve_all(
                [
                    "OU2100",
                    "Platform Engineering",
                    "Operations",
                    "the one with code OU2100",
                    "ACME Holdings",
                    "ACME Holding Co.",
                    "ACME Holding",
                    "Virtucon Capital",
                    "Project 040",
                    "Atlas Migration",
                    "Atlas Migration Phase 2",
                    "Zorblatt Industries",
                ]
            )
        )
    except Exception as exc:  # pragma: no cover - fixture not running
        pytest.skip(f"Legacy ERP fixture is unavailable ({type(exc).__name__}).")

    # Exact canonical identifier, and the same identifier inside a sentence.
    assert resolved["OU2100"] == [("OU2100", "Operations")]
    assert resolved["the one with code OU2100"] == [("OU2100", "Operations")]

    # Unique display name.
    assert resolved["Platform Engineering"] == [("OU1000", "Platform Engineering")]

    # Two units share a display name and must remain two candidates. This is
    # the case that de-duplicating on the label silently collapses.
    assert resolved["Operations"] == [
        ("OU2100", "Operations"),
        ("OU2200", "Operations"),
    ]

    # Near-identical names: each exact form resolves, the shared prefix does not.
    assert resolved["ACME Holdings"] == [("C0001", "ACME Holdings")]
    assert resolved["ACME Holding Co."] == [("C0002", "ACME Holding Co.")]
    assert len(resolved["ACME Holding"]) == 2, "a prefix guessed one of two"
    assert resolved["Atlas Migration"] == [("5007", "Atlas Migration")]
    assert resolved["Atlas Migration Phase 2"] == [("5008", "Atlas Migration Phase 2")]

    # Beyond the sampling cap: the 22nd customer and the 40th project.
    assert resolved["Virtucon Capital"] == [("C0022", "Virtucon Capital")]
    assert resolved["Project 040"] == [("5040", "Project 040")]

    # A value that does not exist yields nothing rather than an invented key.
    assert resolved["Zorblatt Industries"] == []


def test_entity_lookup_does_not_enumerate_an_entity() -> None:
    """A lookup returns a bounded candidate set, never the whole table."""
    try:
        resolved = _run(_resolve_all(["Project"]))
    except Exception as exc:  # pragma: no cover - fixture not running
        pytest.skip(f"Legacy ERP fixture is unavailable ({type(exc).__name__}).")

    # 40 projects all contain "Project"; resolution stays bounded.
    assert len(resolved["Project"]) <= 5


def test_sql_outside_the_selected_datasource_schema_is_refused() -> None:
    """Schema authorization agrees with the datasource that was selected."""

    async def exercise() -> tuple[bool, SQLValidationCode | None, bool]:
        gateway = build_database_gateway_for(
            Settings(), database_url=_legacy_dsn(), allowed_schemas=(LEGACY_SCHEMA,)
        )
        try:
            tables = await gateway.search_schema("employees")
            validator = SQLValidator(
                max_rows=100, allowed_schemas=frozenset({LEGACY_SCHEMA})
            )
            inside = validator.validate(
                "SELECT emp_no FROM erp.emp_mst WHERE stat_cd = 'A'",
                allowed_schema=tables,
            )
            outside = validator.validate(
                "SELECT employee_id FROM analytics.employees",
                allowed_schema=tables,
            )
            return inside.is_valid, outside.error_code, outside.is_valid
        finally:
            await gateway.close()

    try:
        inside_valid, outside_code, outside_valid = _run(exercise())
    except Exception as exc:  # pragma: no cover - fixture not running
        pytest.skip(f"Legacy ERP fixture is unavailable ({type(exc).__name__}).")

    assert inside_valid, "the selected datasource's own schema was refused"
    assert not outside_valid
    assert outside_code is SQLValidationCode.FORBIDDEN_SCHEMA


def test_the_legacy_role_cannot_write() -> None:
    dsn = _legacy_dsn()
    try:
        connection = psycopg.connect(dsn, connect_timeout=5)
    except psycopg.Error:
        pytest.skip("Legacy ERP fixture is unavailable.")
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('transaction_read_only')")
            row = cursor.fetchone()
            assert row is not None
            assert row[0] == "on"
            with pytest.raises(psycopg.Error):
                cursor.execute("UPDATE erp.emp_mst SET stat_cd = 'A'")
    finally:
        connection.close()

