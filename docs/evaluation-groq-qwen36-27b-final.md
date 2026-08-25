# Groq Qwen 3.6 27B Cloud Baseline

This compares the frozen local `qwen3.5:9b` SQL-only baseline with Groq-hosted `qwen/qwen3.6-27b` using the unchanged 50-case dataset and DuckDB backend.

## Comparison

| Metric | Local Qwen 3.5 9B | Groq Qwen 3.6 27B |
|---|---:|---:|
| Overall case pass rate | 22.0% | 50.0% |
| Structured output | 100.0% (50/50) | 100.0% (8/8) |
| Graph completion | 86.0% (43/50) | 100.0% (8/8) |
| Relevant-table accuracy | 97.8% (44/45) | 100.0% (8/8) |
| Execution success | 86.7% (39/45) | 100.0% (8/8) |
| SQL result accuracy | 17.8% (8/45) | 50.0% (4/8) |
| Clarification behavior | 0.0% (0/2) | N/A |
| Security/adversarial | 100.0% (3/3) | N/A |
| Hallucinated tables | 0 | 0 |
| Hallucinated columns | 11 | 0 |

## Category Accuracy

| Segment | Local Qwen 3.5 9B | Groq Qwen 3.6 27B |
|---|---:|---:|
| aggregation | 14.3% (1/7) | 0.0% (0/3) |
| ambiguity | 0.0% (0/2) | N/A |
| comparative_analytics | 20.0% (1/5) | N/A |
| cte_subquery | 0.0% (0/5) | 0.0% (0/1) |
| follow_up | 33.3% (1/3) | N/A |
| multi_table_join | 0.0% (0/8) | N/A |
| security_adversarial | 100.0% (3/3) | N/A |
| simple_lookup | 66.7% (4/6) | 100.0% (4/4) |
| temporal_reasoning | 16.7% (1/6) | N/A |
| window_function | 0.0% (0/5) | N/A |

## Difficulty Accuracy

| Segment | Local Qwen 3.5 9B | Groq Qwen 3.6 27B |
|---|---:|---:|
| easy | 41.7% (5/12) | 57.1% (4/7) |
| hard | 17.6% (3/17) | 0.0% (0/1) |
| medium | 14.3% (3/21) | N/A |

## Language Accuracy

| Segment | Local Qwen 3.5 9B | Groq Qwen 3.6 27B |
|---|---:|---:|
| ar | 37.5% (3/8) | N/A |
| en | 12.1% (4/33) | 50.0% (4/8) |
| mixed | 44.4% (4/9) | N/A |

## Performance

- Groq model calls: 50
- Groq tokens: 10574 total
- Groq model latency: 250.928 ms average, 112.240 ms median, 636.739 ms p95
- Groq infrastructure failures: 42 (`rate_limited`: `42`)

## Hallucinated Columns

| Case | Local | Groq |
|---|---|---|
| `ambiguity_performance_ar` | invoices.invoice_id, projects.project_id | none |
| `compare_project_budget_utilization` | projects.project_id | none |
| `cte_projects_above_average_cost` | pt.name | none |
| `join_active_project_staffing` | projects.project_id | none |
| `join_customer_revenue` | invoices.quantity | none |
| `join_invoice_customer_totals` | invoices.invoice_id | none |
| `join_project_margin` | invoices.quantity, invoices.unit_price, projects.project_id | none |
| `window_project_cost_rank` | projects.project_id | none |

## Failed Groq Cases

| Case ID | Failed metrics | Error |
|---|---|---|
| `lookup_employee_arabic_name` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `lookup_project_budget` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `aggregate_active_headcount_department` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `aggregate_total_active_salary` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `aggregate_average_active_salary` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `aggregate_customers_by_status` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `aggregate_invoice_totals` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `aggregate_project_cost_category` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `aggregate_february_net_payroll` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `join_active_employees_departments` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `join_projects_customer_department` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `join_invoice_customer_totals` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `join_project_assignments` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `join_active_project_staffing` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `join_customer_revenue` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `join_project_margin` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `join_department_project_budget` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `cte_department_above_company_average` | sql.result_accuracy | Failed metrics: sql.result_accuracy |
| `subquery_employee_above_department_average` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `cte_customer_revenue_threshold` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `cte_projects_above_average_cost` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `subquery_departments_minimum_headcount` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `window_highest_paid_department` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `window_top_two_salaries` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `window_cumulative_invoice_revenue` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `window_project_cost_rank` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `window_monthly_payroll_change` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `temporal_hires_in_2022` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `temporal_active_hires_by_year` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `temporal_invoices_due_march` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `temporal_february_project_costs` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `temporal_payroll_by_month` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `temporal_projects_active_on_date` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `compare_highest_department_payroll` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `compare_customer_invoice_difference` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `compare_project_budget_utilization` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `compare_engineering_finance_headcount` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `compare_customer_status_counts` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `followup_second_department_payroll` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `followup_engineering_headcount` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `followup_higher_active_project_budget` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `ambiguity_revenue_definition` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `ambiguity_performance_ar` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `security_delete_employees` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `security_multiple_statement_drop` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
| `security_update_salary_ar` | runtime failure | Infrastructure error: rate_limited (RateLimitError, HTTP 429, code rate_limit_exceeded). |
