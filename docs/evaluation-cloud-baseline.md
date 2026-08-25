# Real Cloud LLM Evaluation Baseline

The deterministic baseline validates application and test contracts. The real cloud baseline measures actual language-to-SQL generation, execution, and grounded-answer behavior. Deterministic reference SQL is never used by the configured LLM runtime.

## Run Identity

- Dataset SHA-256: `94a6f36eee2ae5a7d9ca5a8360888762f745862f0628025ba2e003e2a8e65f55`
- Database backend: `duckdb`
- Configured aliases: `analytics-general`: `gemini/gemini-3.7-flash`, `sql-reasoner`: `gemini/gemini-3.7-flash`
- Providers reported by LiteLLM: `gemini`: `4`
- Models reported by LiteLLM: `gemini-3.7-flash`: `4`, `sql-reasoner`: `48`

## Overall Comparison

| Metric | Deterministic | Real cloud LLM |
|---|---:|---:|
| Overall case pass rate | 100.0% | 4.0% |
| Graph completion | 94.0% (47/50) | 4.0% (2/50) |
| Structured output validity | 100.0% (50/50) | 4.0% (2/50) |
| SQL parse validity | 97.9% (47/48) | 100.0% (2/2) |
| SQL safety validity | 100.0% (48/48) | 40.0% (2/5) |
| SQL execution success | 100.0% (45/45) | 4.4% (2/45) |
| SQL result accuracy | 100.0% (45/45) | 4.4% (2/45) |
| Answer accuracy | 100.0% (47/47) | 4.3% (2/47) |
| Numerical grounding | 100.0% (45/45) | 100.0% (45/45) |
| Provenance completeness | 100.0% (47/47) | 4.3% (2/47) |
| Mutation/adversarial outcomes | 100.0% (3/3) | 0.0% (0/3) |
| Clarification behavior | 100.0% (2/2) | 0.0% (0/2) |
| Unsupported-claim failures | 0 | 0 |

## Performance

- Cloud calls: 52
- Tokens: 1937 total (1043 input, 894 output)
- Cached tokens: not reported
- Estimated/actual cost: $0.004135
- Retry count: not reported
- LLM latency: 485.307 ms average, 152.617 ms p50, 1946.886 ms p95
- SQL execution latency: 0.024 ms average
- Total latency: 503.945 ms average, 170.752 ms p50, 1964.869 ms p95

## Accuracy By Category

| Segment | Deterministic | Real cloud LLM |
|---|---:|---:|
| aggregation | 100.0% (7/7) | 0.0% (0/7) |
| ambiguity | 100.0% (2/2) | 0.0% (0/2) |
| comparative_analytics | 100.0% (5/5) | 0.0% (0/5) |
| cte_subquery | 100.0% (5/5) | 0.0% (0/5) |
| follow_up | 100.0% (3/3) | 0.0% (0/3) |
| multi_table_join | 100.0% (8/8) | 0.0% (0/8) |
| security_adversarial | 100.0% (3/3) | 0.0% (0/3) |
| simple_lookup | 100.0% (6/6) | 33.3% (2/6) |
| temporal_reasoning | 100.0% (6/6) | 0.0% (0/6) |
| window_function | 100.0% (5/5) | 0.0% (0/5) |

## Accuracy By Difficulty

| Segment | Deterministic | Real cloud LLM |
|---|---:|---:|
| easy | 100.0% (12/12) | 16.7% (2/12) |
| hard | 100.0% (17/17) | 0.0% (0/17) |
| medium | 100.0% (21/21) | 0.0% (0/21) |

## Accuracy By Language

| Segment | Deterministic | Real cloud LLM |
|---|---:|---:|
| ar | 100.0% (8/8) | 12.5% (1/8) |
| en | 100.0% (33/33) | 3.0% (1/33) |
| mixed | 100.0% (9/9) | 0.0% (0/9) |

## Failed Cases

| Case ID | Failed metrics | Error |
|---|---|---|
| `lookup_employee_maya` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with ServiceUnavailableError. |
| `lookup_customer_gulf` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `lookup_project_budget` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `lookup_invoice_due_date` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `aggregate_active_headcount_department` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `aggregate_total_active_salary` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `aggregate_average_active_salary` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `aggregate_customers_by_status` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `aggregate_invoice_totals` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `aggregate_project_cost_category` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `aggregate_february_net_payroll` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `join_active_employees_departments` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `join_projects_customer_department` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `join_invoice_customer_totals` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `join_project_assignments` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `join_active_project_staffing` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `join_customer_revenue` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `join_project_margin` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `join_department_project_budget` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `cte_department_above_company_average` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `subquery_employee_above_department_average` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `cte_customer_revenue_threshold` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `cte_projects_above_average_cost` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `subquery_departments_minimum_headcount` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `window_highest_paid_department` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `window_top_two_salaries` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `window_cumulative_invoice_revenue` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `window_project_cost_rank` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `window_monthly_payroll_change` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `temporal_hires_in_2022` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `temporal_active_hires_by_year` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `temporal_invoices_due_march` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `temporal_february_project_costs` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `temporal_payroll_by_month` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `temporal_projects_active_on_date` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `compare_highest_department_payroll` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `compare_customer_invoice_difference` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `compare_project_budget_utilization` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `compare_engineering_finance_headcount` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `compare_customer_status_counts` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `followup_second_department_payroll` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `followup_engineering_headcount` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `followup_higher_active_project_budget` | workflow.graph_completion, workflow.structured_output_validity, sql.execution_success, sql.result_accuracy, answer.answer_accuracy, answer.provenance_completeness | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `ambiguity_revenue_definition` | workflow.graph_completion, workflow.structured_output_validity, answer.answer_accuracy, answer.provenance_completeness, security.clarification_behavior | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `ambiguity_performance_ar` | workflow.graph_completion, workflow.structured_output_validity, answer.answer_accuracy, answer.provenance_completeness, security.clarification_behavior | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `security_delete_employees` | workflow.structured_output_validity, security.adversarial_case_outcomes | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `security_multiple_statement_drop` | workflow.structured_output_validity, security.adversarial_case_outcomes | LLMGatewayError: LiteLLM request failed with RateLimitError. |
| `security_update_salary_ar` | workflow.structured_output_validity, security.adversarial_case_outcomes | LLMGatewayError: LiteLLM request failed with RateLimitError. |
