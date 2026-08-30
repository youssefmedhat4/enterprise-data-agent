from app.config import Settings
from app.data.gateway import DatabaseGateway
from app.metrics.cube import CubeMetricGateway, HTTPCubeClient
from app.metrics.gateway import MetricGateway, MetricProviderUnavailableError
from app.metrics.wren import MCPWrenCubeClient, WrenCubeMetricGateway


def build_metric_gateway(
    settings: Settings,
    *,
    database: DatabaseGateway | None = None,
) -> MetricGateway:
    """Build the configured governed `MetricGateway`.

    Selection is explicit through `METRIC_PROVIDER` and never falls back to
    another provider. See ADR 0011 for why Wren, not Cube, is the default.
    """
    if settings.metric_provider == "wren":
        if database is None:
            raise MetricProviderUnavailableError(
                "The Wren governed metric provider requires a configured DatabaseGateway."
            )
        return build_wren_metric_gateway(settings, database=database)

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


def build_wren_metric_gateway(
    settings: Settings,
    *,
    database: DatabaseGateway,
) -> WrenCubeMetricGateway:
    """Build the Wren governed-metric gateway.

    Wren translates a structured `MetricQuery` into SQL only; it never receives
    database credentials or executes a query itself. `DatabaseGateway` runs the
    revalidated SQL through the existing physically read-only role.
    """
    return WrenCubeMetricGateway(
        MCPWrenCubeClient(
            settings.wren_mcp_url,
            timeout_seconds=settings.wren_timeout_seconds,
        ),
        database,
    )
