"""A reply to a clarification continues the request that prompted it.

The failure this guards against is quiet: the user is asked which of two
organisational units they meant, answers "the one with code OU2100", and that
answer arrives as a brand new question with no payroll in it. The original
request is abandoned and the user is told the schema has no payroll data.
"""

from __future__ import annotations

from typing import Any, cast

from app.agent.context import AnalyticalContext, EntityChoice, PendingEntityChoice
from app.agent.graph import _resume_clarified_request
from app.agent.state import AgentState


def _pending() -> PendingEntityChoice:
    return PendingEntityChoice(
        entity_name="Organizational Unit",
        original_question="Show payroll for Operations.",
        choices=[
            EntityChoice(
                canonical_key="OU2100",
                display_value="Operations",
                canonical_column="erp.org_unit_lkp.org_cd",
                display_column="erp.org_unit_lkp.org_nm",
            ),
            EntityChoice(
                canonical_key="OU2200",
                display_value="Operations",
                canonical_column="erp.org_unit_lkp.org_cd",
                display_column="erp.org_unit_lkp.org_nm",
            ),
        ],
    )


def _state(question: str, context: AnalyticalContext | None) -> AgentState:
    payload: dict[str, Any] = {"question": question}
    if context is not None:
        payload["analytical_context"] = context
    return cast(AgentState, payload)


def _context(
    *, clarifying: bool, pending: PendingEntityChoice | None
) -> AnalyticalContext:
    return AnalyticalContext(
        previous_question="Show payroll for Operations.",
        resolved_question="Show payroll for Operations.",
        clarification_state="required" if clarifying else "none",
        pending_entity_choice=pending,
    )


def test_naming_one_offered_option_resumes_the_original_request() -> None:
    resumed, pinned = _resume_clarified_request(
        _state(
            "the one with code OU2100",
            _context(clarifying=True, pending=_pending()),
        )
    )

    assert resumed == "Show payroll for Operations.", (
        "the reply replaced the request instead of answering it"
    )
    assert pinned == [
        {
            "entity": "Organizational Unit",
            "canonical_column": "erp.org_unit_lkp.org_cd",
            "canonical_key": "OU2100",
            "display_column": "erp.org_unit_lkp.org_nm",
            "display_value": "Operations",
        }
    ]


def test_a_bare_canonical_key_is_also_a_reply() -> None:
    resumed, pinned = _resume_clarified_request(
        _state("OU2200", _context(clarifying=True, pending=_pending()))
    )

    assert resumed == "Show payroll for Operations."
    assert pinned[0]["canonical_key"] == "OU2200"


def test_a_new_question_inherits_nothing() -> None:
    """The user ignored the clarification and asked something else."""
    resumed, pinned = _resume_clarified_request(
        _state(
            "How many active employees do we have?",
            _context(clarifying=True, pending=_pending()),
        )
    )

    assert resumed == "How many active employees do we have?"
    assert pinned == []


def test_repeating_the_ambiguous_label_stays_ambiguous() -> None:
    """Answering "Operations" again names both options, so nothing is chosen."""
    resumed, pinned = _resume_clarified_request(
        _state("Operations", _context(clarifying=True, pending=_pending()))
    )

    assert resumed == "Operations"
    assert pinned == []


def test_nothing_is_inherited_when_the_prior_turn_did_not_clarify() -> None:
    resumed, pinned = _resume_clarified_request(
        _state("OU2100", _context(clarifying=False, pending=_pending()))
    )

    assert resumed == "OU2100"
    assert pinned == []


def test_a_first_turn_has_nothing_to_resume() -> None:
    resumed, pinned = _resume_clarified_request(_state("OU2100", None))

    assert resumed == "OU2100"
    assert pinned == []


def test_a_clarification_without_recorded_options_cannot_resume() -> None:
    """Guards the ordering: options are recorded when the question is asked."""
    resumed, pinned = _resume_clarified_request(
        _state("OU2100", _context(clarifying=True, pending=None))
    )

    assert resumed == "OU2100"
    assert pinned == []
