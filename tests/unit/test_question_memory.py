"""Question memory, fingerprints and clustering."""

from __future__ import annotations

import dataclasses
from uuid import uuid4

import pytest

from app.knowledge.fingerprints import (
    adhoc_fingerprint,
    governed_fingerprint,
    normalize_question,
    structures_are_compatible,
)
from app.knowledge.memory import InMemoryQuestionMemory, QuestionEvent

SOURCE_A = uuid4()
SOURCE_B = uuid4()

PAYROLL_FINGERPRINT = governed_fingerprint(
    metric_keys=["annual_base_payroll"], dimensions=["department"]
)


def event(
    source: uuid4,  # type: ignore[valid-type]
    question: str,
    *,
    fingerprint: str = PAYROLL_FINGERPRINT,
    trustworthy: bool = True,
) -> QuestionEvent:
    return QuestionEvent(
        data_source_id=source,
        question_text=question,
        structural_fingerprint=fingerprint,
        route="governed_metric",
        metric_keys=("annual_base_payroll",),
        success=trustworthy,
        validated=trustworthy,
        grounded=trustworthy,
    )


# --- Fingerprints ---------------------------------------------------------


def test_governed_fingerprint_is_stable_across_spelling_and_order() -> None:
    left = governed_fingerprint(
        metric_keys=["annual_base_payroll", "active_headcount"],
        dimensions=["department"],
    )
    right = governed_fingerprint(
        metric_keys=["Active_Headcount", " annual_base_payroll "],
        dimensions=[" Department "],
    )

    assert left == right
    assert structures_are_compatible(left, right)


def test_different_grain_is_a_different_structure() -> None:
    by_department = governed_fingerprint(
        metric_keys=["annual_base_payroll"], dimensions=["department"]
    )
    overall = governed_fingerprint(
        metric_keys=["annual_base_payroll"], dimensions=[]
    )

    assert not structures_are_compatible(by_department, overall)


def test_fingerprint_records_filter_shape_but_never_the_value() -> None:
    fingerprint = governed_fingerprint(
        metric_keys=["invoice_amount"],
        dimensions=[],
        filter_dimensions=[("customer", "eq")],
    )

    assert "filter:customer:eq" in fingerprint
    # There is no parameter capable of carrying the operand at all.
    assert "acme" not in fingerprint.casefold()


def test_adhoc_fingerprint_captures_shape_without_literals() -> None:
    fingerprint = adhoc_fingerprint(
        "SELECT d.name, SUM(e.salary) FROM analytics.employees e "
        "JOIN analytics.departments d ON d.id = e.department_id "
        "WHERE e.status = 'active' AND d.name = 'ACME Secret Division' "
        "GROUP BY d.name ORDER BY 2 DESC"
    )

    assert "analytics.employees" in fingerprint
    assert "sum" in fingerprint
    assert "status:eq" in fingerprint
    assert "acme secret division" not in fingerprint.casefold()
    assert "active" not in fingerprint.replace("analytics.", "")


def test_unparseable_sql_degrades_instead_of_raising() -> None:
    assert "parse=failed" in adhoc_fingerprint("this is not sql ((")


def test_normalize_question_collapses_case_and_whitespace() -> None:
    assert normalize_question("  Payroll   BY  Department ") == "payroll by department"


# --- Memory and clustering ------------------------------------------------


@pytest.mark.anyio
async def test_paraphrases_with_the_same_structure_share_a_cluster() -> None:
    memory = InMemoryQuestionMemory()

    await memory.record(event(SOURCE_A, "payroll by department"))
    await memory.record(event(SOURCE_A, "salary spending for every team"))
    cluster = await memory.record(
        event(SOURCE_A, "yearly employee compensation by organizational unit")
    )

    assert cluster.occurrence_count == 3
    assert cluster.successful_count == 3
    assert len(await memory.clusters(SOURCE_A)) == 1


@pytest.mark.anyio
async def test_the_same_wording_against_another_datasource_is_a_separate_cluster() -> (
    None
):
    memory = InMemoryQuestionMemory()

    await memory.record(event(SOURCE_A, "payroll by department"))
    await memory.record(event(SOURCE_B, "payroll by department"))

    a_clusters = await memory.clusters(SOURCE_A)
    b_clusters = await memory.clusters(SOURCE_B)
    assert len(a_clusters) == 1
    assert len(b_clusters) == 1
    assert a_clusters[0].id != b_clusters[0].id
    assert a_clusters[0].occurrence_count == 1


@pytest.mark.anyio
async def test_incompatible_structures_do_not_merge() -> None:
    """Similar wording, different grain: these are different analytical needs."""
    memory = InMemoryQuestionMemory()

    await memory.record(event(SOURCE_A, "payroll by department"))
    await memory.record(
        event(
            SOURCE_A,
            "payroll overall",
            fingerprint=governed_fingerprint(
                metric_keys=["annual_base_payroll"], dimensions=[]
            ),
        )
    )

    assert len(await memory.clusters(SOURCE_A)) == 2


@pytest.mark.anyio
async def test_failed_requests_count_as_occurrences_but_not_as_evidence() -> None:
    memory = InMemoryQuestionMemory()

    await memory.record(event(SOURCE_A, "payroll by department", trustworthy=False))
    cluster = await memory.record(event(SOURCE_A, "payroll by department"))

    assert cluster.occurrence_count == 2
    assert cluster.successful_count == 1, "an untrustworthy event became evidence"


@pytest.mark.anyio
async def test_ungrounded_success_is_not_trustworthy_evidence() -> None:
    ungrounded = QuestionEvent(
        data_source_id=SOURCE_A,
        question_text="payroll by department",
        structural_fingerprint=PAYROLL_FINGERPRINT,
        route="adhoc_analytics",
        success=True,
        validated=True,
        grounded=False,
    )

    assert not ungrounded.is_trustworthy_evidence


@pytest.mark.anyio
async def test_eligibility_respects_configured_thresholds() -> None:
    memory = InMemoryQuestionMemory()
    for _ in range(3):
        await memory.record(event(SOURCE_A, "payroll by department"))

    assert await memory.eligible_clusters(
        SOURCE_A, min_occurrences=3, min_successful=3
    )
    assert not await memory.eligible_clusters(
        SOURCE_A, min_occurrences=4, min_successful=3
    )


def test_question_events_cannot_carry_results() -> None:
    """The boundary that stops memory becoming an answer cache.

    Asserted against the contract rather than against behaviour: if someone
    later adds a field for rows or measures, this fails immediately.
    """
    fields = {field.name for field in dataclasses.fields(QuestionEvent)}
    forbidden = {"rows", "result", "result_rows", "answer", "values", "measures"}

    assert not (fields & forbidden), f"question memory gained a result field: {fields}"
