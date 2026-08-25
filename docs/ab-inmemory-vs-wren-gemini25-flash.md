# InMemory vs Wren: Gemini 2.5 Flash

## Experiment Contract

- Dataset SHA-256: `94a6f36eee2ae5a7d9ca5a8360888762f745862f0628025ba2e003e2a8e65f55`
- Database: `duckdb`
- Evaluator: `2.0`, SQL-only mode
- Logical aliases: both resolve to `vertex_ai/gemini-2.5-flash`
- SQL generation: existing `LLMGateway` / `sql-reasoner` path
- Only experimental variable: `SEMANTIC_PROVIDER`

## Pre-flight Context Retrieval

| Question | Provider | Tables/models | Relationships | Definitions/calculated fields |
|---|---|---|---|---|
| Which department has the highest payroll? | inmemory | `analytics.departments`, `analytics.employees`, `analytics.payroll` | `physical:analytics.departments--analytics.employees`, `physical:analytics.employees--analytics.employees`, `physical:analytics.employees--analytics.payroll` | none |
| Which department has the highest payroll? | wren | `analytics.departments`, `analytics.employees`, `analytics.payroll` | `wren:enterprise_analytics:employees_departments`, `wren:enterprise_analytics:payroll_employees` | `wren:annual_base_salary` |
| Which customer has the highest invoice amount? | inmemory | `analytics.customers`, `analytics.projects`, `analytics.invoices`, `analytics.project_costs` | `physical:analytics.customers--analytics.invoices`, `physical:analytics.customers--analytics.projects`, `physical:analytics.invoices--analytics.projects`, `physical:analytics.project_costs--analytics.projects` | none |
| Which customer has the highest invoice amount? | wren | `analytics.customers`, `analytics.invoices`, `analytics.invoice_lines` | `wren:enterprise_analytics:invoices_customers`, `wren:enterprise_analytics:invoice_lines_invoices` | `wren:invoice_amount`, `wren:invoice_lines.line_amount` |
| Which projects are active? | inmemory | `analytics.projects` | none | none |
| Which projects are active? | wren | `analytics.projects` | none | none |
| Show project budget utilization. | inmemory | `analytics.projects`, `analytics.project_costs` | `physical:analytics.project_costs--analytics.projects` | `budget_utilization` |
| Show project budget utilization. | wren | `analytics.projects`, `analytics.project_costs` | `wren:enterprise_analytics:project_costs_projects` | `wren:budget_utilization` |

## Summary

| Metric | InMemory | Wren | Delta |
|---|---:|---:|---:|
| Overall pass | 48.0% | 52.0% | +4.0 pp |
| Structured output validity | 100.0% | 100.0% | +0.0 pp |
| Relevant-table accuracy | 95.1% | 95.0% | -0.1 pp |
| SQL result accuracy | 44.4% | 46.7% | +2.2 pp |
| Execution success | 88.9% | 88.9% | +0.0 pp |
| Follow-up accuracy | 33.3% | 66.7% | +33.3 pp |
| Clarification accuracy | 100.0% | 100.0% | +0.0 pp |
| Security accuracy | 100.0% | 100.0% | +0.0 pp |
| Avg selected tables | 1.720 | 1.760 | +0.040 |
| Avg prompt tokens/call | 552.453 | 552.623 | +0.170 |
| Avg semantic latency ms | 0.743 | 509.575 | +508.832 |
| Avg total latency ms | 2548.289 | 3051.522 | +503.233 |
| Total cost USD | 0.048 | 0.053 | +0.005 |

## Pairwise Outcomes

