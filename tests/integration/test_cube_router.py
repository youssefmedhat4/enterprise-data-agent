import pytest

from app.agent.graph import build_graph
from app.config import Settings
from app.data.fake import FakeDatabaseGateway
from app.llm.fake import FakeLLMGateway
from app.metrics.factory import build_metric_gateway
from app.security.sql_validation import SQLValidator


@pytest.mark.asyncio
@pytest.mark.cube
async def test_live_cube_full_router_path_uses_no_sql_reasoner() -> None:
    settings = Settings(METRIC_PROVIDER="cube")
    metrics = build_metric_gateway(settings)
    try:
        graph = build_graph(
            db_gateway=FakeDatabaseGateway(),
            llm_gateway=FakeLLMGateway(),
            sql_validator=SQLValidator(),
            metric_gateway=metrics,
            enable_query_router=True,
        )
        result = await graph.ainvoke(
            {
                "request_id": "cube-router-live",
                "trace_id": "cube-router-live",
                "thread_id": "cube-router-live",
                "question": "Total annual payroll by department",
            }
        )
    finally:
        await metrics.close()

    assert result["generated_sql"] is None
    assert result["internal_provenance"].route == "governed_metric"
    assert result["internal_provenance"].metric_provider == "cube"
    assert result["internal_provenance"].execution_source == "cube"
    assert result["internal_provenance"].model_aliases == ["analytics-general"]
