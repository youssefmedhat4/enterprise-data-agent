import pytest

from app.config import get_settings
from app.data.postgres import PostgresDatabaseGateway
from app.metrics.evaluation import evaluate_metric_gateway, load_metric_cases
from app.metrics.wren import MCPWrenCubeClient, WrenCubeMetricGateway

pytestmark = [pytest.mark.wren, pytest.mark.postgres]


async def test_wren_cube_metric_suite_against_readonly_postgres() -> None:
    settings = get_settings().model_copy(update={"database_provider": "postgres"})
    database = PostgresDatabaseGateway(settings)
    gateway = WrenCubeMetricGateway(
        MCPWrenCubeClient(
            settings.wren_mcp_url,
            timeout_seconds=settings.wren_timeout_seconds,
        ),
        database,
    )
    try:
        report = await evaluate_metric_gateway("wren", gateway, load_metric_cases())
    finally:
        await gateway.close()
        await database.close()

    assert report.failed == 0
