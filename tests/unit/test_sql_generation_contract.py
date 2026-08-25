import pytest
from pydantic import ValidationError

from app.llm.gateway import SQLGeneration


def test_execute_requires_sql() -> None:
    with pytest.raises(ValidationError, match="requires SQL"):
        SQLGeneration(action="execute")


def test_clarify_requires_question_and_forbids_sql() -> None:
    with pytest.raises(ValidationError, match="cannot contain SQL"):
        SQLGeneration(
            action="clarify",
            sql="SELECT 1",
            clarification_question="Which metric?",
        )


def test_block_requires_reason_and_forbids_sql() -> None:
    with pytest.raises(ValidationError, match="requires a reason"):
        SQLGeneration(action="block", sql="DELETE FROM analytics.employees")


def test_all_three_actions_have_one_valid_shape() -> None:
    assert SQLGeneration(action="execute", sql="SELECT 1").action == "execute"
    assert (
        SQLGeneration(action="clarify", clarification_question="Which metric?").action == "clarify"
    )
    assert SQLGeneration(action="block", block_reason="Mutation is not allowed.").action == "block"
