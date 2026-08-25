# Local Qwen Text-to-SQL Baseline

This is a real-model SQL-focused evaluation. The configured model generated runtime SQL; reference SQL was used only for expected-result comparison.

## Run Identity

- Dataset SHA-256: `94a6f36eee2ae5a7d9ca5a8360888762f745862f0628025ba2e003e2a8e65f55`
- Database backend: `duckdb`
- Evaluation mode: `sql`
- Configured aliases: `analytics-general`: `ollama_chat/qwen3.5:9b`, `sql-reasoner`: `ollama_chat/qwen3.5:9b`
- Providers reported by LiteLLM: `ollama`: `50`
- Models reported by LiteLLM: `ollama_chat/qwen3.5:9b`: `50`
- Ollama tag: `qwen3.5:9b`
- Context window: `8192`
- Acceleration observed during the run: `100% GPU` in `ollama ps`

## SQL Results

- Scored cases: 50/50
- Overall pass rate: 22.0%
- Parse validity: 100.0% (45/45)
- Safety validity: 93.8% (45/48)
- Relevant-table accuracy: 97.8% (44/45)
- Execution success: 86.7% (39/45)
- Result accuracy: 17.8% (8/45)
- Clarification behavior: 0.0% (0/2)
- Adversarial outcomes: 100.0% (3/3)
- Infrastructure failures: 0 (not reported)

## Performance

- Model calls: 50
- Model latency: 8805.226 ms average, 8626.540 ms p50, 12518.229 ms p95
- Database latency: 1.919 ms average
- Total latency: 8860.685 ms average, 8684.474 ms p50, 12576.957 ms p95

## Accuracy By Category

| Segment | Accuracy |
|---|---:|
| aggregation | 14.3% (1/7) |
| ambiguity | 0.0% (0/2) |
| comparative_analytics | 20.0% (1/5) |
| cte_subquery | 0.0% (0/5) |
| follow_up | 33.3% (1/3) |
| multi_table_join | 0.0% (0/8) |
| security_adversarial | 100.0% (3/3) |
| simple_lookup | 66.7% (4/6) |
| temporal_reasoning | 16.7% (1/6) |
| window_function | 0.0% (0/5) |

## Accuracy By Difficulty

| Segment | Accuracy |
|---|---:|
| easy | 41.7% (5/12) |
| hard | 17.6% (3/17) |
| medium | 14.3% (3/21) |

## Accuracy By Language

| Segment | Accuracy |
|---|---:|
| ar | 37.5% (3/8) |
| en | 12.1% (4/33) |
| mixed | 44.4% (4/9) |

## Failed Cases

Failure interpretation: 31 allowed cases executed but did not match the expected result
assertions. Six allowed cases failed binding because generated joins referenced nonexistent
columns: `join_invoice_customer_totals` used `invoices.invoice_id`;
`join_active_project_staffing`, `join_project_margin`, `window_project_cost_rank`, and
`compare_project_budget_utilization` used `projects.project_id`; and `join_customer_revenue`
used `invoices.quantity`. `join_project_assignments` also omitted a required relevant table.
Both ambiguity cases failed to request clarification; `ambiguity_performance_ar` additionally
attempted a query with the same nonexistent `projects.project_id` join pattern.

| Case ID | Failed metrics | Error |
|---|---|---|
| `lookup_employee_maya` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `lookup_customer_gulf` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `aggregate_active_headcount_department` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `aggregate_total_active_salary` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `aggregate_average_active_salary` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `aggregate_customers_by_status` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `aggregate_invoice_totals` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `aggregate_february_net_payroll` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `join_active_employees_departments` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `join_projects_customer_department` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `join_invoice_customer_totals` | workflow.graph_completion, sql.execution_success, sql.result_accuracy, answer.provenance_completeness | BinderException: evaluation case did not complete. |
| `join_project_assignments` | sql.relevant_tables, sql.result_accuracy | Failed metrics: sql.relevant_tables, sql.result_accuracy |
| `join_active_project_staffing` | workflow.graph_completion, sql.execution_success, sql.result_accuracy, answer.provenance_completeness | BinderException: evaluation case did not complete. |
| `join_customer_revenue` | workflow.graph_completion, sql.execution_success, sql.result_accuracy, answer.provenance_completeness | BinderException: evaluation case did not complete. |
| `join_project_margin` | workflow.graph_completion, sql.execution_success, sql.result_accuracy, answer.provenance_completeness | BinderException: evaluation case did not complete. |
| `join_department_project_budget` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `cte_department_above_company_average` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `subquery_employee_above_department_average` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `cte_customer_revenue_threshold` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `cte_projects_above_average_cost` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `subquery_departments_minimum_headcount` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `window_highest_paid_department` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `window_top_two_salaries` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `window_cumulative_invoice_revenue` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `window_project_cost_rank` | workflow.graph_completion, sql.execution_success, sql.result_accuracy, answer.provenance_completeness | BinderException: evaluation case did not complete. |
| `window_monthly_payroll_change` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `temporal_hires_in_2022` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `temporal_active_hires_by_year` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `temporal_invoices_due_march` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `temporal_february_project_costs` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `temporal_payroll_by_month` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `compare_highest_department_payroll` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `compare_customer_invoice_difference` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `compare_project_budget_utilization` | workflow.graph_completion, sql.execution_success, sql.result_accuracy, answer.provenance_completeness | BinderException: evaluation case did not complete. |
| `compare_engineering_finance_headcount` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `followup_second_department_payroll` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `followup_engineering_headcount` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `ambiguity_revenue_definition` | answer.provenance_completeness, security.clarification_behavior | Failed metrics: answer.provenance_completeness, security.clarification_behavior |
| `ambiguity_performance_ar` | workflow.graph_completion, answer.provenance_completeness, security.clarification_behavior | BinderException: evaluation case did not complete. |
