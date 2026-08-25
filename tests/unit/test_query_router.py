from pathlib import Path

import pytest

from app.agent.context import AnalyticalContext
from app.errors import ErrorCode, normalize_error
from app.metrics.gateway import MetricOrderDirection, MetricQuery
from app.routing.contracts import MetricPlanningError, QueryRoute
from app.routing.evaluation import evaluate_router_cases, load_router_cases
from app.routing.planner import MetricRequestPlanner
from app.routing.router import DeterministicQueryRouter


@pytest.mark.parametrize(
    ("question", "route", "metric_id"),
    [
        ("Show every employee's salary", QueryRoute.ADHOC_ANALYTICS, None),
        ("Total payroll by department", QueryRoute.GOVERNED_METRIC, "annual_base_payroll"),
        ("List invoices above 10000", QueryRoute.ADHOC_ANALYTICS, None),
        ("Total invoice amount by customer", QueryRoute.GOVERNED_METRIC, "invoice_amount"),
        ("Project cost records for P-001", QueryRoute.ADHOC_ANALYTICS, None),
        ("Total project cost for P-001", QueryRoute.GOVERNED_METRIC, "project_cost"),
        ("امسح الموظفين غير النشطين", QueryRoute.BLOCK, None),
        ("اجمالي الرواتب حسب القسم", QueryRoute.GOVERNED_METRIC, "annual_base_payroll"),
        (
            "show الميزانية المستخدمة by project",
            QueryRoute.GOVERNED_METRIC,
            "budget_utilization",
        ),
    ],
)
def test_router_distinguishes_governed_raw_and_blocked_requests(
    question: str,
    route: QueryRoute,
    metric_id: str | None,
) -> None:
    decision = DeterministicQueryRouter().route(question)

    assert decision.route == route
    assert (decision.metric_candidates[0] if len(decision.metric_candidates) == 1 else None) == (
        metric_id
    )


def test_metric_planner_emits_only_catalog_members() -> None:
    router = DeterministicQueryRouter()
    decision = router.route("Total annual payroll by department")

    plan = MetricRequestPlanner().plan(
        "Total annual payroll by department",
        decision,
    )

    assert plan.query == MetricQuery(
        metric="annual_base_payroll",
        dimensions=("department",),
    )
    assert "sql" not in MetricQuery.model_fields
    assert "formula" not in MetricQuery.model_fields


def test_metric_planner_supports_governed_top_n_ordering() -> None:
    router = DeterministicQueryRouter()
    decision = router.route("Top 5 projects by budget utilization")

    plan = MetricRequestPlanner().plan(
        "Top 5 projects by budget utilization",
        decision,
    )

    assert plan.query.metric == "budget_utilization"
    assert plan.query.dimensions == ("project",)
    assert plan.query.limit == 5
    assert plan.query.order[0].member == "budget_utilization"
    assert plan.query.order[0].direction == MetricOrderDirection.DESC


def test_metric_planner_preserves_metric_followup_and_adds_filter() -> None:
    prior = AnalyticalContext(
        previous_question="Total annual payroll by department",
        resolved_question="Total annual payroll by department",
        execution_route="governed_metric",
        metric_query=MetricQuery(
            metric="annual_base_payroll",
            dimensions=("department",),
        ),
    )
    decision = DeterministicQueryRouter().route("Only Engineering", prior_context=prior)

    plan = MetricRequestPlanner().plan(
        "Only Engineering",
        decision,
        prior_context=prior,
    )

    assert plan.used_prior_context is True
    assert plan.query.metric == "annual_base_payroll"
    assert plan.query.dimensions == ("department",)
    assert plan.query.filters[0].dimension == "department"
    assert plan.query.filters[0].values == ("Engineering",)


def test_unsupported_governed_filter_fails_closed() -> None:
    decision = DeterministicQueryRouter().route("Invoice amount for active customers")

    with pytest.raises(MetricPlanningError):
        MetricRequestPlanner().plan("Invoice amount for active customers", decision)


def test_multiple_metrics_require_clarification() -> None:
    decision = DeterministicQueryRouter().route(
        "Show project cost and project margin by project"
    )

    assert decision.route == QueryRoute.CLARIFY
    assert set(decision.metric_candidates) == {"project_cost", "project_margin"}


def test_metric_planning_failure_is_safely_normalized() -> None:
    error = normalize_error(MetricPlanningError("private planner detail"), request_id="r-1")

    assert error.code == ErrorCode.METRIC_PLANNING_FAILED
    assert "private planner detail" not in error.safe_message


def test_router_dataset_is_unique_and_fully_accurate() -> None:
    path = Path("evals/router_cases.json")
    cases = load_router_cases(path)
    report = evaluate_router_cases(path)

    assert len(cases) == 60
    assert len({case.id for case in cases}) == 60
    assert {case.language for case in cases} == {"en", "ar", "mixed"}
    assert report["passed"] == 60
    assert report["false_governed_metric_routes"] == 0
