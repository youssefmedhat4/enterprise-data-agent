import json
from typing import Any

from app.llm.gateway import (
    AnswerGeneration,
    LLMGateway,
    ResponseModelT,
    SQLGeneration,
)

FIRST_VERTICAL_SLICE_QUESTION = (
    "show each department, its number of employees, total salary, average salary, "
    "and highest paid employee, ordered by total payroll"
)
ARABIC_VERTICAL_SLICE_QUESTION = (
    "اعرض كل قسم، وعدد الموظفين فيه، وإجمالي الرواتب، ومتوسط الراتب، والموظف الأعلى راتباً، "
    "مرتبة حسب إجمالي الرواتب"
)
MIXED_VERTICAL_SLICE_QUESTION = (
    "show each department مع عدد الموظفين وإجمالي الرواتب ومتوسط الراتب وأعلى موظف راتباً، "
    "ordered by total payroll"
)


class FakeLLMGateway(LLMGateway):
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
            return response_model.model_validate(
                {
                    "sql": self._sql_for_question(user),
                    "explanation": (
                        "Deterministic SQL for the first payroll analytics question."
                    ),
                    "needs_clarification": False,
                }
            )
        if response_model is AnswerGeneration:
            return response_model.model_validate(self._answer_from_rows(user))
        raise ValueError(f"Unsupported fake response model: {response_model.__name__}")

    def _sql_for_question(self, prompt: str) -> str:
        normalized_prompt = prompt.lower()
        supported_questions = (
            FIRST_VERTICAL_SLICE_QUESTION,
            ARABIC_VERTICAL_SLICE_QUESTION,
            MIXED_VERTICAL_SLICE_QUESTION,
        )
        if not any(question in normalized_prompt for question in supported_questions):
            raise ValueError("Fake LLM only supports the first vertical-slice question.")
        return """
            WITH ranked_employees AS (
                SELECT
                    d.id AS department_id,
                    d.name AS department,
                    e.full_name AS employee_name,
                    e.salary,
                    ROW_NUMBER() OVER (
                        PARTITION BY d.id
                        ORDER BY e.salary DESC, e.full_name ASC
                    ) AS salary_rank
                FROM analytics.departments d
                JOIN analytics.employees e ON e.department_id = d.id
                WHERE e.status = 'active'
            )
            SELECT
                department,
                COUNT(*) AS employee_count,
                SUM(salary)::numeric(12, 2) AS total_salary,
                AVG(salary)::numeric(12, 2) AS average_salary,
                MAX(employee_name) FILTER (WHERE salary_rank = 1) AS highest_paid_employee
            FROM ranked_employees
            GROUP BY department
            ORDER BY total_salary DESC, department ASC
            LIMIT 100
        """

    def _answer_from_rows(self, prompt: str) -> dict[str, Any]:
        marker = "Query results JSON:"
        rows_json = prompt.split(marker, maxsplit=1)[1].strip()
        rows = json.loads(rows_json)
        if not rows:
            return {"answer": "No matching department payroll rows were returned.", "chart": None}

        summaries = [
            (
                f"{row['department']}: {row['employee_count']} employees, "
                f"total salary {self._money(row['total_salary'])}, average salary "
                f"{self._money(row['average_salary'])}, highest paid employee "
                f"{row['highest_paid_employee']}"
            )
            for row in rows
        ]
        answer = "; ".join(summaries) + "."
        return {
            "answer": answer,
            "chart": {
                "chart_type": "bar",
                "title": "Total Salary by Department",
                "x": "department",
                "y": "total_salary",
                "series": None,
            },
        }

    def _money(self, value: str | int | float) -> str:
        return f"${float(value):,.2f}"
