from collections.abc import Mapping
from typing import Any

from app.data.gateway import DatabaseGateway, TableMetadata


class FakeDatabaseGateway(DatabaseGateway):
    def __init__(
        self,
        results_by_sql: Mapping[str, list[dict[str, Any]]] | None = None,
        *,
        strict_results: bool = False,
    ) -> None:
        self.executed_sql: list[str] = []
        self._results_by_sql = dict(results_by_sql or {})
        self._strict_results = strict_results

    async def health_check(self) -> bool:
        return True

    async def search_schema(self, question: str) -> list[TableMetadata]:
        del question
        return [
            TableMetadata(
                schema_name="analytics",
                table_name="departments",
                columns=["id", "name", "arabic_name", "cost_center"],
                description="Enterprise departments with English and Arabic names.",
            ),
            TableMetadata(
                schema_name="analytics",
                table_name="employees",
                columns=["id", "department_id", "full_name", "arabic_name", "status", "salary"],
                description="Synthetic employees, department assignments, status, and salary.",
            ),
            TableMetadata(
                schema_name="analytics",
                table_name="payroll",
                columns=[
                    "id",
                    "employee_id",
                    "period_start",
                    "period_end",
                    "base_salary",
                    "bonus",
                    "deductions",
                    "paid_at",
                    "status",
                ],
                description="Synthetic monthly payroll facts.",
            ),
            TableMetadata(
                schema_name="analytics",
                table_name="customers",
                columns=[
                    "id",
                    "customer_code",
                    "name",
                    "arabic_name",
                    "country_code",
                    "industry",
                    "status",
                ],
                description="Synthetic customers, countries, industries, and statuses.",
            ),
            TableMetadata(
                schema_name="analytics",
                table_name="projects",
                columns=[
                    "id",
                    "project_code",
                    "customer_id",
                    "owning_department_id",
                    "name",
                    "status",
                    "start_date",
                    "end_date",
                    "budget",
                ],
                description="Synthetic customer projects, dates, status, and budget.",
            ),
            TableMetadata(
                schema_name="analytics",
                table_name="employee_project_assignments",
                columns=[
                    "employee_id",
                    "project_id",
                    "assigned_from",
                    "assigned_to",
                    "allocation_percent",
                    "billable",
                ],
                description="Many-to-many employee project assignments and allocation.",
            ),
            TableMetadata(
                schema_name="analytics",
                table_name="invoices",
                columns=[
                    "id",
                    "invoice_number",
                    "customer_id",
                    "project_id",
                    "issued_on",
                    "due_on",
                    "status",
                    "currency",
                ],
                description="Synthetic customer and project invoice headers.",
            ),
            TableMetadata(
                schema_name="analytics",
                table_name="invoice_lines",
                columns=["id", "invoice_id", "description", "quantity", "unit_price"],
                description="Synthetic invoice lines used to calculate invoice totals.",
            ),
            TableMetadata(
                schema_name="analytics",
                table_name="project_costs",
                columns=["id", "project_id", "cost_date", "category", "amount", "description"],
                description="Synthetic dated project costs by category.",
            ),
        ]

    async def execute_readonly(self, sql: str) -> list[dict[str, Any]]:
        self.executed_sql.append(sql)
        if sql in self._results_by_sql:
            return [row.copy() for row in self._results_by_sql[sql]]
        if self._strict_results:
            raise ValueError("No deterministic fake result is configured for this SQL.")
        return [
            {
                "department": "Engineering",
                "employee_count": 4,
                "total_salary": "610000.00",
                "average_salary": "152500.00",
                "highest_paid_employee": "Maya Haddad",
            },
            {
                "department": "Sales",
                "employee_count": 3,
                "total_salary": "375000.00",
                "average_salary": "125000.00",
                "highest_paid_employee": "Noura Mansour",
            },
            {
                "department": "Finance",
                "employee_count": 2,
                "total_salary": "255000.00",
                "average_salary": "127500.00",
                "highest_paid_employee": "Omar Farouk",
            },
            {
                "department": "People Operations",
                "employee_count": 1,
                "total_salary": "135000.00",
                "average_salary": "135000.00",
                "highest_paid_employee": "Dalia Fawzi",
            },
        ]

    async def close(self) -> None:
        return None
