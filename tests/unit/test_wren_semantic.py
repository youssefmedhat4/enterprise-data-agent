from typing import Any

import pytest

from app.config import Settings
from app.data.schema_metadata import synthetic_enterprise_metadata
from app.semantic.factory import build_semantic_gateway
from app.semantic.gateway import SemanticProviderUnavailableError
from app.semantic.wren import WrenSemanticGateway, WrenSnapshot


class SnapshotClient:
    def __init__(self, snapshot: WrenSnapshot) -> None:
        self.snapshot = snapshot
        self.questions: list[str] = []

    async def retrieve(self, question: str) -> WrenSnapshot:
        self.questions.append(question)
        return self.snapshot


class UnavailableClient:
    async def retrieve(self, question: str) -> WrenSnapshot:
        del question
        raise SemanticProviderUnavailableError("unavailable")


def _model(
    name: str,
    *,
    aliases: list[str],
    columns: list[dict[str, Any]],
    definitions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "tableReference": {"schema": "analytics", "table": name},
        "columns": columns,
        "properties": {
            "description": f"Semantic {name} model.",
            "aliases": aliases,
            "definitions": definitions or [],
        },
    }


def _snapshot() -> WrenSnapshot:
    return WrenSnapshot(
        mdl={
            "models": [
                _model(
                    "departments",
                    aliases=["department", "departments"],
                    columns=[{"name": "id"}, {"name": "name"}],
                ),
                _model(
                    "employees",
                    aliases=["employee", "employees", "headcount"],
                    columns=[
                        {"name": "department_id"},
                        {
                            "name": "salary",
                            "properties": {"aliases": ["annual salary"]},
                        },
                    ],
                    definitions=[
                        {
                            "identifier": "annual_base_salary",
                            "name": "Annual base salary payroll",
                            "description": "Annual employee base-salary payroll.",
                            "expression": "SUM(analytics.employees.salary)",
                            "tables": ["analytics.employees", "analytics.departments"],
                            "aliases": ["payroll", "department payroll"],
                        }
                    ],
                ),
                _model(
                    "payroll",
                    aliases=["payroll", "monthly payroll"],
                    columns=[
                        {"name": "employee_id"},
                        {
                            "name": "net_amount",
                            "isCalculated": True,
                            "expression": "base_salary + bonus - deductions",
                            "properties": {
                                "description": "Net payroll amount.",
                                "aliases": ["net payroll"],
                            },
                        },
                    ],
                ),
                _model(
                    "customers",
                    aliases=["customer", "customers"],
                    columns=[{"name": "id"}, {"name": "name"}],
                ),
                _model(
                    "invoices",
                    aliases=["invoice", "invoices"],
                    columns=[{"name": "id"}, {"name": "customer_id"}],
                ),
                _model(
                    "invoice_lines",
                    aliases=["invoice amount", "invoice lines"],
                    columns=[
                        {"name": "invoice_id"},
                        {
                            "name": "line_amount",
                            "isCalculated": True,
                            "expression": "quantity * unit_price",
                            "properties": {
                                "description": "Extended invoice line amount.",
                                "aliases": ["invoice amount"],
                            },
                        },
                    ],
                ),
                _model(
                    "projects",
                    aliases=["project", "projects"],
                    columns=[{"name": "id"}, {"name": "budget"}],
                    definitions=[
                        {
                            "identifier": "budget_utilization",
                            "name": "Project budget utilization",
                            "description": "Project cost divided by project budget.",
                            "expression": "SUM(project_costs.amount) / projects.budget",
                            "tables": ["analytics.projects", "analytics.project_costs"],
                            "aliases": ["budget utilization"],
                        }
                    ],
                ),
                _model(
                    "project_costs",
                    aliases=["project cost", "project costs"],
                    columns=[{"name": "project_id"}, {"name": "amount"}],
                ),
            ],
            "relationships": [
                {
                    "name": "employees_departments",
                    "models": ["employees", "departments"],
                    "joinType": "MANY_TO_ONE",
                    "condition": "employees.department_id = departments.id",
                },
                {
                    "name": "payroll_employees",
                    "models": ["payroll", "employees"],
                    "joinType": "MANY_TO_ONE",
                    "condition": "payroll.employee_id = employees.id",
                },
                {
                    "name": "invoices_customers",
                    "models": ["invoices", "customers"],
                    "joinType": "MANY_TO_ONE",
                    "condition": "invoices.customer_id = customers.id",
                },
                {
                    "name": "invoice_lines_invoices",
                    "models": ["invoice_lines", "invoices"],
                    "joinType": "MANY_TO_ONE",
                    "condition": "invoice_lines.invoice_id = invoices.id",
                },
                {
                    "name": "project_costs_projects",
                    "models": ["project_costs", "projects"],
                    "joinType": "MANY_TO_ONE",
                    "condition": "project_costs.project_id = projects.id",
                },
            ],
        },
        retrieval={
            "strategy": "full",
            "schema": "bounded by the adapter",
            "results": [
                {"model_name": name}
                for name in (
                    "departments",
                    "employees",
                    "payroll",
                    "customers",
                    "invoices",
                    "invoice_lines",
                )
            ],
        },
        instructions="Synthetic business rules.",
    )


