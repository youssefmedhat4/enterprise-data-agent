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
