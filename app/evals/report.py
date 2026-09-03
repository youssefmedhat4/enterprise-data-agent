from typing import Any

from app.evals.models import DimensionSummary, EvaluationSummary, MetricAggregate


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
        "# SQL Evaluation Baseline",
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


