import json
from typing import Any

from app.contracts.analytics import ClaimEvidence, GroundedClaim
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
HIGHEST_PAYROLL_QUESTION = "which department has the highest payroll"
LAST_YEAR_FOLLOW_UP = "what about last year"


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
            sql, analysis = self._sql_for_question(user)
            return response_model.model_validate(
                {
                    "action": "execute",
                    "sql": sql,
                    "explanation": "Deterministic SQL for a supported payroll question.",
                    "analysis": analysis,
                }
            )
        if response_model is AnswerGeneration:
            return response_model.model_validate(self._answer_from_rows(user))
        raise ValueError(f"Unsupported fake response model: {response_model.__name__}")

    def _sql_for_question(self, prompt: str) -> tuple[str, dict[str, Any]]:
        normalized_prompt = prompt.lower()
        current_question = normalized_prompt.rsplit("current question:", maxsplit=1)[-1].strip()
        if (
            LAST_YEAR_FOLLOW_UP in current_question
            and '"metric":"total_payroll"' in normalized_prompt
        ):
            return self._highest_payroll_sql(last_year=True), {
                "intent": "rank_departments_by_payroll",
                "metric": "total_payroll",
                "dimensions": ["department"],
                "time_range": {
                    "start": "2025-01-01",
                    "end": "2025-12-31",
                    "label": "last year",
                },
            }
        if HIGHEST_PAYROLL_QUESTION in current_question:
            return self._highest_payroll_sql(last_year=False), {
                "intent": "rank_departments_by_payroll",
                "metric": "total_payroll",
                "dimensions": ["department"],
            }
        supported_questions = (
            FIRST_VERTICAL_SLICE_QUESTION,
            ARABIC_VERTICAL_SLICE_QUESTION,
            MIXED_VERTICAL_SLICE_QUESTION,
        )
        if not any(question in normalized_prompt for question in supported_questions):
            raise ValueError("Fake LLM only supports the first vertical-slice question.")
        return (
            """
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
        """,
            {
                "intent": "department_payroll_summary",
                "metric": "total_salary",
                "dimensions": ["department"],
                "filters": {"employee_status": "active"},
            },
        )

    def _highest_payroll_sql(self, *, last_year: bool) -> str:
        date_filter = ""
        if last_year:
            date_filter = (
                "WHERE p.period_start >= DATE '2025-01-01' AND p.period_start < DATE '2026-01-01'"
            )
        return f"""
            SELECT
                d.name AS department,
                SUM(p.base_salary + p.bonus - p.deductions)::numeric(14, 2)
                    AS total_payroll
            FROM analytics.departments d
            JOIN analytics.employees e ON e.department_id = d.id
            JOIN analytics.payroll p ON p.employee_id = e.id
            {date_filter}
            GROUP BY d.name
            ORDER BY total_payroll DESC, department ASC
            LIMIT 1
        """

    def _answer_from_rows(self, prompt: str) -> dict[str, Any]:
        marker = "Query results JSON:"
        rows_json = prompt.split(marker, maxsplit=1)[1].strip()
        rows = json.loads(rows_json)
        if not rows:
            return {
                "answer": "No matching department payroll rows were returned.",
                "claims": [],
                "chart": None,
            }

        claims = self._claims_for_rows(rows)
        if {"department", "total_payroll"}.issubset(rows[0]):
            row = rows[0]
            return {
                "answer": (
                    f"{row['department']} has the highest payroll at "
                    f"{self._money(row['total_payroll'])}."
                ),
                "claims": [claims[0]],
                "chart": None,
            }
        if {"department", "annual_base_payroll"}.issubset(rows[0]):
            summaries = [
                f"{row['department']}: annual base payroll "
                f"{self._money(row['annual_base_payroll'])}"
                for row in rows
            ]
            return {
                "answer": "; ".join(summaries) + ".",
                "claims": claims,
                "chart": {
                    "chart_type": "bar",
                    "title": "Annual Base Payroll by Department",
                    "x": "department",
                    "measures": ["annual_base_payroll"],
                    "orientation": "horizontal",
                    "sort": "descending",
                    "value_format": "currency",
                },
            }

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
            "claims": claims,
            "chart": {
                "chart_type": "bar",
                "title": "Total Salary by Department",
                "x": "department",
                "measures": ["total_salary"],
                "orientation": "horizontal",
                "sort": "descending",
                "value_format": "currency",
            },
        }

    def _claims_for_rows(self, rows: list[dict[str, Any]]) -> list[GroundedClaim]:
        return [
            GroundedClaim(
                claim=f"Query result row {row_index + 1} supports the answer.",
                evidence=[
                    ClaimEvidence(row_index=row_index, field=field, value=value)
                    for field, value in row.items()
                ],
            )
            for row_index, row in enumerate(rows)
        ]

    def _money(self, value: str | int | float) -> str:
        return f"${float(value):,.2f}"
