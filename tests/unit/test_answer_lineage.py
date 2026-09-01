"""Lineage read off the statement that ran, never invented.

The failure worth guarding against is a confident wrong picture. Lineage that
says a figure came from a column it did not come from is unfalsifiable by the
reader and gets acted on, so where it cannot be derived this says so.
"""

from __future__ import annotations

from uuid import uuid4

from app.agent.lineage import lineage_from_metric, lineage_from_sql
from app.knowledge.contracts import ApprovalStatus, SemanticEntity
from app.knowledge.discovery import SemanticModel
from app.knowledge.expressions import BinaryOp, Literal_, MetricRef

SOURCE = uuid4()


def _model() -> SemanticModel:
    return SemanticModel(
        data_source_id=SOURCE,
        schema_fingerprint="fp",
        entities=(
            SemanticEntity(
                id=uuid4(),
                data_source_id=SOURCE,
                source_schema="erp",
                source_table="emp_mst",
                entity_name="Employee",
                status=ApprovalStatus.CONFIRMED,
            ),
        ),
    )


def test_columns_are_attributed_when_the_statement_says_which_table() -> None:
    lineage = lineage_from_sql(
        "SELECT h.inv_no, l.qty, l.unit_amt FROM erp.ar_inv_hdr AS h"
        " JOIN erp.ar_inv_ln AS l ON l.inv_no = h.inv_no WHERE h.void_flg = 'N'"
    )

    assert [item.table for item in lineage.tables] == [
        "erp.ar_inv_hdr",
        "erp.ar_inv_ln",
    ]
    assert lineage.tables[0].columns == ("inv_no", "void_flg")
    assert lineage.tables[1].columns == ("inv_no", "qty", "unit_amt")
    assert lineage.column_level


def test_an_unqualified_column_across_two_tables_falls_back_to_table_level() -> None:
    """Guessing which table it came from is the one thing worth not doing."""
    lineage = lineage_from_sql(
        "SELECT amount FROM erp.ar_inv_hdr AS h JOIN erp.ar_inv_ln AS l"
        " ON l.inv_no = h.inv_no"
    )

    assert not lineage.column_level
    assert "Table-level lineage" in lineage.note


def test_a_single_table_statement_can_attribute_unqualified_columns() -> None:
    lineage = lineage_from_sql(
        "SELECT emp_no, stat_cd FROM erp.emp_mst WHERE stat_cd = 'A'"
    )

    assert lineage.tables[0].columns == ("emp_no", "stat_cd")
    assert lineage.column_level


def test_a_cte_is_not_reported_as_a_table() -> None:
    """It names a result computed inside the statement, not a real relation."""
    lineage = lineage_from_sql(
        "WITH current_pay AS (SELECT emp_no, ann_sal_amt FROM erp.emp_comp_hist"
        " WHERE curr_flg = 'Y') SELECT emp_no FROM current_pay"
    )

    assert [item.table for item in lineage.tables] == ["erp.emp_comp_hist"]


def test_a_confirmed_entity_names_the_table_a_reader_recognises() -> None:
    lineage = lineage_from_sql(
        "SELECT emp_no FROM erp.emp_mst", semantic_model=_model()
    )

    assert lineage.tables[0].entity == "Employee"


def test_a_governed_answer_still_reports_the_tables_it_is_known_to_read() -> None:
    """There is no SQL to parse, so the recorded tables are what is honest."""
    lineage = lineage_from_sql(
        None, fallback_tables=("analytics.employees", "analytics.departments")
    )

    assert [item.table for item in lineage.tables] == [
        "analytics.employees",
        "analytics.departments",
    ]
    assert not lineage.column_level
    assert "no statement" in lineage.note


def test_unparseable_sql_degrades_rather_than_inventing() -> None:
    lineage = lineage_from_sql(
        "SELECT FROM WHERE ((", fallback_tables=("erp.emp_mst",)
    )

    assert not lineage.column_level
    assert [item.table for item in lineage.tables] == ["erp.emp_mst"]


def test_a_derived_metric_renders_its_own_expression_tree() -> None:
    """Already bounded and already stored: this is a rendering, not an analysis."""
    node = lineage_from_metric(
        "payroll_per_active_employee",
        BinaryOp(
            operator="divide",
            left=MetricRef(metric_key="annual_base_payroll"),
            right=MetricRef(metric_key="active_headcount"),
        ),
    )

    assert node.label == "payroll_per_active_employee"
    assert node.kind == "divide"
    assert [child.label for child in node.children] == [
        "annual_base_payroll",
        "active_headcount",
    ]


def test_a_literal_operand_is_shown_as_a_number() -> None:
    node = lineage_from_metric(
        "scaled",
        BinaryOp(
            operator="multiply",
            left=MetricRef(metric_key="annual_base_payroll"),
            right=Literal_(value="1.05"),
        ),
    )

    assert [child.label for child in node.children] == [
        "annual_base_payroll",
        "1.05",
    ]
