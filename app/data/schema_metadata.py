from app.data.gateway import ColumnMetadata, ForeignKeyMetadata, TableMetadata


def synthetic_enterprise_metadata() -> list[TableMetadata]:
    """Return metadata that describes the checked-in synthetic fixture only."""
    return [
        _table(
            "departments",
            "Enterprise departments and their English/Arabic labels.",
            [
                _column("id", "INTEGER", False, "Department identifier.", pk=True),
                _column("name", "VARCHAR", False, "English department name."),
                _column("arabic_name", "VARCHAR", False, "Arabic department name."),
                _column("cost_center", "VARCHAR", False, "Finance cost-center code."),
                _column(
                    "created_at",
                    "TIMESTAMP",
                    False,
                    "Timestamp when the department record was created.",
                    date_meaning="record creation timestamp",
                ),
            ],
            primary_key=("id",),
        ),
        _table(
            "employees",
            "Employees, department placement, employment status, and annual base salary.",
            [
                _column("id", "INTEGER", False, "Employee identifier.", pk=True),
                _column("employee_number", "VARCHAR", False, "Business employee code."),
                _column("department_id", "INTEGER", False, "Current department identifier."),
                _column("manager_id", "INTEGER", True, "Manager employee identifier."),
                _column("full_name", "VARCHAR", False, "English employee name."),
                _column("arabic_name", "VARCHAR", False, "Arabic employee name."),
                _column("job_title", "VARCHAR", False, "Current job title."),
                _column(
                    "status",
                    "VARCHAR",
                    False,
                    "Employment status. Values are observed fixture metadata.",
                    observed=("active", "leave", "terminated"),
                ),
                _column(
                    "hire_date",
                    "DATE",
                    False,
                    "Employment start date.",
                    date_meaning="employee hire date",
                ),
                _column(
                    "termination_date",
                    "DATE",
                    True,
                    "Employment termination date when applicable.",
                    date_meaning="employee termination date",
                ),
                _column("salary", "DECIMAL(12,2)", False, "Annual base salary."),
                _column(
                    "currency",
                    "VARCHAR",
                    False,
                    "Salary currency. Values are observed fixture metadata.",
                    observed=("USD",),
                ),
            ],
            primary_key=("id",),
            foreign_keys=(
                _fk(("department_id",), "analytics.departments", ("id",)),
                _fk(("manager_id",), "analytics.employees", ("id",)),
            ),
        ),
        _table(
            "payroll",
            "Monthly payroll facts for employees, including bonuses and deductions.",
            [
                _column("id", "INTEGER", False, "Payroll row identifier.", pk=True),
                _column("employee_id", "INTEGER", False, "Paid employee identifier."),
                _column(
                    "period_start",
                    "DATE",
                    False,
                    "Payroll period start.",
                    date_meaning="payroll accounting period start",
                ),
                _column(
                    "period_end",
                    "DATE",
                    False,
                    "Payroll period end.",
                    date_meaning="payroll accounting period end",
                ),
                _column("base_salary", "DECIMAL(12,2)", False, "Base pay for this payroll period."),
                _column("bonus", "DECIMAL(12,2)", False, "Bonus paid in this payroll period."),
                _column("deductions", "DECIMAL(12,2)", False, "Deductions in this payroll period."),
                _column(
                    "paid_at",
                    "DATE",
                    True,
                    "Date payment was made.",
                    date_meaning="payment date; nullable until paid",
                ),
                _column(
                    "status",
                    "VARCHAR",
                    False,
                    "Payroll state. Values are observed fixture metadata.",
                    observed=("paid",),
                ),
            ],
            primary_key=("id",),
            foreign_keys=(_fk(("employee_id",), "analytics.employees", ("id",)),),
        ),
        _table(
            "customers",
            "Enterprise customers, location, industry, and lifecycle status.",
            [
                _column("id", "INTEGER", False, "Customer identifier.", pk=True),
                _column("customer_code", "VARCHAR", False, "Business customer code."),
                _column("name", "VARCHAR", False, "English customer name."),
                _column("arabic_name", "VARCHAR", True, "Arabic customer name when available."),
                _column("country_code", "VARCHAR", False, "ISO-like two-letter country code."),
                _column("industry", "VARCHAR", False, "Customer industry."),
                _column(
                    "status",
                    "VARCHAR",
                    False,
                    "Customer lifecycle status. Values are observed fixture metadata.",
                    observed=("active", "inactive"),
                ),
            ],
            primary_key=("id",),
        ),
        _table(
            "projects",
            "Customer projects, ownership, lifecycle dates, status, and approved budget.",
            [
                _column("id", "INTEGER", False, "Project identifier.", pk=True),
                _column("project_code", "VARCHAR", False, "Business project code."),
                _column("customer_id", "INTEGER", False, "Owning customer identifier."),
                _column("owning_department_id", "INTEGER", False, "Owning department identifier."),
                _column("name", "VARCHAR", False, "Project name."),
                _column(
                    "status",
                    "VARCHAR",
                    False,
                    "Project lifecycle status. Values are observed fixture metadata.",
                    observed=("active", "completed"),
                ),
                _column(
                    "start_date",
                    "DATE",
                    False,
                    "Planned or actual project start.",
                    date_meaning="project activity interval start",
                ),
                _column(
                    "end_date",
                    "DATE",
                    True,
                    "Project end; null means no recorded end.",
                    date_meaning="project activity interval end, inclusive when present",
                ),
                _column("budget", "DECIMAL(14,2)", False, "Approved project budget."),
            ],
            primary_key=("id",),
            foreign_keys=(
                _fk(("customer_id",), "analytics.customers", ("id",)),
                _fk(("owning_department_id",), "analytics.departments", ("id",)),
            ),
        ),
        _table(
            "employee_project_assignments",
            "Many-to-many employee/project assignments, allocation, and billability.",
            [
                _column("employee_id", "INTEGER", False, "Assigned employee identifier.", pk=True),
                _column("project_id", "INTEGER", False, "Assigned project identifier.", pk=True),
                _column(
                    "assigned_from",
                    "DATE",
                    False,
                    "Assignment start.",
                    date_meaning="assignment interval start",
                ),
                _column(
                    "assigned_to",
                    "DATE",
                    True,
                    "Assignment end; null means open-ended.",
                    date_meaning="assignment interval end, inclusive when present",
                ),
                _column(
                    "allocation_percent",
                    "DECIMAL(5,2)",
                    False,
                    "Percentage of employee capacity allocated.",
                ),
                _column(
                    "billable",
                    "BOOLEAN",
                    False,
                    "Whether assignment time is billable.",
                    observed=("true",),
                ),
            ],
            primary_key=("employee_id", "project_id"),
            foreign_keys=(
                _fk(("employee_id",), "analytics.employees", ("id",)),
                _fk(("project_id",), "analytics.projects", ("id",)),
            ),
        ),
        _table(
            "invoices",
            "Customer/project invoice headers and invoice lifecycle dates.",
            [
                _column("id", "INTEGER", False, "Invoice identifier.", pk=True),
                _column("invoice_number", "VARCHAR", False, "Business invoice number."),
                _column("customer_id", "INTEGER", False, "Billed customer identifier."),
                _column("project_id", "INTEGER", True, "Related project when one exists."),
                _column(
                    "issued_on",
                    "DATE",
                    False,
                    "Invoice issue date.",
                    date_meaning="invoice recognition/issue date",
                ),
                _column(
                    "due_on", "DATE", False, "Invoice due date.", date_meaning="payment due date"
                ),
                _column(
                    "status",
                    "VARCHAR",
                    False,
                    "Invoice lifecycle status. Values are observed fixture metadata.",
                    observed=("paid", "issued"),
                ),
                _column(
                    "currency",
                    "VARCHAR",
                    False,
                    "Invoice currency. Values are observed fixture metadata.",
                    observed=("USD",),
                ),
            ],
            primary_key=("id",),
            foreign_keys=(
                _fk(("customer_id",), "analytics.customers", ("id",)),
                _fk(("project_id",), "analytics.projects", ("id",)),
            ),
        ),
        _table(
            "invoice_lines",
            "Invoice line quantities and unit prices used to calculate invoice amounts.",
            [
                _column("id", "INTEGER", False, "Invoice line identifier.", pk=True),
                _column("invoice_id", "INTEGER", False, "Parent invoice identifier."),
                _column("description", "VARCHAR", False, "Line-item description."),
                _column("quantity", "DECIMAL(10,2)", False, "Billed quantity."),
                _column("unit_price", "DECIMAL(12,2)", False, "Price per unit."),
            ],
            primary_key=("id",),
            foreign_keys=(_fk(("invoice_id",), "analytics.invoices", ("id",)),),
        ),
        _table(
            "project_costs",
            "Dated project cost entries by category.",
            [
                _column("id", "INTEGER", False, "Project cost identifier.", pk=True),
                _column("project_id", "INTEGER", False, "Related project identifier."),
                _column(
                    "cost_date",
                    "DATE",
                    False,
                    "Date the project cost occurred.",
                    date_meaning="cost recognition date",
                ),
                _column(
                    "category",
                    "VARCHAR",
                    False,
                    "Cost category. Values are observed fixture metadata.",
                    observed=("labor", "software", "travel"),
                ),
                _column("amount", "DECIMAL(12,2)", False, "Recognized project cost amount."),
                _column("description", "VARCHAR", False, "Cost-entry description."),
            ],
            primary_key=("id",),
            foreign_keys=(_fk(("project_id",), "analytics.projects", ("id",)),),
        ),
    ]


def _column(
    name: str,
    data_type: str,
    nullable: bool,
    description: str,
    *,
    pk: bool = False,
    observed: tuple[str, ...] = (),
    date_meaning: str | None = None,
) -> ColumnMetadata:
    return ColumnMetadata(
        name=name,
        data_type=data_type,
        nullable=nullable,
        description=description,
        primary_key=pk,
        observed_values=observed,
        observed_values_source="fixture" if observed else None,
        date_meaning=date_meaning,
    )


def _fk(
    columns: tuple[str, ...], referenced_table: str, referenced_columns: tuple[str, ...]
) -> ForeignKeyMetadata:
    return ForeignKeyMetadata(columns, referenced_table, referenced_columns)


def _table(
    name: str,
    description: str,
    columns: list[ColumnMetadata],
    *,
    primary_key: tuple[str, ...],
    foreign_keys: tuple[ForeignKeyMetadata, ...] = (),
) -> TableMetadata:
    return TableMetadata(
        schema_name="analytics",
        table_name=name,
        columns=[column.name for column in columns],
        description=description,
        column_metadata=columns,
        primary_key=primary_key,
        foreign_keys=foreign_keys,
    )
