import re
from collections import defaultdict, deque
from time import perf_counter

from app.agent.context import AnalyticalContext
from app.data.gateway import TableMetadata
from app.semantic.gateway import SemanticContext, SemanticDefinition, SemanticGateway

DEFINITIONS = (
    SemanticDefinition(
        identifier="active_employee",
        name="Active employee",
        description="An employee whose current employment status is active.",
        expression="LOWER(analytics.employees.status) = 'active'",
        tables=("analytics.employees",),
        required_columns=("analytics.employees.status",),
        aliases=("active employee", "active employees", "active headcount", "الموظفين النشطين"),
    ),
    SemanticDefinition(
        identifier="annual_base_salary",
        name="Annual base salary payroll",
        description="Annual employee payroll uses the annual base salary stored on employees.",
        expression="SUM(analytics.employees.salary)",
        tables=("analytics.employees", "analytics.departments"),
        required_columns=("analytics.employees.salary",),
        aliases=("annual salary", "annual payroll", "department payroll", "salary", "رواتب"),
    ),
    SemanticDefinition(
        identifier="net_payroll",
        name="Net payroll",
        description=(
            "Net payroll for a payroll period includes base pay plus bonus minus deductions."
        ),
        expression="SUM(base_salary + bonus - deductions), grouped/filtered by period_start",
        tables=("analytics.payroll",),
        required_columns=(
            "analytics.payroll.base_salary",
            "analytics.payroll.bonus",
            "analytics.payroll.deductions",
            "analytics.payroll.period_start",
        ),
        aliases=("net payroll", "payroll by month", "صافي الرواتب"),
    ),
    SemanticDefinition(
        identifier="invoice_amount",
        name="Invoiced amount",
        description="Invoice amount is the sum of line quantity multiplied by line unit price.",
        expression="SUM(analytics.invoice_lines.quantity * analytics.invoice_lines.unit_price)",
        tables=("analytics.invoices", "analytics.invoice_lines"),
        required_columns=(
            "analytics.invoice_lines.quantity",
            "analytics.invoice_lines.unit_price",
        ),
        aliases=(
            "invoice total",
            "invoice totals",
            "invoiced",
            "invoiced revenue",
            "قيمة الفاتورة",
        ),
    ),
    SemanticDefinition(
        identifier="project_cost",
        name="Project cost",
        description=(
            "Project cost is the sum of project cost entry amounts using cost_date "
            "for time filters."
        ),
        expression="SUM(analytics.project_costs.amount), filtered by project_costs.cost_date",
        tables=("analytics.projects", "analytics.project_costs"),
        required_columns=(
            "analytics.project_costs.amount",
            "analytics.project_costs.cost_date",
        ),
        aliases=("project cost", "project costs", "cost categories", "تكاليف"),
    ),
    SemanticDefinition(
        identifier="budget_utilization",
        name="Project budget utilization",
        description=(
            "Budget utilization percentage is project cost divided by approved "
            "project budget times 100."
        ),
        expression="100 * COALESCE(SUM(project_costs.amount), 0) / projects.budget",
        tables=("analytics.projects", "analytics.project_costs"),
        required_columns=(
            "analytics.project_costs.amount",
            "analytics.projects.budget",
        ),
        aliases=("budget utilization", "percentage من budget", "percentage of budget"),
    ),
    SemanticDefinition(
        identifier="active_project_status",
        name="Active project by status",
        description="A currently active project has the observed status value active.",
        expression="LOWER(analytics.projects.status) = 'active'",
        tables=("analytics.projects",),
        required_columns=("analytics.projects.status",),
        aliases=("active project", "active projects", "المشاريع النشطة"),
    ),
    SemanticDefinition(
        identifier="project_active_on_date",
        name="Project active on a date",
        description=(
            "For date-based activity, start_date is on/before the date and end_date "
            "is null or on/after it; status is not additionally required."
        ),
        expression="start_date <= target_date AND (end_date IS NULL OR end_date >= target_date)",
        tables=("analytics.projects",),
        required_columns=(
            "analytics.projects.start_date",
            "analytics.projects.end_date",
        ),
        aliases=("active on", "based on their dates", "نشط في تاريخ"),
    ),
)


TABLE_TERMS: dict[str, tuple[str, ...]] = {
    "analytics.departments": ("department", "departments", "cost center", "قسم", "الأقسام"),
    "analytics.employees": (
        "employee",
        "employees",
        "salary",
        "headcount",
        "hire",
        "موظف",
        "الموظفين",
        "راتب",
    ),
    "analytics.payroll": ("payroll", "base payroll", "bonus", "deduction", "رواتب"),
    "analytics.customers": ("customer", "customers", "industry", "عميل", "العملاء"),
    "analytics.projects": ("project", "projects", "budget", "مشروع", "المشاريع"),
    "analytics.employee_project_assignments": (
        "assignment",
        "assignments",
        "assigned",
        "allocation",
        "تخصيص",
    ),
    "analytics.invoices": ("invoice", "invoices", "due", "issued", "فاتورة", "الفواتير"),
    "analytics.invoice_lines": ("invoice total", "invoice totals", "invoiced", "قيمة الفاتورة"),
    "analytics.project_costs": (
        "project cost",
        "project costs",
        "cost category",
        "costs",
        "تكاليف",
    ),
}