- **WREN_ONLY_PASS:** aggregate_project_cost_category, followup_higher_active_project_budget
- **INMEMORY_ONLY_PASS:** none
- **BOTH_PASS:** lookup_department_cost_center, lookup_employee_maya, lookup_customer_gulf, lookup_project_budget, lookup_invoice_due_date, aggregate_total_active_salary, aggregate_average_active_salary, aggregate_customers_by_status, aggregate_february_net_payroll, join_active_employees_departments, join_projects_customer_department, join_project_margin, subquery_employee_above_department_average, cte_projects_above_average_cost, temporal_hires_in_2022, temporal_invoices_due_march, temporal_projects_active_on_date, compare_customer_status_counts, followup_engineering_headcount, ambiguity_revenue_definition, ambiguity_performance_ar, security_delete_employees, security_multiple_statement_drop, security_update_salary_ar
- **BOTH_FAIL:** lookup_employee_arabic_name, aggregate_active_headcount_department, aggregate_invoice_totals, join_invoice_customer_totals, join_project_assignments, join_customer_revenue, join_department_project_budget, cte_department_above_company_average, subquery_departments_minimum_headcount, window_highest_paid_department, window_top_two_salaries, window_cumulative_invoice_revenue, window_monthly_payroll_change, temporal_active_hires_by_year, temporal_february_project_costs, temporal_payroll_by_month, compare_highest_department_payroll, compare_customer_invoice_difference, compare_project_budget_utilization, compare_engineering_finance_headcount, followup_second_department_payroll
- **DIFFERENT_FAILURE:** join_active_project_staffing, cte_customer_revenue_threshold, window_project_cost_rank

| Case | Classification | Evidence-based difference |
|---|---|---|
| `lookup_department_cost_center` | BOTH_PASS | Same pass outcome and failure signature. |
| `lookup_employee_maya` | BOTH_PASS | Same pass outcome and failure signature. |
| `lookup_employee_arabic_name` | BOTH_FAIL | Same pass outcome and failure signature. |
| `lookup_customer_gulf` | BOTH_PASS | Same pass outcome and failure signature. |
| `lookup_project_budget` | BOTH_PASS | Same pass outcome and failure signature. |
| `lookup_invoice_due_date` | BOTH_PASS | Same pass outcome and failure signature. |
| `aggregate_active_headcount_department` | BOTH_FAIL | Same pass outcome and failure signature. |
| `aggregate_total_active_salary` | BOTH_PASS | Same pass outcome and failure signature. |
| `aggregate_average_active_salary` | BOTH_PASS | Same pass outcome and failure signature. |
| `aggregate_customers_by_status` | BOTH_PASS | Same pass outcome and failure signature. |
| `aggregate_invoice_totals` | BOTH_FAIL | Same pass outcome and failure signature. |
| `aggregate_project_cost_category` | WREN_ONLY_PASS | other: generated output alias/result shape differed. |
| `aggregate_february_net_payroll` | BOTH_PASS | Same pass outcome and failure signature. |
| `join_active_employees_departments` | BOTH_PASS | Same pass outcome and failure signature. |
| `join_projects_customer_department` | BOTH_PASS | Same pass outcome and failure signature. |
| `join_invoice_customer_totals` | BOTH_FAIL | Same pass outcome and failure signature. |
| `join_project_assignments` | BOTH_FAIL | Same pass outcome and failure signature. |
| `join_active_project_staffing` | DIFFERENT_FAILURE | InMemory: sql.result_accuracy; Wren: sql.relevant_tables, sql.result_accuracy. |
| `join_customer_revenue` | BOTH_FAIL | Same pass outcome and failure signature. |
| `join_project_margin` | BOTH_PASS | Same pass outcome and failure signature. |
| `join_department_project_budget` | BOTH_FAIL | Same pass outcome and failure signature. |
| `cte_department_above_company_average` | BOTH_FAIL | Same pass outcome and failure signature. |
| `subquery_employee_above_department_average` | BOTH_PASS | Same pass outcome and failure signature. |
| `cte_customer_revenue_threshold` | DIFFERENT_FAILURE | InMemory: workflow.graph_completion, sql.execution_success, sql.result_accuracy, answer.provenance_completeness; Wren: sql.result_accuracy. |
| `cte_projects_above_average_cost` | BOTH_PASS | Same pass outcome and failure signature. |
| `subquery_departments_minimum_headcount` | BOTH_FAIL | Same pass outcome and failure signature. |
| `window_highest_paid_department` | BOTH_FAIL | Same pass outcome and failure signature. |
| `window_top_two_salaries` | BOTH_FAIL | Same pass outcome and failure signature. |
| `window_cumulative_invoice_revenue` | BOTH_FAIL | Same pass outcome and failure signature. |
| `window_project_cost_rank` | DIFFERENT_FAILURE | InMemory: sql.relevant_tables; Wren: sql.execution_success, sql.result_accuracy, answer.provenance_completeness. |
| `window_monthly_payroll_change` | BOTH_FAIL | Same pass outcome and failure signature. |
| `temporal_hires_in_2022` | BOTH_PASS | Same pass outcome and failure signature. |
| `temporal_active_hires_by_year` | BOTH_FAIL | Same pass outcome and failure signature. |
| `temporal_invoices_due_march` | BOTH_PASS | Same pass outcome and failure signature. |
| `temporal_february_project_costs` | BOTH_FAIL | Same pass outcome and failure signature. |
| `temporal_payroll_by_month` | BOTH_FAIL | Same pass outcome and failure signature. |
| `temporal_projects_active_on_date` | BOTH_PASS | Same pass outcome and failure signature. |
| `compare_highest_department_payroll` | BOTH_FAIL | Same pass outcome and failure signature. |
| `compare_customer_invoice_difference` | BOTH_FAIL | Same pass outcome and failure signature. |
| `compare_project_budget_utilization` | BOTH_FAIL | Same pass outcome and failure signature. |
| `compare_engineering_finance_headcount` | BOTH_FAIL | Same pass outcome and failure signature. |
| `compare_customer_status_counts` | BOTH_PASS | Same pass outcome and failure signature. |
| `followup_second_department_payroll` | BOTH_FAIL | Same pass outcome and failure signature. |
| `followup_engineering_headcount` | BOTH_PASS | Same pass outcome and failure signature. |
| `followup_higher_active_project_budget` | WREN_ONLY_PASS | different calculated-field context. |
| `ambiguity_revenue_definition` | BOTH_PASS | Same pass outcome and failure signature. |
| `ambiguity_performance_ar` | BOTH_PASS | Same pass outcome and failure signature. |
| `security_delete_employees` | BOTH_PASS | Same pass outcome and failure signature. |
| `security_multiple_statement_drop` | BOTH_PASS | Same pass outcome and failure signature. |
| `security_update_salary_ar` | BOTH_PASS | Same pass outcome and failure signature. |

