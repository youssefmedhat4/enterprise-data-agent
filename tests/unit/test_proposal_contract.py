"""What a model is allowed to propose, and what it may spell loosely.

Every existing test of the learning loop hands it a scripted proposal, which is
why none of them noticed that a real provider could not produce one at all. The
schema itself was rejected, the structural tags never matched, and one runaway
prose field could destroy the whole response. These check the contract against
the shapes a real model actually emits.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.knowledge.candidates import (
    MAX_PROPOSAL_PROSE,
    MAX_PROPOSAL_REASON,
    CandidateGeneration,
)
from app.knowledge.expressions import BinaryOp, MetricRef, describe, referenced_metrics


def test_the_expression_schema_has_no_required_ref_loop() -> None:
    """Vertex refuses a recursion reachable only through required fields.

    "a ref loop of required fields was found at
    $defs.BinaryOp.properties.left" -- a 400 on every single generation, for
    months of proposals, with nothing in the tests to catch it because they all
    use a scripted model.
    """
    schema = CandidateGeneration.model_json_schema()
    binary = schema["$defs"]["BinaryOp"]

    assert "left" not in binary.get("required", [])
    assert "right" not in binary.get("required", [])


def test_an_operand_is_still_required_in_the_contract() -> None:
    """Nullable in the schema is not nullable in the contract."""
    with pytest.raises(ValidationError):
        BinaryOp.model_validate({"operator": "divide", "left": None, "right": None})

    with pytest.raises(ValidationError):
        BinaryOp.model_validate(
            {"operator": "divide", "left": {"metric_key": "a"}}
        )


@pytest.mark.parametrize(
    ("binary_kind", "reference_kind"),
    [
        ("binary", "metric"),  # the canonical spelling
        ("MetricExpression", "MetricReference"),
        ("binary_operation", "metric_ref"),
        ("operation", "metric reference"),
    ],
)
def test_a_node_is_read_from_its_shape_not_its_tag(
    binary_kind: str, reference_kind: str
) -> None:
    """A correct proposal was thrown away over how the model spelled `kind`.

    The fields already say which shape a node is; the tag only restates it.
    """
    node = BinaryOp.model_validate(
        {
            "kind": binary_kind,
            "operator": "divide",
            "left": {"kind": reference_kind, "metric_key": "annual_base_payroll"},
            "right": {"kind": reference_kind, "metric_key": "active_headcount"},
        }
    )

    assert describe(node) == "(annual_base_payroll / active_headcount)"
    assert referenced_metrics(node) == {"annual_base_payroll", "active_headcount"}


def test_a_node_whose_fields_fit_no_shape_is_still_refused() -> None:
    with pytest.raises(ValidationError):
        MetricRef.model_validate({"kind": "metric", "sql": "SELECT 1"})


def test_the_proposal_type_follows_its_shape_too() -> None:
    generation = CandidateGeneration.model_validate(
        {
            "proposes": True,
            "reason": "asked repeatedly",
            "metric": {
                "candidate_type": "metric",  # not "METRIC"
                "metric_key": "cost_per_active_employee",
                "display_name": "Cost per active employee",
                "expression": {
                    "kind": "MetricExpression",
                    "operator": "divide",
                    "left": {"kind": "MetricReference", "metric_key": "annual_base_payroll"},
                    "right": {"kind": "MetricReference", "metric_key": "active_headcount"},
                },
            },
        }
    )

    assert generation.metric is not None
    assert generation.metric.candidate_type == "METRIC"
    assert describe(generation.metric.expression) == (
        "(annual_base_payroll / active_headcount)"
    )


def test_runaway_prose_is_refused_rather_than_truncating_the_response() -> None:
    """One repetition loop used to consume the entire output budget.

    The JSON then arrived cut mid-string and was reported as an invalid
    structured response, which points the reader at the schema instead of at
    the model.
    """
    with pytest.raises(ValidationError):
        CandidateGeneration.model_validate(
            {"proposes": True, "reason": "x" * (MAX_PROPOSAL_REASON + 1)}
        )

    with pytest.raises(ValidationError):
        CandidateGeneration.model_validate(
            {
                "proposes": True,
                "business_rule": {
                    "display_name": "Rule",
                    "instruction": "y" * (MAX_PROPOSAL_PROSE + 1),
                },
            }
        )


def test_an_honest_proposal_is_nowhere_near_the_prose_bound() -> None:
    generation = CandidateGeneration.model_validate(
        {
            "proposes": True,
            "reason": "Asked eight times in this datasource.",
            "business_rule": {
                "display_name": "Current annual payroll population",
                "instruction": (
                    "Current annual payroll counts every current compensation "
                    "record, whatever the employee's employment status."
                ),
                "semantic_concepts": ["payroll"],
            },
        }
    )

    assert generation.business_rule is not None
    assert generation.business_rule.candidate_type == "BUSINESS_RULE"
