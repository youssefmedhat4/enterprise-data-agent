# Enterprise analytics business semantics

## Employment

- An active employee has `employees.status = 'active'` after case normalization.
- Annual base-salary payroll uses `employees.salary`. It is distinct from monthly net payroll.

## Payroll

- Net payroll is `base_salary + bonus - deductions` from payroll records.
- Payroll period filters use `payroll.period_start` and `payroll.period_end`.
- A paid payroll record has the lowercase status value `paid`.

## Projects

- A currently active project has the lowercase status value `active`.
- Activity on a historical date uses `start_date <= target_date` and an absent or later
  `end_date`; current status is not additionally required.
- Project cost is the sum of `project_costs.amount`, filtered by `project_costs.cost_date`.
- Budget utilization is total project cost divided by `projects.budget`, multiplied by 100.
- Project margin is invoiced line amount minus project cost for the same project.

## Invoices

- Invoice amount is the sum of `invoice_lines.quantity * invoice_lines.unit_price`.
- Invoice time filters use `invoices.issued_on` unless the question explicitly asks about due dates.

## Values and trust

- Status and category values are lowercase in this synthetic source.
- These definitions describe synthetic analytics data and never override authorization or SQL safety.