## Quality Breakdowns

### By Category

| Segment | InMemory | Wren | Delta |
|---|---:|---:|---:|
| aggregation | 57.1% | 71.4% | +14.3 pp |
| ambiguity | 100.0% | 100.0% | +0.0 pp |
| comparative_analytics | 20.0% | 20.0% | +0.0 pp |
| cte_subquery | 40.0% | 40.0% | +0.0 pp |
| follow_up | 33.3% | 66.7% | +33.3 pp |
| multi_table_join | 37.5% | 37.5% | +0.0 pp |
| security_adversarial | 100.0% | 100.0% | +0.0 pp |
| simple_lookup | 83.3% | 83.3% | +0.0 pp |
| temporal_reasoning | 50.0% | 50.0% | +0.0 pp |
| window_function | 0.0% | 0.0% | +0.0 pp |

### By Difficulty

| Segment | InMemory | Wren | Delta |
|---|---:|---:|---:|
| easy | 83.3% | 83.3% | +0.0 pp |
| hard | 41.2% | 41.2% | +0.0 pp |
| medium | 33.3% | 42.9% | +9.5 pp |

### By Language

| Segment | InMemory | Wren | Delta |
|---|---:|---:|---:|
| ar | 50.0% | 50.0% | +0.0 pp |
| en | 54.5% | 54.5% | +0.0 pp |
| mixed | 22.2% | 44.4% | +22.2 pp |

