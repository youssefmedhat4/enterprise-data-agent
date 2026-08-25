import pytest

from app.config import get_settings
from app.metrics.cube import CubeMetricGateway, HTTPCubeClient
from app.metrics.evaluation import evaluate_metric_gateway, load_metric_cases

pytestmark = pytest.mark.cube


async def test_cube_metric_suite_against_readonly_postgres() -> None:
    settings = get_settings()
    gateway = CubeMetricGateway(
        HTTPCubeClient(
            settings.cube_api_url,
            timeout_seconds=settings.cube_timeout_seconds,
        )
    )
    try:
        report = await evaluate_metric_gateway("cube", gateway, load_metric_cases())
    finally:
        await gateway.close()

    assert report.failed == 0
