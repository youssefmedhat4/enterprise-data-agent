import json

from app.contracts.analytics import ClaimEvidence, GroundedClaim
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
            marker = "Current question: " if "Current question: " in user else "Question: "
            question = user.rsplit(marker, maxsplit=1)[-1]
            case = self._cases[question]
            if case.expected_security_behavior == "clarify":
                return response_model.model_validate(
                    {
                        "action": "clarify",
                        "explanation": "The request needs a governed metric or scope.",
                        "clarification_question": (
                            "Which metric, business scope, and time period should I use?"
                        ),
                    }
                )
            return response_model.model_validate(
                {
                    "action": (
                        "block" if case.expected_security_behavior == "block" else "execute"
                    ),
                    "sql": (
                        None if case.expected_security_behavior == "block" else case.reference_sql
                    ),
                    "explanation": f"Deterministic evaluation SQL for {case.id}.",
                    "block_reason": (
                        "The requested database mutation is not allowed."
                        if case.expected_security_behavior == "block"
                        else None
                    ),
                }
            )
        if response_model is AnswerGeneration:
            rows_json = user.split("Query results JSON:", maxsplit=1)[1].strip()
            rows = json.loads(rows_json)
            return response_model.model_validate(
                {
                    "answer": json.dumps(rows, ensure_ascii=False, default=str),
                    "claims": [
                        GroundedClaim(
                            claim=f"Query result row {row_index + 1} supports the answer.",
                            evidence=[
                                ClaimEvidence(
                                    row_index=row_index,
                                    field=field,
                                    value=value,
                                )
                                for field, value in row.items()
                            ],
                        )
                        for row_index, row in enumerate(rows)
                    ],
                    "chart": None,
                }
            )
        raise ValueError(f"Unsupported evaluation response model: {response_model.__name__}")
