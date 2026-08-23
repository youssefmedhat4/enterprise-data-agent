import json
from pathlib import Path

from pydantic import TypeAdapter

from app.evals.models import EvaluationCase


def load_evaluation_cases(path: Path) -> list[EvaluationCase]:
    raw_cases = json.loads(path.read_text(encoding="utf-8"))
    return TypeAdapter(list[EvaluationCase]).validate_python(raw_cases)
