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


def _rows(count: int) -> list[dict[str, object]]:
    """Rows whose values contain no small integers.

    Names are spelled out rather than numbered: a fixture of "E0".."E41"
    contains the digits 41, so an off-by-one assertion would be testing that
    the harvester ignores a number the result genuinely holds.
    """
    return [
        {"emp_no": 100000 + index, "emp_nm": f"Employee {chr(65 + index % 26)}"}
        for index in range(count)
    ]


def _one_claim() -> list[GroundedClaim]:
    return [
        GroundedClaim(
            claim="first",
            evidence=[ClaimEvidence(row_index=0, field="emp_no", value=100000)],
        )
    ]


@pytest.mark.parametrize(
    "answer",
    [
        # The count of what was asked about, in the words a person would use.
        "42 active employees have had more than one compensation change.",
        "There are 42 active employees with multiple changes.",
        "A total of 42 employees qualify.",
        # Explicit result-shape wording keeps working.
        "The query returned 42 rows.",
    ],
)
def test_a_numeral_equal_to_the_row_count_is_supported(answer: str) -> None:
    """A right answer was thrown away because of the noun it used.

    The rule used to accept only "rows", "records", "departments" or
    "categories" -- the demo database's vocabulary. On a schema of employees or
    projects, a correct count was rejected as an invented number.
    """
    assert GroundingValidator().validate(
        answer=answer, claims=_one_claim(), rows=_rows(42)
    )


@pytest.mark.parametrize(
    "answer",
    [
        # Equal to the row count, but stated as money rather than a count.
        "Total payroll is $42 dollars.",
        "The amount is $42 across the board.",
        "We paid 42 dollars.",
        # Not the row count at all.
        "There are 41 active employees.",
        "Revenue grew by 99 percent.",
    ],
)
def test_a_numeral_that_is_not_a_count_of_the_result_is_refused(answer: str) -> None:
    with pytest.raises(GroundingFailureError):
        GroundingValidator().validate(
            answer=answer, claims=_one_claim(), rows=_rows(42)
        )


@pytest.mark.parametrize(
    "answer",
    [
        "OU2100 has the highest average salary at 113,571.43.",
        "OU2100 averages 113,571.4286.",
    ],
)
def test_a_rounded_rendering_of_a_real_value_is_supported(answer: str) -> None:
    """Rounding for display is presentation, not invention.

    An average of 113571.428571 shown to two places was rejected as an
    unsupported number, failing an otherwise correct answer.
    """
    rows: list[dict[str, object]] = [
        {"org_cd": "OU2100", "avg_salary": "113571.428571428571"}
    ]
    claims = [
        GroundedClaim(
            claim="highest",
            evidence=[ClaimEvidence(row_index=0, field="org_cd", value="OU2100")],
        )
    ]

    assert GroundingValidator().validate(answer=answer, claims=claims, rows=rows)


@pytest.mark.parametrize(
    "answer",
    [
        "OU2100 averages 113,571.44.",  # not what the value rounds to
        "OU2100 averages 999,999.99.",  # nothing like any value
        "OU2100 averages 113,571.428571428572.",  # more precision than exists
    ],
)
def test_rounding_cannot_manufacture_a_figure(answer: str) -> None:
    rows: list[dict[str, object]] = [
        {"org_cd": "OU2100", "avg_salary": "113571.428571428571"}
    ]
    claims = [
        GroundedClaim(
            claim="highest",
            evidence=[ClaimEvidence(row_index=0, field="org_cd", value="OU2100")],
        )
    ]

    with pytest.raises(GroundingFailureError):
        GroundingValidator().validate(answer=answer, claims=claims, rows=rows)


def test_a_year_inside_a_month_label_is_quoting_the_result() -> None:
    """The failure this guards against sank every time-series answer.

    A month column of "2024-02" yielded neither 2024 nor 02 under the prose
    pattern, whose word-boundary guards exist to avoid matching inside an
    identifier. Right for reading a sentence, wrong for reading data: naming
    the year alongside the month is quoting the result back, not inventing.
    """
    rows: list[dict[str, object]] = [
        {"month": "2024-02", "invoiced_revenue": "6900.0000"},
        {"month": "2024-11", "invoiced_revenue": "43200.0000"},
    ]
    claims = [
        GroundedClaim(
            claim="February",
            evidence=[ClaimEvidence(row_index=0, field="month", value="2024-02")],
        )
    ]

    assert GroundingValidator().validate(
        answer="Invoiced revenue started at 6,900 in February 2024 and "
        "ended at 43,200 in November.",
        claims=claims,
        rows=rows,
    )


def test_a_round_approximation_is_still_refused() -> None:
    """"consistently above 100,000" is a figure the model made up."""
    rows: list[dict[str, object]] = [
        {"month": "2024-04", "invoiced_revenue": "113050.0000"},
    ]
    claims = [
        GroundedClaim(
            claim="April",
            evidence=[ClaimEvidence(row_index=0, field="month", value="2024-04")],
        )
    ]

    with pytest.raises(GroundingFailureError):
        GroundingValidator().validate(
            answer="Revenue stayed above 100,000 through April 2024.",
            claims=claims,
            rows=rows,
        )
