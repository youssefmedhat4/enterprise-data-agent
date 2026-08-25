from __future__ import annotations

from app.metrics.gateway import (
    MetricDefinition,
    MetricDimensionDefinition,
    MetricFilterOperator,
    MetricQuery,
    MetricQueryValidationError,
    MetricTimeDimensionDefinition,
)

TEXT_OPERATORS = (
    MetricFilterOperator.EQ,
    MetricFilterOperator.NEQ,
    MetricFilterOperator.IN,
    MetricFilterOperator.NOT_IN,
    MetricFilterOperator.CONTAINS,
    MetricFilterOperator.STARTS_WITH,
    MetricFilterOperator.IS_NULL,
    MetricFilterOperator.IS_NOT_NULL,
)
NUMBER_OPERATORS = (
    MetricFilterOperator.EQ,
    MetricFilterOperator.NEQ,
    MetricFilterOperator.GT,
    MetricFilterOperator.GTE,
    MetricFilterOperator.LT,
    MetricFilterOperator.LTE,
    MetricFilterOperator.IS_NULL,
    MetricFilterOperator.IS_NOT_NULL,
)


def _text_dimension(
    identifier: str,
    description: str,
    *aliases: str,
) -> MetricDimensionDefinition:
    return MetricDimensionDefinition(
        id=identifier,
        description=description,
        data_type="string",
        aliases=aliases,
        allowed_operators=TEXT_OPERATORS,
    )


DEPARTMENT = _text_dimension(
    "department", "English department name.", "department", "dept", "القسم", "الاقسام"
)
EMPLOYMENT_STATUS = _text_dimension(
    "employment_status", "Lowercase employment status.", "employment status", "حالة الموظف"
)
PAYROLL_STATUS = _text_dimension(
    "payroll_status", "Lowercase payroll status.", "payroll status", "حالة الراتب"
)
CUSTOMER = _text_dimension("customer", "English customer name.", "customer", "client", "العميل")
INVOICE_STATUS = _text_dimension(
    "invoice_status", "Lowercase invoice status.", "invoice status", "حالة الفاتورة"
)
PROJECT = _text_dimension("project", "Project name.", "project", "المشروع")
COST_CATEGORY = _text_dimension(
    "cost_category", "Lowercase project cost category.", "cost category", "فئة التكلفة"
)
CURRENCY = _text_dimension("currency", "ISO currency code.", "currency", "العملة")

PAYROLL_PERIOD = MetricTimeDimensionDefinition(
    id="payroll_period",
    description="Payroll accounting period start date.",
)
INVOICE_ISSUED = MetricTimeDimensionDefinition(
    id="invoice_issued",
    description="Invoice issue date.",
)
PROJECT_COST_DATE = MetricTimeDimensionDefinition(
    id="project_cost_date",
    description="Project cost accounting date.",
)


