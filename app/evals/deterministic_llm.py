import json

from app.evals.models import EvaluationCase
from app.llm.gateway import AnswerGeneration, LLMGateway, ResponseModelT, SQLGeneration


class DeterministicEvaluationLLM(LLMGateway):
    def __init__(self, cases: list[EvaluationCase]) -> None:
        self._cases = {case.question: case for case in cases}

    async def generate_structured(
        self,
        *,
        model_alias: str,
        system: str,
        user: str,
        response_model: type[ResponseModelT],
    ) -> ResponseModelT:
        del model_alias, system
        if response_model is SQLGeneration:
            question = user.rsplit("Question: ", maxsplit=1)[-1]
            case = self._cases[question]
            if case.expected_security_behavior == "clarify":
                return response_model.model_validate(
                    {
                        "explanation": "The request needs a governed metric or scope.",
                        "needs_clarification": True,
                        "clarification_question": (
                            "Which metric, business scope, and time period should I use?"
                        ),
                    }
                )
            return response_model.model_validate(
                {
                    "sql": case.reference_sql,
                    "explanation": f"Deterministic evaluation SQL for {case.id}.",
                    "needs_clarification": False,
                }
            )
        if response_model is AnswerGeneration:
            rows_json = user.split("Query results JSON:", maxsplit=1)[1].strip()
            rows = json.loads(rows_json)
            return response_model.model_validate(
                {
                    "answer": json.dumps(rows, ensure_ascii=False, default=str),
                    "chart": None,
                }
            )
        raise ValueError(f"Unsupported evaluation response model: {response_model.__name__}")