## Semantic Context

| Metric | InMemory | Wren | Delta |
|---|---:|---:|---:|
| Average selected tables | 1.720 | 1.760 | +0.040 |
| Average selected models | 1.720 | 1.760 | +0.040 |
| Average relationships | 1.200 | 0.840 | -0.360 |
| Average definitions | 0.680 | 0.600 | -0.080 |
| Average calculated fields | 0.000 | 0.280 | +0.280 |
| Average context chars | 147.020 | 237.260 | +90.240 |
| Average retrieval latency ms | 0.743 | 509.575 | +508.832 |
| P50 retrieval latency ms | 0.402 | 489.019 | +488.617 |
| P95 retrieval latency ms | 1.381 | 608.766 | +607.385 |
| Missing-required-context cases | 5.000 | 6.000 | +1.000 |
| Irrelevant-context cases | 10.000 | 11.000 | +1.000 |

- InMemory missing context: `aggregate_invoice_totals`, `cte_customer_revenue_threshold`, `window_top_two_salaries`, `compare_customer_invoice_difference`, `compare_engineering_finance_headcount`.
- Wren missing context: `aggregate_invoice_totals`, `join_active_project_staffing`, `window_top_two_salaries`, `window_project_cost_rank`, `compare_customer_invoice_difference`, `compare_engineering_finance_headcount`.
- InMemory irrelevant context: `lookup_employee_maya`, `aggregate_total_active_salary`, `aggregate_average_active_salary`, `aggregate_invoice_totals`, `aggregate_project_cost_category`, `join_active_project_staffing`, `subquery_employee_above_department_average`, `compare_highest_department_payroll`, `followup_second_department_payroll`, `followup_engineering_headcount`.
- Wren irrelevant context: `lookup_department_cost_center`, `lookup_employee_maya`, `aggregate_total_active_salary`, `aggregate_average_active_salary`, `aggregate_project_cost_category`, `join_project_assignments`, `join_active_project_staffing`, `subquery_employee_above_department_average`, `compare_highest_department_payroll`, `followup_second_department_payroll`, `followup_engineering_headcount`.

Missing context means at least one frozen `relevant_tables` entry was absent. Irrelevant context means selected tables exceeded that case's frozen relevant-table set; it is a diagnostic, not an automatic quality failure.
Each arm contains one cloud-model sample per case. Provider-only passes are not treated as causally semantic unless the recorded contexts supply a concrete difference; otherwise they remain model/output variation.

## Performance And Cost

- InMemory calls/tokens: 53 calls, 29,280 prompt, 15,851 completion, 45,131 total tokens.
- Wren calls/tokens: 53 calls, 29,289 prompt, 17,682 completion, 46,971 total tokens.
- InMemory model latency avg/p50/p95: 2513.676/1920.365/6217.650 ms.
- Wren model latency avg/p50/p95: 2484.075/1858.895/5131.767 ms.
- InMemory semantic latency avg/p50/p95: 0.743/0.402/1.381 ms.
- Wren semantic latency avg/p50/p95: 509.575/489.019/608.766 ms.
- InMemory total latency avg/p50/p95: 2548.289/1966.262/6243.282 ms.
- Wren total latency avg/p50/p95: 3051.522/2368.592/6062.471 ms.
- Average database latency: InMemory 1.575 ms; Wren 1.460 ms.
- InMemory Vertex cost: $0.048412.
- Wren Vertex cost: $0.052992.
- Approximate Wren semantic overhead: +508.832 ms/request.

## Architecture Recommendation

**KEEP_BOTH_WREN_OPTIONAL**

The measured pass-rate delta is +4.0 percentage points (2 Wren-only passes, 0 InMemory-only passes). Wren adds +508.8 ms of average semantic retrieval latency and one optional service. Under the predeclared decision rule, these quality, reliability, context, and operational measurements select `KEEP_BOTH_WREN_OPTIONAL`.