TABLE_TERMS_BY_NAME = {
    identifier.rsplit(".", maxsplit=1)[-1]: terms for identifier, terms in TABLE_TERMS.items()
}

GENERIC_COLUMN_TERMS = {
    "id",
    "name",
    "arabic name",
    "status",
    "description",
    "currency",
}


class InMemorySemanticGateway(SemanticGateway):
    """Deterministic development selector over explicit synthetic metadata."""

    async def retrieve_context(
        self,
        *,
        question: str,
        available_tables: list[TableMetadata],
        prior_context: AnalyticalContext | None,
    ) -> SemanticContext:
        started_at = perf_counter()
        context_text = _context_text(question, prior_context)
        available_ids = {table.identifier for table in available_tables}
        definitions = [
            definition
            for definition in DEFINITIONS
            if any(_contains(context_text, alias) for alias in definition.aliases)
            and set(definition.tables).issubset(available_ids)
        ]
        reasons: dict[str, set[str]] = defaultdict(set)
        selected: set[str] = set()
        for table in available_tables:
            terms = TABLE_TERMS_BY_NAME.get(table.table_name, ())
            table_terms = {
                table.table_name.replace("_", " "),
                table.table_name.removesuffix("s").replace("_", " "),
            }
            if any(_contains(context_text, term) for term in (*terms, *table_terms)):
                selected.add(table.identifier)
                reasons[table.identifier].add("lexical_match")
            column_terms = {
                column.replace("_", " ")
                for column in table.columns
                if column.replace("_", " ") not in GENERIC_COLUMN_TERMS
            }
            if any(_contains(context_text, term) for term in column_terms if len(term) > 3):
                selected.add(table.identifier)
                reasons[table.identifier].add("column_match")

        for definition in definitions:
            for table_id in definition.tables:
                selected.add(table_id)
                reasons[table_id].add(f"semantic:{definition.identifier}")

        selected = _expand_relationship_paths(selected, available_tables, reasons)
        tables = [table for table in available_tables if table.identifier in selected]
        relationship_ids = sorted(
            {
                _relationship_id(table.identifier, foreign_key.referenced_table)
                for table in tables
                for foreign_key in table.foreign_keys
                if foreign_key.referenced_table in selected
            }
        )
        return SemanticContext(
            tables=tables,
            definitions=definitions,
            selection_reasons={key: tuple(sorted(value)) for key, value in reasons.items()},
            provider="inmemory",
            retrieval_latency_ms=round((perf_counter() - started_at) * 1000, 3),
            model_ids=[table.identifier for table in tables],
            relationship_ids=relationship_ids,
            context_size_chars=sum(
                len(table.identifier) + len(table.description) for table in tables
            ),
        )


def _context_text(question: str, prior_context: AnalyticalContext | None) -> str:
    values = [question]
    if prior_context is not None:
        values.extend(
            value
            for value in (
                prior_context.metric,
                *prior_context.dimensions,
                *prior_context.entities,
                prior_context.previous_question,
            )
            if value
        )
    return " ".join(values).casefold()


def _contains(text: str, term: str) -> bool:
    normalized_term = term.casefold()
    if re.fullmatch(r"[a-z0-9 _-]+", normalized_term):
        return bool(re.search(rf"(?<!\w){re.escape(normalized_term)}(?!\w)", text))
    return normalized_term in text


def _expand_relationship_paths(
    selected: set[str],
    tables: list[TableMetadata],
    reasons: dict[str, set[str]],
) -> set[str]:
    if len(selected) < 2:
        return selected
    graph: dict[str, set[str]] = defaultdict(set)
    for table in tables:
        for foreign_key in table.foreign_keys:
            graph[table.identifier].add(foreign_key.referenced_table)
            graph[foreign_key.referenced_table].add(table.identifier)
    targets = list(selected)
    for start_index, start in enumerate(targets):
        for target in targets[start_index + 1 :]:
            path = _shortest_path(graph, start, target, preferred=selected)
            for table_id in path[1:-1]:
                selected.add(table_id)
                reasons[table_id].add("relationship_path")
    return selected


def _shortest_path(
    graph: dict[str, set[str]],
    start: str,
    target: str,
    *,
    preferred: set[str],
) -> list[str]:
    queue: deque[list[str]] = deque([[start]])
    visited = {start}
    while queue:
        path = queue.popleft()
        for neighbor in sorted(graph[path[-1]], key=lambda value: (value not in preferred, value)):
            if neighbor in visited:
                continue
            next_path = [*path, neighbor]
            if neighbor == target:
                return next_path
            visited.add(neighbor)
            queue.append(next_path)
    return []


def _relationship_id(left: str, right: str) -> str:
    return "physical:" + "--".join(sorted((left, right)))
