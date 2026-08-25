import pytest

from app.authentication.gateway import UserIdentity
from app.authorization.gateway import build_authorization_request
from app.authorization.opa import OPAAuthorizationGateway
from app.config import Settings
from app.data.fake import FakeDatabaseGateway
from app.metrics.catalog import GOVERNED_METRICS


@pytest.mark.opa
@pytest.mark.asyncio
async def test_live_opa_filters_columns_metrics_and_debug_capability() -> None:
    settings = Settings(AUTHORIZATION_PROVIDER="opa")
    database = FakeDatabaseGateway()
    tables = await database.search_schema("employees payroll")
    gateway = OPAAuthorizationGateway(
        base_url=settings.opa_url,
        decision_path=settings.opa_decision_path,
        timeout_seconds=settings.opa_timeout_seconds,
    )
    metrics = tuple(metric.id for metric in GOVERNED_METRICS)
    try:
        analyst = await gateway.authorize(
            build_authorization_request(
                identity=UserIdentity(
                    subject_id="live-analyst",
                    roles=("analyst",),
                    provider="test",
                ),
                tables=tables,
                metrics=metrics,
            )
        )
        admin = await gateway.authorize(
            build_authorization_request(
                identity=UserIdentity(
                    subject_id="live-admin",
                    roles=("admin_analytics",),
                    provider="test",
                ),
                tables=tables,
                metrics=metrics,
            )
        )
    finally:
        await gateway.close()
        await database.close()

    assert "salary" not in analyst.table_columns["analytics.employees"]
    assert "annual_base_payroll" not in analyst.allowed_metrics
    assert analyst.debug_allowed is False
    assert "salary" in admin.table_columns["analytics.employees"]
    assert "annual_base_payroll" in admin.allowed_metrics
    assert admin.debug_allowed is True
