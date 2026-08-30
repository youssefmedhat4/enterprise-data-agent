import pytest

from app.agent.grounding import GroundingValidator
from app.contracts.analytics import ClaimEvidence, GroundedClaim
from app.errors import GroundingFailureError


def claim(*evidence: ClaimEvidence) -> GroundedClaim:
    return GroundedClaim(claim="Supported analytical claim.", evidence=list(evidence))


def test_accepts_correct_grounded_number_and_aggregation() -> None:
    rows: list[dict[str, object]] = [{"department": "Engineering", "total_payroll": "12400000.00"}]
    claims = [
        claim(
            ClaimEvidence(row_index=0, field="department", value="Engineering"),
            ClaimEvidence(row_index=0, field="total_payroll", value="12400000.00"),
        )
    ]

    validated = GroundingValidator().validate(
        answer="Engineering has total payroll of $12,400,000.00.",
        claims=claims,
        rows=rows,
    )

    assert validated == claims


def test_rejects_wrong_number_in_answer() -> None:
    rows = [{"department": "Engineering", "total_payroll": 100}]
    claims = [claim(ClaimEvidence(row_index=0, field="total_payroll", value=100))]

    with pytest.raises(GroundingFailureError):
        GroundingValidator().validate(answer="Payroll is 101.", claims=claims, rows=rows)


def test_rejects_unsupported_claim_and_missing_claims() -> None:
    rows = [{"department": "Engineering", "total_payroll": 100}]
    unsupported = [claim(ClaimEvidence(row_index=0, field="department", value="Sales"))]

    with pytest.raises(GroundingFailureError):
        GroundingValidator().validate(
            answer="Sales has the highest payroll.",
            claims=unsupported,
            rows=rows,
        )
    with pytest.raises(GroundingFailureError):
        GroundingValidator().validate(
            answer="Engineering has the highest payroll.",
            claims=[],
            rows=rows,
        )


def test_empty_results_require_no_claims_or_numbers() -> None:
    assert (
        GroundingValidator().validate(
            answer="No matching rows were returned.",
            claims=[],
            rows=[],
        )
        == []
    )
    with pytest.raises(GroundingFailureError):
        GroundingValidator().validate(
            answer="No rows, but total is 1.",
            claims=[],
            rows=[],
        )


def department_rows() -> list[dict[str, object]]:
    return [
        {"department_name": "Engineering", "project_margin": "116300.00"},
        {"department_name": "Finance", "project_margin": "0.00"},
        {"department_name": "People Operations", "project_margin": "0.00"},
        {"department_name": "Sales", "project_margin": "0.00"},
    ]


def department_claim() -> GroundedClaim:
    return claim(
        ClaimEvidence(row_index=0, field="department_name", value="Engineering"),
        ClaimEvidence(row_index=0, field="project_margin", value="116300.00"),
    )


def test_accepts_row_count_summary_that_is_not_a_cell_value() -> None:
    validated = GroundingValidator().validate(
        answer="The result contains 4 departments. Engineering has the highest project margin.",
        claims=[department_claim()],
        rows=department_rows(),
    )

    assert len(validated) == 1


def test_accepts_explicit_bounded_rank_wording() -> None:
    assert GroundingValidator().validate(
        answer="Engineering is ranked 1 by project margin, at 116,300.00.",
        claims=[department_claim()],
        rows=department_rows(),
    )


def test_rejects_unrelated_small_business_integer() -> None:
    with pytest.raises(GroundingFailureError) as excinfo:
        GroundingValidator().validate(
            answer="Engineering has 3 projects and a project margin of 116,300.00.",
            claims=[department_claim()],
            rows=department_rows(),
        )

    assert excinfo.value.reason == "GROUNDING_UNSUPPORTED_NUMERIC_CLAIM"