GOVERNED_METRICS: tuple[MetricDefinition, ...] = (
    MetricDefinition(
        id="active_headcount",
        definition_id="enterprise.active_headcount",
        version="1.0",
        description="Employees whose current normalized employment status is active.",
        aliases=(
            "active headcount",
            "headcount",
            "active employees",
            "employee count",
            "عدد الموظفين النشطين",
            "عدد الموظفين",
        ),
        grain="employee",
        formula="COUNT employees WHERE LOWER(status) = 'active'",
        source_models=("employees", "departments"),
        source_tables=("analytics.employees", "analytics.departments"),
        dimensions=(DEPARTMENT, EMPLOYMENT_STATUS),
        null_behavior="Employees with a null status do not count; null dimensions remain null.",
        unit="employees",
    ),
    MetricDefinition(
        id="annual_base_payroll",
        definition_id="enterprise.annual_base_payroll",
        version="1.0",
        description="Annual base salary across employee roster rows.",
        aliases=(
            "annual base payroll",
            "annual payroll",
            "base payroll",
            "total payroll",
            "annual salary cost",
            "اجمالي الرواتب",
            "إجمالي الرواتب",
            "الرواتب السنوية",
        ),
        grain="employee",
        formula="SUM(employees.salary)",
        source_models=("employees", "departments"),
        source_tables=("analytics.employees", "analytics.departments"),
        dimensions=(DEPARTMENT, EMPLOYMENT_STATUS, CURRENCY),
        null_behavior="Null salaries are ignored by SUM; an empty group returns no row.",
        unit="currency amount per year",
    ),
    MetricDefinition(
        id="net_payroll",
        definition_id="enterprise.net_payroll",
        version="1.0",
        description="Base salary plus bonus minus deductions for payroll records.",
        aliases=("net payroll", "net salary", "صافي الرواتب", "صافي الراتب"),
        grain="employee payroll period",
        formula="SUM(payroll.base_salary + payroll.bonus - payroll.deductions)",
        source_models=("payroll", "employees", "departments"),
        source_tables=(
            "analytics.payroll",
            "analytics.employees",
            "analytics.departments",
        ),
        dimensions=(DEPARTMENT, PAYROLL_STATUS, CURRENCY),
        time_dimensions=(PAYROLL_PERIOD,),
        null_behavior=(
            "A row with a null arithmetic operand contributes null and is ignored by SUM."
        ),
        unit="currency amount",
    ),
    MetricDefinition(
        id="invoice_amount",
        definition_id="enterprise.invoice_amount",
        version="1.0",
        description="Extended invoice-line amount based on quantity and unit price.",
        aliases=(
            "invoice amount",
            "total invoice amount",
            "invoice total",
            "invoiced revenue",
            "اجمالي الفواتير",
            "إجمالي الفواتير",
        ),
        grain="invoice line",
        formula="SUM(invoice_lines.quantity * invoice_lines.unit_price)",
        source_models=("invoice_lines", "invoices", "customers", "projects"),
        source_tables=(
            "analytics.invoice_lines",
            "analytics.invoices",
            "analytics.customers",
            "analytics.projects",
        ),
        dimensions=(CUSTOMER, INVOICE_STATUS, PROJECT, CURRENCY),
        time_dimensions=(INVOICE_ISSUED,),
        null_behavior=(
            "A line with null quantity or unit price contributes null and is ignored by SUM."
        ),
        unit="currency amount",
    ),
    MetricDefinition(
        id="project_cost",
        definition_id="enterprise.project_cost",
        version="1.0",
        description="Recorded project cost amount.",
        aliases=(
            "project cost",
            "project costs",
            "total project cost",
            "تكلفة المشروع",
            "تكاليف المشاريع",
        ),
        grain="project cost entry",
        formula="SUM(project_costs.amount)",
        source_models=("project_costs", "projects", "customers", "departments"),
        source_tables=(
            "analytics.project_costs",
            "analytics.projects",
            "analytics.customers",
            "analytics.departments",
        ),
        dimensions=(PROJECT, COST_CATEGORY, CUSTOMER, DEPARTMENT),
        time_dimensions=(PROJECT_COST_DATE,),
        null_behavior="Null cost amounts are ignored by SUM; projects without costs return no row.",
        unit="currency amount",
    ),
    MetricDefinition(
        id="project_margin",
        definition_id="enterprise.project_margin",
        version="1.0",
        description="Invoiced line amount minus recorded costs for the same project.",
        aliases=("project margin", "margin", "هامش المشروع", "هامش المشاريع"),
        grain="project",
        formula="COALESCE(project invoice amount, 0) - COALESCE(project cost, 0)",
        source_models=("project_financials",),
        source_tables=(
            "analytics.projects",
            "analytics.invoices",
            "analytics.invoice_lines",
            "analytics.project_costs",
        ),
        dimensions=(PROJECT, CUSTOMER, DEPARTMENT),
        null_behavior="Missing invoice or cost aggregates are treated as zero.",
        unit="currency amount",
    ),
    MetricDefinition(
        id="budget_utilization",
        definition_id="enterprise.budget_utilization",
        version="1.0",
        description="Recorded project cost as a percentage of approved project budget.",
        aliases=(
            "budget utilization",
            "budget used",
            "budget usage",
            "الميزانية المستخدمة",
            "استخدام الميزانية",
        ),
        grain="project",
        formula="100 * COALESCE(project cost, 0) / NULLIF(project budget, 0)",
        source_models=("project_financials",),
        source_tables=("analytics.projects", "analytics.project_costs"),
        dimensions=(PROJECT, CUSTOMER, DEPARTMENT),
        null_behavior="Missing cost is zero; zero or null budget produces null utilization.",
        unit="percent",
    ),
)

_BY_ID = {metric.id: metric for metric in GOVERNED_METRICS}


def metric_definition(metric_id: str) -> MetricDefinition:
    try:
        return _BY_ID[metric_id]
    except KeyError as exc:
        raise MetricQueryValidationError(f"Unknown governed metric '{metric_id}'.") from exc


def validate_metric_query(query: MetricQuery) -> MetricDefinition:
    definition = metric_definition(query.metric)
    allowed_dimensions = {dimension.id: dimension for dimension in definition.dimensions}
    for dimension_id in query.dimensions:
        if dimension_id not in allowed_dimensions:
            raise MetricQueryValidationError(
                f"Dimension '{dimension_id}' is not allowed for metric '{query.metric}'."
            )
    for filter_ in query.filters:
        dimension = allowed_dimensions.get(filter_.dimension)
        if dimension is None:
            raise MetricQueryValidationError(
                f"Filter dimension '{filter_.dimension}' is not allowed for metric "
                f"'{query.metric}'."
            )
        if filter_.operator not in dimension.allowed_operators:
            raise MetricQueryValidationError(
                f"Operator '{filter_.operator}' is not allowed for dimension "
                f"'{filter_.dimension}'."
            )
    if query.time_dimension is not None:
        allowed_time_dimensions = {item.id: item for item in definition.time_dimensions}
        time_dimension = allowed_time_dimensions.get(query.time_dimension)
        if time_dimension is None:
            raise MetricQueryValidationError(
                f"Time dimension '{query.time_dimension}' is not allowed for metric "
                f"'{query.metric}'."
            )
        if query.time_grain is not None and query.time_grain not in time_dimension.allowed_grains:
            raise MetricQueryValidationError(
                f"Time grain '{query.time_grain}' is not allowed for "
                f"'{query.time_dimension}'."
            )
    allowed_order_members = {query.metric, *allowed_dimensions}
    for order in query.order:
        if order.member not in allowed_order_members:
            raise MetricQueryValidationError(
                f"Order member '{order.member}' is not allowed for metric '{query.metric}'."
            )
    return definition