@pytest.mark.asyncio
async def test_wren_selects_payroll_relationship_path_and_definition() -> None:
    client = SnapshotClient(_snapshot())
    gateway = WrenSemanticGateway(client)

    context = await gateway.retrieve_context(
        question="Which department has the highest payroll?",
        available_tables=synthetic_enterprise_metadata(),
        prior_context=None,
    )

    assert client.questions == ["Which department has the highest payroll?"]
    assert set(context.table_ids) == {
        "analytics.departments",
        "analytics.employees",
        "analytics.payroll",
    }
    assert "wren:annual_base_salary" in context.definition_ids
    assert "wren:enterprise_analytics:employees_departments" in context.relationship_ids
    assert context.provider == "wren"
    assert context.context_size_chars > 0


@pytest.mark.asyncio
async def test_wren_selects_customer_invoice_join_and_calculated_field() -> None:
    gateway = WrenSemanticGateway(SnapshotClient(_snapshot()))

    context = await gateway.retrieve_context(
        question="Which customer has the highest invoice amount?",
        available_tables=synthetic_enterprise_metadata(),
        prior_context=None,
    )

    assert set(context.table_ids) == {
        "analytics.customers",
        "analytics.invoices",
        "analytics.invoice_lines",
    }
    assert "wren:invoice_lines.line_amount" in context.measure_ids
    assert len(context.tables) < len(synthetic_enterprise_metadata())


@pytest.mark.asyncio
async def test_wren_includes_tables_declared_by_selected_definition() -> None:
    gateway = WrenSemanticGateway(SnapshotClient(_snapshot()))

    context = await gateway.retrieve_context(
        question="Show project budget utilization.",
        available_tables=synthetic_enterprise_metadata(),
        prior_context=None,
    )

    assert set(context.table_ids) == {"analytics.projects", "analytics.project_costs"}
    assert "wren:budget_utilization" in context.definition_ids
    assert "wren:enterprise_analytics:project_costs_projects" in context.relationship_ids
    assert context.selection_reasons["analytics.project_costs"] == ("wren_semantic_dependency",)


@pytest.mark.asyncio
async def test_wren_unavailability_does_not_fall_back() -> None:
    gateway = WrenSemanticGateway(UnavailableClient())

    with pytest.raises(SemanticProviderUnavailableError):
        await gateway.retrieve_context(
            question="department payroll",
            available_tables=synthetic_enterprise_metadata(),
            prior_context=None,
        )


def test_semantic_factory_selects_configured_provider() -> None:
    assert build_semantic_gateway(Settings(SEMANTIC_PROVIDER="inmemory")).__class__.__name__ == (
        "InMemorySemanticGateway"
    )
    assert build_semantic_gateway(Settings(SEMANTIC_PROVIDER="wren")).__class__.__name__ == (
        "WrenSemanticGateway"
    )
