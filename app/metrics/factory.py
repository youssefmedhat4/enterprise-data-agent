from app.config import Settings
from app.data.gateway import DatabaseGateway
from app.metrics.cube import CubeMetricGateway, HTTPCubeClient
from app.metrics.gateway import MetricGateway
from app.metrics.wren import MCPWrenCubeClient, WrenCubeMetricGateway


def build_metric_gateway(
    settings: Settings,
    *,
    database: DatabaseGateway | None = None,
) -> MetricGateway:
    del database
    token = (
        settings.cube_api_token.get_secret_value()
        if settings.cube_api_token is not None
        else None
    )
    return CubeMetricGateway(
        HTTPCubeClient(
            settings.cube_api_url,
            timeout_seconds=settings.cube_timeout_seconds,
            api_token=token,
        )
    )


def build_experimental_wren_metric_gateway(
    settings: Settings,
    *,
    database: DatabaseGateway,
) -> WrenCubeMetricGateway:
    """Build the frozen Wren metric experiment, not a production provider route."""
    return WrenCubeMetricGateway(
        MCPWrenCubeClient(
            settings.wren_mcp_url,
            timeout_seconds=settings.wren_timeout_seconds,
        ),
        database,
    )
