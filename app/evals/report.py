from typing import Any

from app.data.gateway import TableMetadata
from app.evals.models import DimensionSummary, EvaluationSummary, MetricAggregate
from app.evals.sql_diagnostics import summarize_hallucinations


def render_cloud_comparison(
    deterministic: EvaluationSummary,
    cloud: EvaluationSummary | None,
    *,
    blocker: str | None = None,
) -> str:
    lines = [
        "# Real Cloud LLM Evaluation Baseline",
        "",
        (
            "The deterministic baseline validates application and test contracts. The real cloud "
            "baseline measures actual language-to-SQL generation, execution, and grounded-answer "
            "behavior. Deterministic reference SQL is never used by the configured LLM runtime."
        ),
        "",
    ]
    if cloud is None:
        lines.extend(
            [
                "## Status",
                "",
                f"Cloud evaluation pending: {blocker or 'a configured cloud run is required.'}",
                "",
                f"Dataset SHA-256: `{deterministic.dataset_sha256 or 'not recorded'}`",
                "",
                "The table below intentionally leaves cloud measurements pending; no synthetic "
                "or deterministic result is represented as real model accuracy.",
                "",
                _metric_table(deterministic, None),
            ]
        )
        return "\n".join(lines).rstrip() + "\n"

    lines.extend(
        [
            "## Run Identity",
            "",
            f"- Dataset SHA-256: `{cloud.dataset_sha256 or 'not recorded'}`",
            f"- Database backend: `{cloud.backend}`",
            f"- Configured aliases: {_format_mapping(cloud.configured_models)}",
            f"- Providers reported by LiteLLM: {_format_mapping(cloud.performance.provider_calls)}",
            f"- Models reported by LiteLLM: {_format_mapping(cloud.performance.model_calls)}",
            "",
            "## Overall Comparison",
            "",
            _metric_table(deterministic, cloud),
            "",
            "## Performance",
            "",
            f"- Cloud calls: {cloud.performance.llm_call_count}",
            f"- Tokens: {_format_optional_int(cloud.performance.total_tokens)} total "
            f"({_format_optional_int(cloud.performance.prompt_tokens)} input, "
            f"{_format_optional_int(cloud.performance.completion_tokens)} output)",
            f"- Cached tokens: {_format_optional_int(cloud.performance.cached_tokens)}",
            f"- Estimated/actual cost: {_format_cost(cloud.performance.total_cost_usd)}",
            f"- Retry count: {_format_optional_int(cloud.performance.total_retries)}",
            f"- LLM latency: {cloud.performance.average_llm_latency_ms:.3f} ms average, "
            f"{cloud.performance.p50_llm_latency_ms:.3f} ms p50, "
            f"{cloud.performance.p95_llm_latency_ms:.3f} ms p95",
            f"- SQL execution latency: {cloud.performance.average_database_latency_ms:.3f} "
            "ms average",
            f"- Total latency: {cloud.performance.average_total_latency_ms:.3f} ms average, "
            f"{cloud.performance.p50_total_latency_ms:.3f} ms p50, "
            f"{cloud.performance.p95_total_latency_ms:.3f} ms p95",
            "",
            "## Accuracy By Category",
            "",
            _dimension_table(deterministic.by_category, cloud.by_category),
            "",
            "## Accuracy By Difficulty",
            "",
            _dimension_table(deterministic.by_difficulty, cloud.by_difficulty),
            "",
            "## Accuracy By Language",
            "",
            _dimension_table(deterministic.by_language, cloud.by_language),
            "",
            "## Failed Cases",
            "",
            _failed_cases_table(cloud),
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def render_local_sql_baseline(summary: EvaluationSummary) -> str:
    lines = [
        "# Local Qwen Text-to-SQL Baseline",
        "",
        "This is a real-model SQL-focused evaluation. The configured model generated runtime "
        "SQL; reference SQL was used only for expected-result comparison.",
        "",
        "## Run Identity",
        "",
        f"- Dataset SHA-256: `{summary.dataset_sha256 or 'not recorded'}`",
        f"- Database backend: `{summary.backend}`",
        f"- Evaluation mode: `{summary.evaluation_mode}`",
        f"- Configured aliases: {_format_mapping(summary.configured_models)}",
        f"- Providers reported by LiteLLM: {_format_mapping(summary.performance.provider_calls)}",
        f"- Models reported by LiteLLM: {_format_mapping(summary.performance.model_calls)}",
        "",
        "## SQL Results",
        "",
        f"- Scored cases: {summary.scored_cases}/{summary.total_cases}",
        f"- Overall pass rate: {summary.pass_rate:.1%}",
        f"- Parse validity: {_format_metric(summary.sql['parse_validity'])}",
        f"- Safety validity: {_format_metric(summary.sql['safety_validation'])}",
        f"- Relevant-table accuracy: {_format_metric(summary.sql['relevant_tables'])}",
        f"- Execution success: {_format_metric(summary.sql['execution_success'])}",
        f"- Result accuracy: {_format_metric(summary.sql['result_accuracy'])}",
        f"- Clarification behavior: {_format_metric(summary.security['clarification_behavior'])}",
        f"- Adversarial outcomes: {_format_metric(summary.security['adversarial_case_outcomes'])}",
        f"- Infrastructure failures: {summary.infrastructure_failures} "
        f"({_format_mapping(summary.infrastructure_errors)})",
        "",
        "## Performance",
        "",
        f"- Model calls: {summary.performance.llm_call_count}",
        f"- Model latency: {summary.performance.average_llm_latency_ms:.3f} ms average, "
        f"{summary.performance.p50_llm_latency_ms:.3f} ms p50, "
        f"{summary.performance.p95_llm_latency_ms:.3f} ms p95",
        f"- Database latency: {summary.performance.average_database_latency_ms:.3f} ms average",
        f"- Total latency: {summary.performance.average_total_latency_ms:.3f} ms average, "
        f"{summary.performance.p50_total_latency_ms:.3f} ms p50, "
        f"{summary.performance.p95_total_latency_ms:.3f} ms p95",
        "",
        "## Accuracy By Category",
        "",
        _single_dimension_table(summary.by_category),
        "",
        "## Accuracy By Difficulty",
        "",
        _single_dimension_table(summary.by_difficulty),
        "",
        "## Accuracy By Language",
        "",
        _single_dimension_table(summary.by_language),
        "",
        "## Failed Cases",
        "",
        _failed_cases_table(summary),
    ]
    return "\n".join(lines).rstrip() + "\n"


def render_groq_qwen_comparison(
    local: EvaluationSummary,
    cloud: EvaluationSummary | None,
    *,
    schema: list[TableMetadata],
    blocker: str | None = None,
) -> str:
    lines = [
        "# Groq Qwen 3.6 27B Cloud Baseline",
        "",
        "This compares the frozen local `qwen3.5:9b` SQL-only baseline with Groq-hosted "
        "`qwen/qwen3.6-27b` using the unchanged 50-case dataset and DuckDB backend.",
        "",
    ]
    if cloud is None:
        lines.extend(
            [
                "## Status",
                "",
                f"Cloud evaluation pending: {blocker or 'GROQ_API_KEY is not configured.'}",
                "",
                "No cloud accuracy or latency values are reported before a genuine Groq run.",
                "",
                f"- Dataset SHA-256: `{local.dataset_sha256 or 'not recorded'}`",
                "- Frozen local SQL result accuracy: 17.8% (8/45)",
                "- Required LiteLLM identifier: `groq/qwen/qwen3.6-27b`",
            ]
        )
        return "\n".join(lines).rstrip() + "\n"

    local_tables, local_columns, _, local_column_cases = summarize_hallucinations(local, schema)
    cloud_tables, cloud_columns, _, cloud_column_cases = summarize_hallucinations(cloud, schema)
    lines.extend(
        [
            "## Comparison",
            "",
            "| Metric | Local Qwen 3.5 9B | Groq Qwen 3.6 27B |",
            "|---|---:|---:|",
            _comparison_metric_row("Overall case pass rate", local.pass_rate, cloud.pass_rate),
            _comparison_aggregate_row(
                "Structured output",
                local.workflow["structured_output_validity"],
                cloud.workflow["structured_output_validity"],
            ),
            _comparison_aggregate_row(
                "Graph completion",
                local.workflow["graph_completion"],
                cloud.workflow["graph_completion"],
            ),
            _comparison_aggregate_row(
                "Relevant-table accuracy",
                local.sql["relevant_tables"],
                cloud.sql["relevant_tables"],
            ),
            _comparison_aggregate_row(
                "Execution success",
                local.sql["execution_success"],
                cloud.sql["execution_success"],
            ),
            _comparison_aggregate_row(
                "SQL result accuracy",
                local.sql["result_accuracy"],
                cloud.sql["result_accuracy"],
            ),
            _comparison_aggregate_row(
                "Clarification behavior",
                local.security["clarification_behavior"],
                cloud.security["clarification_behavior"],
            ),
            _comparison_aggregate_row(
                "Security/adversarial",
                local.security["adversarial_case_outcomes"],
                cloud.security["adversarial_case_outcomes"],
            ),
            f"| Hallucinated tables | {local_tables} | {cloud_tables} |",
            f"| Hallucinated columns | {local_columns} | {cloud_columns} |",
            "",
            "## Category Accuracy",
            "",
            _dimension_table(
                local.by_category,
                cloud.by_category,
                left_label="Local Qwen 3.5 9B",
                right_label="Groq Qwen 3.6 27B",
            ),
            "",
            "## Difficulty Accuracy",
            "",
            _dimension_table(
                local.by_difficulty,
                cloud.by_difficulty,
                left_label="Local Qwen 3.5 9B",
                right_label="Groq Qwen 3.6 27B",
            ),
            "",
            "## Language Accuracy",
            "",
            _dimension_table(
                local.by_language,
                cloud.by_language,
                left_label="Local Qwen 3.5 9B",
                right_label="Groq Qwen 3.6 27B",
            ),
            "",
            "## Performance",
            "",
            f"- Groq model calls: {cloud.performance.llm_call_count}",
            f"- Groq tokens: {_format_optional_int(cloud.performance.total_tokens)} total",
            f"- Groq model latency: {cloud.performance.average_llm_latency_ms:.3f} ms average, "
            f"{cloud.performance.p50_llm_latency_ms:.3f} ms median, "
            f"{cloud.performance.p95_llm_latency_ms:.3f} ms p95",
            f"- Groq infrastructure failures: {cloud.infrastructure_failures} "
            f"({_format_mapping(cloud.infrastructure_errors)})",
            "",
            "## Hallucinated Columns",
            "",
            _hallucination_cases(local_column_cases, cloud_column_cases),
            "",
            "## Failed Groq Cases",
            "",
            _failed_cases_table(cloud),
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def render_cerebras_comparison(
    local: EvaluationSummary,
    groq: EvaluationSummary | None,
    cerebras: EvaluationSummary,
    *,
    schema: list[TableMetadata],
) -> str:
    local_tables, local_columns, _, _ = summarize_hallucinations(local, schema)
    cerebras_tables, cerebras_columns, _, _ = summarize_hallucinations(cerebras, schema)
    groq_tables = 0
    groq_columns = 0
    if groq is not None:
        groq_tables, groq_columns, _, _ = summarize_hallucinations(groq, schema)

    lines = [
        "# Cerebras GPT-OSS 120B Cloud Baseline",
        "",
        "This SQL-only benchmark uses the unchanged 50-case dataset and DuckDB backend. "
        "Cerebras generated runtime SQL through the existing LLMGateway and LiteLLM path; "
        "reference SQL was used only for expected-result comparison.",
        "",
        "## Run Identity",
        "",
        f"- Dataset SHA-256: `{cerebras.dataset_sha256 or 'not recorded'}`",
        f"- Configured aliases: {_format_mapping(cerebras.configured_models)}",
        f"- Providers reported by LiteLLM: {_format_mapping(cerebras.performance.provider_calls)}",
        f"- Models reported by LiteLLM: {_format_mapping(cerebras.performance.model_calls)}",
        "",
        "## Baseline Comparison",
        "",
        "The Groq column is the available rate-limited run, not a complete 50-case model "
        "measurement. Infrastructure failures are excluded from its scored metrics.",
        "",
        "| Metric | Local Qwen 3.5 9B | Groq Qwen 3.6 27B | Cerebras GPT-OSS 120B |",
        "|---|---:|---:|---:|",
        _three_count_row("Scored cases", local, groq, cerebras),
        _three_rate_row("All-case pass rate", local, groq, cerebras),
        _three_summary_rate_row("Scored-case pass rate", local, groq, cerebras),
        _three_aggregate_row(
            "Structured output",
            local.workflow["structured_output_validity"],
            groq.workflow["structured_output_validity"] if groq else None,
            cerebras.workflow["structured_output_validity"],
        ),
        _three_aggregate_row(
            "Relevant-table accuracy",
            local.sql["relevant_tables"],
            groq.sql["relevant_tables"] if groq else None,
            cerebras.sql["relevant_tables"],
        ),
        _three_aggregate_row(
            "SQL execution success",
            local.sql["execution_success"],
            groq.sql["execution_success"] if groq else None,
            cerebras.sql["execution_success"],
        ),
        _three_aggregate_row(
            "SQL result accuracy",
            local.sql["result_accuracy"],
            groq.sql["result_accuracy"] if groq else None,
            cerebras.sql["result_accuracy"],
        ),
        _three_aggregate_row(
            "Clarification accuracy",
            local.security["clarification_behavior"],
            groq.security["clarification_behavior"] if groq else None,
            cerebras.security["clarification_behavior"],
        ),
        _three_aggregate_row(
            "Security/adversarial accuracy",
            local.security["adversarial_case_outcomes"],
            groq.security["adversarial_case_outcomes"] if groq else None,
            cerebras.security["adversarial_case_outcomes"],
        ),
        f"| Hallucinated tables | {local_tables} | "
        f"{groq_tables if groq else 'N/A'} | {cerebras_tables} |",
        f"| Hallucinated columns | {local_columns} | "
        f"{groq_columns if groq else 'N/A'} | {cerebras_columns} |",
        "",
        "## Category Accuracy",
        "",
        _three_dimension_table(
            local.by_category,
            groq.by_category if groq else {},
            cerebras.by_category,
        ),
        "",
        "## Difficulty Accuracy",
        "",
        _three_dimension_table(
            local.by_difficulty,
            groq.by_difficulty if groq else {},
            cerebras.by_difficulty,
        ),
        "",
        "## Language Accuracy",
        "",
        _three_dimension_table(
            local.by_language,
            groq.by_language if groq else {},
            cerebras.by_language,
        ),
        "",
        "## Performance",
        "",
        _performance_comparison(local, groq, cerebras),
        "",
        "## Failed Cerebras Cases",
        "",
        _failed_cases_table(cerebras),
    ]
    return "\n".join(lines).rstrip() + "\n"


def _three_count_row(
    label: str,
    local: EvaluationSummary,
    groq: EvaluationSummary | None,
    cerebras: EvaluationSummary,
) -> str:
    groq_value = f"{groq.scored_cases}/{groq.total_cases}" if groq else "N/A"
    return (
        f"| {label} | {local.scored_cases}/{local.total_cases} | {groq_value} | "
        f"{cerebras.scored_cases}/{cerebras.total_cases} |"
    )


def _three_rate_row(
    label: str,
    local: EvaluationSummary,
    groq: EvaluationSummary | None,
    cerebras: EvaluationSummary,
) -> str:
    def rate(summary: EvaluationSummary) -> float:
        return summary.passed_cases / summary.total_cases if summary.total_cases else 0.0

    groq_value = f"{rate(groq):.1%}" if groq else "N/A"
    return f"| {label} | {rate(local):.1%} | {groq_value} | {rate(cerebras):.1%} |"


def _three_summary_rate_row(
    label: str,
    local: EvaluationSummary,
    groq: EvaluationSummary | None,
    cerebras: EvaluationSummary,
) -> str:
    groq_value = f"{groq.pass_rate:.1%}" if groq else "N/A"
    return f"| {label} | {local.pass_rate:.1%} | {groq_value} | {cerebras.pass_rate:.1%} |"


def _three_aggregate_row(
    label: str,
    local: MetricAggregate,
    groq: MetricAggregate | None,
    cerebras: MetricAggregate,
) -> str:
    return (
        f"| {label} | {_format_metric(local)} | {_format_metric(groq)} | "
        f"{_format_metric(cerebras)} |"
    )


def _three_dimension_table(
    local: dict[str, DimensionSummary],
    groq: dict[str, DimensionSummary],
    cerebras: dict[str, DimensionSummary],
) -> str:
    rows = [
        "| Segment | Local Qwen 3.5 9B | Groq Qwen 3.6 27B | Cerebras GPT-OSS 120B |",
        "|---|---:|---:|---:|",
    ]
    for name in sorted(set(local) | set(groq) | set(cerebras)):
        rows.append(
            f"| {name} | {_format_dimension(local.get(name))} | "
            f"{_format_dimension(groq.get(name))} | "
            f"{_format_dimension(cerebras.get(name))} |"
        )
    return "\n".join(rows)


def _performance_comparison(
    local: EvaluationSummary,
    groq: EvaluationSummary | None,
    cerebras: EvaluationSummary,
) -> str:
    rows = [
        "| Metric | Local Qwen 3.5 9B | Groq Qwen 3.6 27B | Cerebras GPT-OSS 120B |",
        "|---|---:|---:|---:|",
    ]
    summaries = (local, groq, cerebras)
    rows.extend(
        [
            "| Model calls | "
            + " | ".join(
                str(value.performance.llm_call_count) if value else "N/A" for value in summaries
            )
            + " |",
            "| Total tokens | "
            + " | ".join(
                _format_optional_int(value.performance.total_tokens) if value else "N/A"
                for value in summaries
            )
            + " |",
            "| Average model latency | "
            + " | ".join(
                f"{value.performance.average_llm_latency_ms:.3f} ms" if value else "N/A"
                for value in summaries
            )
            + " |",
            "| Median model latency | "
            + " | ".join(
                f"{value.performance.p50_llm_latency_ms:.3f} ms" if value else "N/A"
                for value in summaries
            )
            + " |",
            "| p95 model latency | "
            + " | ".join(
                f"{value.performance.p95_llm_latency_ms:.3f} ms" if value else "N/A"
                for value in summaries
            )
            + " |",
            "| Infrastructure failures | "
            + " | ".join(
                str(value.infrastructure_failures) if value else "N/A" for value in summaries
            )
            + " |",
        ]
    )
    rows.extend(
        [
            "",
            f"Cerebras infrastructure categories: "
            f"{_format_mapping(cerebras.infrastructure_errors)}.",
        ]
    )
    return "\n".join(rows)


def _comparison_metric_row(label: str, local: float, cloud: float) -> str:
    return f"| {label} | {local:.1%} | {cloud:.1%} |"


def _comparison_aggregate_row(
    label: str,
    local: MetricAggregate,
    cloud: MetricAggregate,
) -> str:
    return f"| {label} | {_format_metric(local)} | {_format_metric(cloud)} |"


def _hallucination_cases(
    local: dict[str, list[str]],
    cloud: dict[str, list[str]],
) -> str:
    if not local and not cloud:
        return "No hallucinated columns detected."
    rows = ["| Case | Local | Groq |", "|---|---|---|"]
    for case_id in sorted(set(local) | set(cloud)):
        rows.append(
            f"| `{case_id}` | {', '.join(local.get(case_id, [])) or 'none'} | "
            f"{', '.join(cloud.get(case_id, [])) or 'none'} |"
        )
    return "\n".join(rows)


def _single_dimension_table(values: dict[str, DimensionSummary]) -> str:
    rendered = ["| Segment | Accuracy |", "|---|---:|"]
    rendered.extend(
        f"| {name} | {_format_dimension(value)} |" for name, value in sorted(values.items())
    )
    return "\n".join(rendered)


def _metric_table(
    deterministic: EvaluationSummary,
    cloud: EvaluationSummary | None,
) -> str:
    rows: list[tuple[str, MetricAggregate | float | int, MetricAggregate | float | int | None]] = [
        ("Overall case pass rate", deterministic.pass_rate, cloud.pass_rate if cloud else None),
        (
            "Graph completion",
            deterministic.workflow["graph_completion"],
            cloud.workflow["graph_completion"] if cloud else None,
        ),
        (
            "Structured output validity",
            deterministic.workflow["structured_output_validity"],
            cloud.workflow["structured_output_validity"] if cloud else None,
        ),
        (
            "SQL parse validity",
            deterministic.sql["parse_validity"],
            cloud.sql["parse_validity"] if cloud else None,
        ),
        (
            "SQL safety validity",
            deterministic.sql["safety_validation"],
            cloud.sql["safety_validation"] if cloud else None,
        ),
        (
            "SQL execution success",
            deterministic.sql["execution_success"],
            cloud.sql["execution_success"] if cloud else None,
        ),
        (
            "SQL result accuracy",
            deterministic.sql["result_accuracy"],
            cloud.sql["result_accuracy"] if cloud else None,
        ),
        (
            "Answer accuracy",
            deterministic.answer["answer_accuracy"],
            cloud.answer["answer_accuracy"] if cloud else None,
        ),
        (
            "Numerical grounding",
            deterministic.answer["numeric_grounding"],
            cloud.answer["numeric_grounding"] if cloud else None,
        ),
        (
            "Provenance completeness",
            deterministic.answer["provenance_completeness"],
            cloud.answer["provenance_completeness"] if cloud else None,
        ),
        (
            "Mutation/adversarial outcomes",
            deterministic.security["adversarial_case_outcomes"],
            cloud.security["adversarial_case_outcomes"] if cloud else None,
        ),
        (
            "Clarification behavior",
            deterministic.security["clarification_behavior"],
            cloud.security["clarification_behavior"] if cloud else None,
        ),
    ]
    rendered = ["| Metric | Deterministic | Real cloud LLM |", "|---|---:|---:|"]
    rendered.extend(
        f"| {name} | {_format_metric(deterministic_value)} | {_format_metric(cloud_value)} |"
        for name, deterministic_value, cloud_value in rows
    )
    deterministic_unsupported = deterministic.answer["unsupported_claim_failures"]
    cloud_unsupported = cloud.answer["unsupported_claim_failures"] if cloud else None
    rendered.append(
        "| Unsupported-claim failures | "
        f"{_format_count(deterministic_unsupported)} | {_format_count(cloud_unsupported)} |"
    )
    return "\n".join(rendered)


def _dimension_table(
    deterministic: dict[str, DimensionSummary],
    cloud: dict[str, DimensionSummary],
    *,
    left_label: str = "Deterministic",
    right_label: str = "Real cloud LLM",
) -> str:
    rendered = [f"| Segment | {left_label} | {right_label} |", "|---|---:|---:|"]
    for name in sorted(set(deterministic) | set(cloud)):
        rendered.append(
            f"| {name} | {_format_dimension(deterministic.get(name))} | "
            f"{_format_dimension(cloud.get(name))} |"
        )
    return "\n".join(rendered)


def _failed_cases_table(summary: EvaluationSummary) -> str:
    failed = [result for result in summary.results if not result.passed]
    if not failed:
        return "No failed cases."
    rendered = ["| Case ID | Failed metrics | Error |", "|---|---|---|"]
    for result in failed:
        metrics = ", ".join(result.failed_metrics) or "runtime failure"
        error = (result.error or "unspecified").replace("|", "\\|").replace("\n", " ")
        rendered.append(f"| `{result.case_id}` | {metrics} | {error} |")
    return "\n".join(rendered)


def _format_metric(value: MetricAggregate | float | int | None) -> str:
    if isinstance(value, MetricAggregate):
        if value.accuracy is None:
            return "N/A"
        return f"{value.accuracy:.1%} ({value.passed}/{value.applicable})"
    if isinstance(value, int | float):
        return f"{value:.1%}"
    return "Pending"


def _format_dimension(value: DimensionSummary | None) -> str:
    if value is None:
        return "N/A"
    return f"{value.accuracy:.1%} ({value.passed}/{value.total})"


def _format_mapping(values: dict[str, Any]) -> str:
    if not values:
        return "not reported"
    return ", ".join(f"`{key}`: `{value}`" for key, value in sorted(values.items()))


def _format_optional_int(value: int | None) -> str:
    return str(value) if value is not None else "not reported"


def _format_cost(value: float | None) -> str:
    return f"${value:.6f}" if value is not None else "not reported by LiteLLM"


def _format_count(value: MetricAggregate | int | None) -> str:
    return str(value) if isinstance(value, int) else "Pending"
