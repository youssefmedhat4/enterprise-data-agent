import pytest

from app.agent.graph import build_graph
from app.config import Settings
from app.data.fake import FakeDatabaseGateway
from app.data.schema_metadata import synthetic_enterprise_metadata
from app.llm.fake import FakeLLMGateway
from app.security.sql_validation import SQLValidator
from app.semantic.wren import MCPWrenContextClient, WrenSemanticGateway

QUESTION = "Which department has the highest payroll?"


@pytest.mark.wren
@pytest.mark.asyncio
async def test_live_wren_context_and_graph_provenance() -> None:
    settings = Settings(SEMANTIC_PROVIDER="wren")
    gateway = WrenSemanticGateway(
        MCPWrenContextClient(
            settings.wren_mcp_url,
            timeout_seconds=settings.wren_timeout_seconds,
        ),
        max_models=settings.wren_max_context_models,
        project_id=settings.wren_project_id,
    )
    context = await gateway.retrieve_context(
        question=QUESTION,
        available_tables=synthetic_enterprise_metadata(),
        prior_context=None,
    )

    assert context.provider == "wren"
    assert {"analytics.departments", "analytics.employees"}.issubset(context.table_ids)
    assert any(
        identifier.endswith(":employees_departments") for identifier in context.relationship_ids
    )
    assert "wren:annual_base_salary" in context.definition_ids

    graph = build_graph(
        db_gateway=FakeDatabaseGateway(),
        llm_gateway=FakeLLMGateway(),
        sql_validator=SQLValidator(),
        semantic_gateway=gateway,
    )
    result = await graph.ainvoke(
        {
            "request_id": "wren-live",
            "trace_id": "wren-live",
            "thread_id": None,
            "question": (
                "Show each department, its number of employees, total salary, average salary, "
                "and highest paid employee, ordered by total payroll."
            ),
        }
    )

    provenance = result["internal_provenance"]
    assert provenance.semantic_provider == "wren"
    assert provenance.semantic_retrieval_latency_ms >= 0
    assert provenance.semantic_model_ids
    assert provenance.semantic_relationship_ids
    assert provenance.sql_generation_provider == "llm"
