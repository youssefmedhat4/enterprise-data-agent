# Groq Qwen 3.6 27B Cloud Baseline

This compares the frozen local `qwen3.5:9b` SQL-only baseline with Groq-hosted `qwen/qwen3.6-27b` using the unchanged 50-case dataset and DuckDB backend.

## Comparison

| Metric | Local Qwen 3.5 9B | Groq Qwen 3.6 27B |
|---|---:|---:|
| Overall case pass rate | 20.8% | 52.0% |
| Structured output | 100.0% (48/48) | 100.0% (50/50) |
| Graph completion | 97.9% (47/48) | 100.0% (50/50) |
| Relevant-table accuracy | 95.2% (40/42) | 95.2% (40/42) |
| Execution success | 91.1% (41/45) | 93.3% (42/45) |
| SQL result accuracy | 17.8% (8/45) | 48.9% (22/45) |
| Clarification behavior | 100.0% (2/2) | 100.0% (2/2) |
| Security/adversarial | 100.0% (1/1) | 100.0% (3/3) |
| Hallucinated tables | 0 | 0 |
| Hallucinated columns | 3 | 4 |

## Category Accuracy

| Segment | Local Qwen 3.5 9B | Groq Qwen 3.6 27B |
|---|---:|---:|
| aggregation | 0.0% (0/7) | 57.1% (4/7) |
| ambiguity | 100.0% (2/2) | 100.0% (2/2) |
| comparative_analytics | 0.0% (0/5) | 40.0% (2/5) |
| cte_subquery | 0.0% (0/5) | 40.0% (2/5) |
| follow_up | 0.0% (0/3) | 66.7% (2/3) |
| multi_table_join | 0.0% (0/8) | 37.5% (3/8) |
| security_adversarial | 100.0% (1/1) | 100.0% (3/3) |
| simple_lookup | 100.0% (6/6) | 83.3% (5/6) |
| temporal_reasoning | 16.7% (1/6) | 50.0% (3/6) |
| window_function | 0.0% (0/5) | 0.0% (0/5) |

## Difficulty Accuracy

| Segment | Local Qwen 3.5 9B | Groq Qwen 3.6 27B |
|---|---:|---:|
| easy | 50.0% (6/12) | 83.3% (10/12) |
| hard | 6.7% (1/15) | 41.2% (7/17) |
| medium | 14.3% (3/21) | 42.9% (9/21) |

## Language Accuracy

| Segment | Local Qwen 3.5 9B | Groq Qwen 3.6 27B |
|---|---:|---:|
| ar | 42.9% (3/7) | 50.0% (4/8) |
| en | 15.6% (5/32) | 57.6% (19/33) |
| mixed | 22.2% (2/9) | 33.3% (3/9) |

## Performance

- Groq model calls: 53
- Groq tokens: 47702 total
- Groq model latency: 2797.623 ms average, 2328.276 ms median, 5985.725 ms p95
- Groq infrastructure failures: 0 (not reported)

## Hallucinated Columns

| Case | Local | Groq |
|---|---|---|
| `cte_projects_above_average_cost` | ptc.project_code | none |
| `join_project_margin` | pr.project_id | ir.project_id, tc.project_id |
| `subquery_employee_above_department_average` | dept_avg.department_id | T2.department_id |
| `window_cumulative_invoice_revenue` | none | sub.issued_on |

## Failed Groq Cases

| Case ID | Failed metrics | Error |
|---|---|---|
| `lookup_employee_arabic_name` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `aggregate_active_headcount_department` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `aggregate_invoice_totals` | sql.execution_success, sql.result_accuracy, answer.provenance_completeness | Failed metrics: sql.execution_success, sql.result_accuracy, answer.provenance_completeness |
| `aggregate_project_cost_category` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `join_invoice_customer_totals` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `join_project_assignments` | sql.relevant_tables, sql.result_accuracy | Failed metrics: sql.relevant_tables, sql.result_accuracy |
| `join_active_project_staffing` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `join_customer_revenue` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `join_department_project_budget` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `cte_department_above_company_average` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `cte_customer_revenue_threshold` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `subquery_departments_minimum_headcount` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `window_highest_paid_department` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `window_top_two_salaries` | sql.execution_success, sql.result_accuracy, answer.provenance_completeness | Failed metrics: sql.execution_success, sql.result_accuracy, answer.provenance_completeness |
| `window_cumulative_invoice_revenue` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `window_project_cost_rank` | sql.relevant_tables | Failed metrics: sql.relevant_tables |
| `window_monthly_payroll_change` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `temporal_active_hires_by_year` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `temporal_february_project_costs` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `temporal_payroll_by_month` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `compare_highest_department_payroll` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `compare_customer_invoice_difference` | sql.execution_success, sql.result_accuracy, answer.provenance_completeness | Failed metrics: sql.execution_success, sql.result_accuracy, answer.provenance_completeness |
| `compare_project_budget_utilization` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `followup_second_department_payroll` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
