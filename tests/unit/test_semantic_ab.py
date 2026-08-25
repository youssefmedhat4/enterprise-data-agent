from pathlib import Path

import pytest

from app.evals.models import EvaluationSummary
from app.evals.semantic_ab import _pairwise, _validate_experiment, render_semantic_ab

ROOT = Path(__file__).parents[2]


def _load(name: str) -> EvaluationSummary:
    return EvaluationSummary.model_validate_json(
        (ROOT / "artifacts" / name).read_text(encoding="utf-8")
    )


def test_frozen_semantic_ab_artifacts_form_valid_pairwise_experiment() -> None:
    inmemory = _load("ab-inmemory-gemini25-flash.json")
    wren = _load("ab-wren-gemini25-flash.json")

    _validate_experiment(inmemory, wren)
    pairs = _pairwise(inmemory, wren)

    assert len(pairs) == 50
    assert [case_id for case_id, _, _ in pairs] == [result.case_id for result in inmemory.results]
    assert sum(classification == "WREN_ONLY_PASS" for _, classification, _ in pairs) == 2
    assert sum(classification == "INMEMORY_ONLY_PASS" for _, classification, _ in pairs) == 0
    assert "**KEEP_BOTH_WREN_OPTIONAL**" in render_semantic_ab(inmemory, wren)


def test_semantic_ab_rejects_model_configuration_drift() -> None:
    inmemory = _load("ab-inmemory-gemini25-flash.json")
    wren = _load("ab-wren-gemini25-flash.json").model_copy(
        update={"configured_models": {"sql-reasoner": "different/model"}}
    )

    with pytest.raises(ValueError, match="configured model aliases differ"):
        _validate_experiment(inmemory, wren)