def test_rejects_fabricated_business_value_despite_structural_allowance() -> None:
    with pytest.raises(GroundingFailureError) as excinfo:
        GroundingValidator().validate(
            answer="Engineering payroll is $9,999,999; project margin is 116,300.00.",
            claims=[department_claim()],
            rows=department_rows(),
        )

    assert excinfo.value.reason == "GROUNDING_UNSUPPORTED_NUMERIC_CLAIM"


def test_rejects_invented_decimal_even_when_small() -> None:
    """3.5 is inside the row-count range but is not an integer, so it must be evidenced."""
    with pytest.raises(GroundingFailureError) as excinfo:
        GroundingValidator().validate(
            answer="Engineering's payroll is 3.5 times the median; margin is 116,300.00.",
            claims=[department_claim()],
            rows=department_rows(),
        )

    assert excinfo.value.reason == "GROUNDING_UNSUPPORTED_NUMERIC_CLAIM"


def test_rejects_integer_beyond_the_row_count() -> None:
    with pytest.raises(GroundingFailureError):
        GroundingValidator().validate(
            answer="There are 42 departments; Engineering margin is 116,300.00.",
            claims=[department_claim()],
            rows=department_rows(),
        )


def test_wrong_evidence_value_still_fails_with_a_specific_reason() -> None:
    with pytest.raises(GroundingFailureError) as excinfo:
        GroundingValidator().validate(
            answer="Engineering has the highest project margin at 116,300.00.",
            claims=[
                claim(
                    ClaimEvidence(
                        row_index=0,
                        field="project_margin",
                        value="99999999.00",
                    )
                )
            ],
            rows=department_rows(),
        )

    assert excinfo.value.reason == "GROUNDING_EVIDENCE_VALUE_MISMATCH"


def test_failure_diagnostics_and_logs_are_content_safe(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("WARNING")
    with pytest.raises(GroundingFailureError) as excinfo:
        GroundingValidator().validate(
            answer="Engineering salary is $9,999,999; project margin is 116,300.00.",
            claims=[department_claim()],
            rows=department_rows(),
        )

    error = excinfo.value
    assert error.detail is not None
    assert "9,999,999" not in error.detail
    assert "9,999,999" not in caplog.text
    assert "116,300" not in caplog.text
    assert "Engineering" not in caplog.text
    assert "salary" not in caplog.text.lower()
    assert "9,999,999" not in error.safe_message
    assert "payroll" not in error.safe_message.lower()


@pytest.mark.parametrize(
    ("evidence", "reason"),
    [
        (
            ClaimEvidence(row_index=4, field="department_name", value="Engineering"),
            "GROUNDING_EVIDENCE_ROW_OUT_OF_RANGE",
        ),
        (
            ClaimEvidence(row_index=0, field="salary", value="116300.00"),
            "GROUNDING_EVIDENCE_FIELD_MISSING",
        ),
        (
            ClaimEvidence(row_index=0, field="project_margin", value="999.00"),
            "GROUNDING_EVIDENCE_VALUE_MISMATCH",
        ),
    ],
)
def test_rejects_invalid_structured_evidence(
    evidence: ClaimEvidence,
    reason: str,
) -> None:
    with pytest.raises(GroundingFailureError) as excinfo:
        GroundingValidator().validate(
            answer="Engineering has the highest project margin at 116,300.00.",
            claims=[claim(evidence)],
            rows=department_rows(),
        )

    assert excinfo.value.reason == reason


def test_null_ranking_and_percentage_evidence_are_supported() -> None:
    rows = [
        {
            "rank": 1,
            "department": "Engineering",
            "growth_percent": 12.5,
            "note": None,
        }
    ]
    claims = [
        claim(
            ClaimEvidence(row_index=0, field="rank", value=1),
            ClaimEvidence(row_index=0, field="department", value="Engineering"),
            ClaimEvidence(row_index=0, field="growth_percent", value=12.5),
            ClaimEvidence(row_index=0, field="note", value=None),
        )
    ]

    assert GroundingValidator().validate(
        answer="Engineering ranks 1 with 12.5% growth.",
        claims=claims,
        rows=rows,
    )
