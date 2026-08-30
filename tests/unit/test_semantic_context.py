import pytest

from app.data.schema_metadata import synthetic_enterprise_metadata
from app.semantic.in_memory import InMemorySemanticGateway


@pytest.mark.asyncio
async def test_selector_uses_lexical_matches_and_relationship_bridges() -> None:
    context = await InMemorySemanticGateway().retrieve_context(
        question="List project assignments with employee name and allocation percentage.",
        available_tables=synthetic_enterprise_metadata(),
        prior_context=None,
    )

    assert set(context.table_ids) == {
        "analytics.employee_project_assignments",
        "analytics.employees",
        "analytics.projects",
    }
    assert len(context.table_ids) < len(synthetic_enterprise_metadata())


@pytest.mark.asyncio
async def test_context_contains_fixture_enum_values_and_business_definition() -> None:
    context = await InMemorySemanticGateway().retrieve_context(
        question="Count active employees by department.",
        available_tables=synthetic_enterprise_metadata(),
        prior_context=None,
    )

    employee = next(table for table in context.tables if table.table_name == "employees")
    status = next(column for column in employee.column_metadata if column.name == "status")
    assert status.observed_values == ("active", "leave", "terminated")
    assert "active_employee" in context.definition_ids


@pytest.mark.asyncio
async def test_bare_revenue_does_not_invent_a_business_definition() -> None:
    context = await InMemorySemanticGateway().retrieve_context(
        question="Show revenue.",
        available_tables=synthetic_enterprise_metadata(),
        prior_context=None,
    )

    assert context.definition_ids == []
    assert context.table_ids == []


@pytest.mark.asyncio
async def test_multi_fact_context_describes_grain_and_independent_measure_scopes() -> None:
    context = await InMemorySemanticGateway().retrieve_context(
        question=(
            "For each department show active employee count, annual base payroll, average "
            "employee salary, project cost, invoiced amount, and project margin."
        ),
        available_tables=synthetic_enterprise_metadata(),
        prior_context=None,
    )

    definitions = {definition.identifier: definition for definition in context.definitions}
    assert {
        "active_employee",
        "annual_base_salary",
        "average_employee_salary",
        "invoice_amount",
        "project_cost",
        "project_margin",
    }.issubset(definitions)
    assert "not implicitly limited to active" in definitions["annual_base_salary"].description
    assert "does not automatically apply" in definitions["average_employee_salary"].description
    assert "independent one-to-many fact sources" in definitions["project_margin"].description
