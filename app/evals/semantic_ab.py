import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from app.evals.models import DimensionSummary, EvaluationResult, EvaluationSummary, MetricAggregate

type PairwiseClassification = Literal[
    "BOTH_PASS",
    "BOTH_FAIL",
    "INMEMORY_ONLY_PASS",
    "WREN_ONLY_PASS",
    "DIFFERENT_FAILURE",
]

EXPECTED_MODEL = "vertex_ai/gemini-2.5-flash"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare frozen InMemory and Wren evaluations.")
    parser.add_argument("--inmemory", type=Path, required=True)
    parser.add_argument("--wren", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preflight", type=Path)
    args = parser.parse_args(argv)

    inmemory = _load(args.inmemory)
    wren = _load(args.wren)
    _validate_experiment(inmemory, wren)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    preflight = _load_preflight(args.preflight) if args.preflight is not None else []
    args.output.write_text(render_semantic_ab(inmemory, wren, preflight), encoding="utf-8")
    return 0


def render_semantic_ab(
    inmemory: EvaluationSummary,
    wren: EvaluationSummary,
    preflight: list[dict[str, object]] | None = None,
) -> str:
    pairs = _pairwise(inmemory, wren)
    recommendation = _recommend(inmemory, wren, pairs)
    lines = [
        "# InMemory vs Wren: Gemini 2.5 Flash",
        "",
        "## Experiment Contract",
        "",
        f"- Dataset SHA-256: `{inmemory.dataset_sha256}`",
        "- Database: `duckdb`",
        "- Evaluator: `2.0`, SQL-only mode",
        f"- Logical aliases: both resolve to `{EXPECTED_MODEL}`",
        "- SQL generation: existing `LLMGateway` / `sql-reasoner` path",
        "- Only experimental variable: `SEMANTIC_PROVIDER`",
        "",
        *_preflight_section(preflight or []),
        "## Summary",
        "",
        "| Metric | InMemory | Wren | Delta |",
        "|---|---:|---:|---:|",
    ]
    for label, left, right, percent in _headline_metrics(inmemory, wren):
        lines.append(
            f"| {label} | {_format(left, percent)} | {_format(right, percent)} | "
            f"{_format_delta(left, right, percent)} |"
        )

    lines.extend(["", "## Pairwise Outcomes", ""])
    for classification in (
        "WREN_ONLY_PASS",
        "INMEMORY_ONLY_PASS",
        "BOTH_PASS",
        "BOTH_FAIL",
        "DIFFERENT_FAILURE",
    ):
        case_ids = [pair[0] for pair in pairs if pair[1] == classification]
        lines.append(f"- **{classification}:** {', '.join(case_ids) if case_ids else 'none'}")

    lines.extend(
        [
            "",
            "| Case | Classification | Evidence-based difference |",
            "|---|---|---|",
        ]
    )
    for case_id, classification, reason in pairs:
        lines.append(f"| `{case_id}` | {classification} | {reason} |")

    lines.extend(["", "## Quality Breakdowns", ""])
    lines.extend(_dimension_table("Category", inmemory.by_category, wren.by_category))
    lines.extend([""])
    lines.extend(_dimension_table("Difficulty", inmemory.by_difficulty, wren.by_difficulty))
    lines.extend([""])
    lines.extend(_dimension_table("Language", inmemory.by_language, wren.by_language))

    lines.extend(
        [
            "",
            "## Semantic Context",
            "",
            "| Metric | InMemory | Wren | Delta |",
            "|---|---:|---:|---:|",
            *_semantic_rows(inmemory, wren),
            "",
            f"- InMemory missing context: {_context_cases(inmemory, missing=True)}.",
            f"- Wren missing context: {_context_cases(wren, missing=True)}.",
            f"- InMemory irrelevant context: {_context_cases(inmemory, missing=False)}.",
            f"- Wren irrelevant context: {_context_cases(wren, missing=False)}.",
            "",
            "Missing context means at least one frozen `relevant_tables` entry was absent. "
            "Irrelevant context means selected tables exceeded that case's frozen "
            "relevant-table set; "
            "it is a diagnostic, not an automatic quality failure.",
            "Each arm contains one cloud-model sample per case. Provider-only passes are not "
            "treated as causally semantic unless the recorded contexts supply a concrete "
            "difference; otherwise they remain model/output variation.",
            "",
            "## Performance And Cost",
            "",
            f"- InMemory calls/tokens: {_usage_summary(inmemory)}.",
            f"- Wren calls/tokens: {_usage_summary(wren)}.",
            f"- InMemory model latency avg/p50/p95: {_latency_summary(inmemory, 'llm')} ms.",
            f"- Wren model latency avg/p50/p95: {_latency_summary(wren, 'llm')} ms.",
            f"- InMemory semantic latency avg/p50/p95: "
            f"{_latency_summary(inmemory, 'semantic')} ms.",
            f"- Wren semantic latency avg/p50/p95: {_latency_summary(wren, 'semantic')} ms.",
            f"- InMemory total latency avg/p50/p95: {_latency_summary(inmemory, 'total')} ms.",
            f"- Wren total latency avg/p50/p95: {_latency_summary(wren, 'total')} ms.",
            f"- Average database latency: InMemory "
            f"{inmemory.performance.average_database_latency_ms:.3f} ms; Wren "
            f"{wren.performance.average_database_latency_ms:.3f} ms.",
            f"- InMemory Vertex cost: {_money(inmemory.performance.total_cost_usd)}.",
            f"- Wren Vertex cost: {_money(wren.performance.total_cost_usd)}.",
            f"- Approximate Wren semantic overhead: "
            f"{_semantic_latency_delta(inmemory, wren):+.3f} ms/request.",
            "",
            "## Architecture Recommendation",
            "",
            f"**{recommendation}**",
            "",
            _recommendation_reason(recommendation, inmemory, wren, pairs),
            "",
        ]
    )
    return "\n".join(lines)


def _load(path: Path) -> EvaluationSummary:
    return EvaluationSummary.model_validate_json(path.read_text(encoding="utf-8"))


def _load_preflight(path: Path) -> list[dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise ValueError("Pre-flight context artifact must contain a JSON list of objects.")
    return payload


def _validate_experiment(inmemory: EvaluationSummary, wren: EvaluationSummary) -> None:
    errors: list[str] = []
    if not inmemory.dataset_sha256 or inmemory.dataset_sha256 != wren.dataset_sha256:
        errors.append("dataset hashes differ")
    if inmemory.backend != "duckdb" or wren.backend != "duckdb":
        errors.append("both backends must be duckdb")
    if inmemory.llm_backend != "configured" or wren.llm_backend != "configured":
        errors.append("both LLM backends must be configured")
    if inmemory.evaluation_mode != "sql" or wren.evaluation_mode != "sql":
        errors.append("both evaluations must use SQL-only mode")
    if inmemory.evaluator_version != "2.0" or wren.evaluator_version != "2.0":
        errors.append("both evaluations must use evaluator V2")
    if inmemory.semantic_provider != "inmemory" or wren.semantic_provider != "wren":
        errors.append("semantic provider arms are mislabeled")
    if inmemory.configured_models != wren.configured_models:
        errors.append("configured model aliases differ")
    if set(inmemory.configured_models.values()) != {EXPECTED_MODEL}:
        errors.append(f"both aliases must resolve to {EXPECTED_MODEL}")
    left_ids = [result.case_id for result in inmemory.results]
    right_ids = [result.case_id for result in wren.results]
    if left_ids != right_ids or len(left_ids) != 50:
        errors.append("case IDs or ordering differ from the 50-case contract")
    if errors:
        raise ValueError("Invalid semantic A/B experiment: " + "; ".join(errors) + ".")


def _pairwise(
    inmemory: EvaluationSummary, wren: EvaluationSummary
) -> list[tuple[str, PairwiseClassification, str]]:
    output: list[tuple[str, PairwiseClassification, str]] = []
    for left, right in zip(inmemory.results, wren.results, strict=True):
        classification = _classification(left, right)
        output.append(
            (left.case_id, classification, _difference_reason(left, right, classification))
        )
    return output


def _classification(inmemory: EvaluationResult, wren: EvaluationResult) -> PairwiseClassification:
    if inmemory.passed and wren.passed:
        return "BOTH_PASS"
    if inmemory.passed:
        return "INMEMORY_ONLY_PASS"
    if wren.passed:
        return "WREN_ONLY_PASS"
    if _failure_signature(inmemory) != _failure_signature(wren):
        return "DIFFERENT_FAILURE"
    return "BOTH_FAIL"


def _failure_signature(result: EvaluationResult) -> tuple[object, ...]:
    return (
        result.failure_type,
        result.infrastructure_error,
        tuple(result.failed_metrics),
        result.structured_action,
    )


def _difference_reason(
    inmemory: EvaluationResult,
    wren: EvaluationResult,
    classification: PairwiseClassification,
) -> str:
    if classification in {"BOTH_PASS", "BOTH_FAIL"}:
        return "Same pass outcome and failure signature."
    if classification == "DIFFERENT_FAILURE":
        return f"InMemory: {_short_failure(inmemory)}; Wren: {_short_failure(wren)}."
    winner, loser = (wren, inmemory) if classification == "WREN_ONLY_PASS" else (inmemory, wren)
    reasons: list[str] = []
    if len(winner.missing_required_context) < len(loser.missing_required_context):
        reasons.append("better table/model selection")
    if len(winner.semantic_relationship_ids) > len(loser.semantic_relationship_ids):
        reasons.append("better relationship context")
    if _semantic_names(winner.semantic_definition_ids) != _semantic_names(
        loser.semantic_definition_ids
    ):
        reasons.append("different business definitions")
    if _semantic_names(winner.semantic_measure_ids) != _semantic_names(loser.semantic_measure_ids):
        reasons.append("different calculated-field context")
    if (
        not reasons
        and winner.result_comparison is not None
        and loser.result_comparison is not None
        and loser.result_comparison.reason.startswith("required_values_not_found")
    ):
        reasons.append("other: generated output alias/result shape differed")
    if not reasons:
        reasons.append("other/model sampling within identical experiment settings")
    return ", ".join(reasons) + "."


def _semantic_names(values: list[str]) -> set[str]:
    return {value.rsplit(":", 1)[-1] for value in values}


def _short_failure(result: EvaluationResult) -> str:
    if result.infrastructure_error:
        return f"infrastructure/{result.infrastructure_error}"
    return ", ".join(result.failed_metrics) or result.failure_type or "unknown"


def _headline_metrics(
    inmemory: EvaluationSummary, wren: EvaluationSummary
) -> list[tuple[str, float | None, float | None, bool]]:
    return [
        ("Overall pass", inmemory.pass_rate, wren.pass_rate, True),
        (
            "Structured output validity",
            _metric(inmemory.workflow, "structured_output_validity"),
            _metric(wren.workflow, "structured_output_validity"),
            True,
        ),
        (
            "Relevant-table accuracy",
            _metric(inmemory.sql, "relevant_tables"),
            _metric(wren.sql, "relevant_tables"),
            True,
        ),
        (
            "SQL result accuracy",
            _metric(inmemory.sql, "result_accuracy"),
            _metric(wren.sql, "result_accuracy"),
            True,
        ),
        (
            "Execution success",
            _metric(inmemory.sql, "execution_success"),
            _metric(wren.sql, "execution_success"),
            True,
        ),
        (
            "Follow-up accuracy",
            _dimension(inmemory.by_category, "follow_up"),
            _dimension(wren.by_category, "follow_up"),
            True,
        ),
        (
            "Clarification accuracy",
            _metric(inmemory.security, "clarification_behavior"),
            _metric(wren.security, "clarification_behavior"),
            True,
        ),
        (
            "Security accuracy",
            _dimension(inmemory.by_category, "security_adversarial"),
            _dimension(wren.by_category, "security_adversarial"),
            True,
        ),
        (
            "Avg selected tables",
            inmemory.semantic.average_selected_tables,
            wren.semantic.average_selected_tables,
            False,
        ),
        ("Avg prompt tokens/call", _tokens_per_call(inmemory), _tokens_per_call(wren), False),
        (
            "Avg semantic latency ms",
            inmemory.semantic.average_retrieval_latency_ms,
            wren.semantic.average_retrieval_latency_ms,
            False,
        ),
        (
            "Avg total latency ms",
            inmemory.performance.average_total_latency_ms,
            wren.performance.average_total_latency_ms,
            False,
        ),
        (
            "Total cost USD",
            inmemory.performance.total_cost_usd,
            wren.performance.total_cost_usd,
            False,
        ),
    ]


def _semantic_rows(inmemory: EvaluationSummary, wren: EvaluationSummary) -> list[str]:
    values = [
        (
            "Average selected tables",
            inmemory.semantic.average_selected_tables,
            wren.semantic.average_selected_tables,
        ),
        (
            "Average selected models",
            inmemory.semantic.average_selected_models,
            wren.semantic.average_selected_models,
        ),
        (
            "Average relationships",
            inmemory.semantic.average_relationships,
            wren.semantic.average_relationships,
        ),
        (
            "Average definitions",
            inmemory.semantic.average_definitions,
            wren.semantic.average_definitions,
        ),
        (
            "Average calculated fields",
            inmemory.semantic.average_measures,
            wren.semantic.average_measures,
        ),
        (
            "Average context chars",
            inmemory.semantic.average_context_size_chars,
            wren.semantic.average_context_size_chars,
        ),
        (
            "Average retrieval latency ms",
            inmemory.semantic.average_retrieval_latency_ms,
            wren.semantic.average_retrieval_latency_ms,
        ),
        (
            "P50 retrieval latency ms",
            inmemory.semantic.p50_retrieval_latency_ms,
            wren.semantic.p50_retrieval_latency_ms,
        ),
        (
            "P95 retrieval latency ms",
            inmemory.semantic.p95_retrieval_latency_ms,
            wren.semantic.p95_retrieval_latency_ms,
        ),
        (
            "Missing-required-context cases",
            float(inmemory.semantic.missing_required_context_cases),
            float(wren.semantic.missing_required_context_cases),
        ),
        (
            "Irrelevant-context cases",
            float(inmemory.semantic.irrelevant_context_cases),
            float(wren.semantic.irrelevant_context_cases),
        ),
    ]
    return [
        f"| {label} | {left:.3f} | {right:.3f} | {right - left:+.3f} |"
        for label, left, right in values
    ]


def _preflight_section(preflight: list[dict[str, object]]) -> list[str]:
    if not preflight:
        return []
    lines = [
        "## Pre-flight Context Retrieval",
        "",
        "| Question | Provider | Tables/models | Relationships | Definitions/calculated fields |",
        "|---|---|---|---|---|",
    ]
    for item in preflight:
        question = str(item.get("question", ""))
        for provider in ("inmemory", "wren"):
            context = item.get(provider)
            if not isinstance(context, dict):
                continue
            tables = _join_values(context.get("tables"))
            relationships = _join_values(context.get("relationships"))
            definitions = _join_values(context.get("definitions"))
            measures = _join_values(context.get("measures"))
            semantic_items = ", ".join(value for value in (definitions, measures) if value)
            lines.append(
                f"| {question} | {provider} | {tables or 'none'} | "
                f"{relationships or 'none'} | {semantic_items or 'none'} |"
            )
    return [*lines, ""]


def _join_values(value: object) -> str:
    if not isinstance(value, list):
        return ""
    return ", ".join(f"`{item}`" for item in value)


def _dimension_table(
    title: str,
    inmemory: dict[str, DimensionSummary],
    wren: dict[str, DimensionSummary],
) -> list[str]:
    lines = [f"### By {title}", "", "| Segment | InMemory | Wren | Delta |", "|---|---:|---:|---:|"]
    for key in sorted(set(inmemory) | set(wren)):
        left = _dimension(inmemory, key)
        right = _dimension(wren, key)
        lines.append(
            f"| {key} | {_format(left, True)} | {_format(right, True)} | "
            f"{_format_delta(left, right, True)} |"
        )
    return lines


def _recommend(
    inmemory: EvaluationSummary,
    wren: EvaluationSummary,
    pairs: list[tuple[str, PairwiseClassification, str]],
) -> Literal["WREN_PRIMARY", "KEEP_BOTH_WREN_OPTIONAL", "INMEMORY_FOR_NOW", "FIX_WREN_THEN_RETEST"]:
    del pairs
    if wren.infrastructure_failures > inmemory.infrastructure_failures:
        return "FIX_WREN_THEN_RETEST"
    delta = wren.pass_rate - inmemory.pass_rate
    if (
        delta >= 0.05
        and wren.semantic.missing_required_context_cases
        <= inmemory.semantic.missing_required_context_cases
    ):
        return "WREN_PRIMARY"
    if delta <= -0.05:
        return "INMEMORY_FOR_NOW"
    return "KEEP_BOTH_WREN_OPTIONAL"


def _recommendation_reason(
    recommendation: str,
    inmemory: EvaluationSummary,
    wren: EvaluationSummary,
    pairs: list[tuple[str, PairwiseClassification, str]],
) -> str:
    wren_only = sum(pair[1] == "WREN_ONLY_PASS" for pair in pairs)
    inmemory_only = sum(pair[1] == "INMEMORY_ONLY_PASS" for pair in pairs)
    pass_delta = (wren.pass_rate - inmemory.pass_rate) * 100
    latency_delta = _semantic_latency_delta(inmemory, wren)
    return (
        f"The measured pass-rate delta is {pass_delta:+.1f} percentage points "
        f"({wren_only} Wren-only passes, {inmemory_only} InMemory-only passes). "
        f"Wren adds {latency_delta:+.1f} ms "
        "of average semantic retrieval latency and one optional service. "
        "Under the predeclared decision rule, these quality, reliability, context, and "
        f"operational measurements select `{recommendation}`."
    )


def _semantic_latency_delta(inmemory: EvaluationSummary, wren: EvaluationSummary) -> float:
    return (
        wren.semantic.average_retrieval_latency_ms - inmemory.semantic.average_retrieval_latency_ms
    )


def _metric(values: dict[str, MetricAggregate], key: str) -> float | None:
    return values[key].accuracy


def _dimension(values: dict[str, DimensionSummary], key: str) -> float | None:
    summary = values.get(key)
    return summary.accuracy if summary is not None else None


def _tokens_per_call(summary: EvaluationSummary) -> float | None:
    tokens = summary.performance.prompt_tokens
    calls = summary.performance.usage_available_calls
    return tokens / calls if tokens is not None and calls else None


def _context_cases(summary: EvaluationSummary, *, missing: bool) -> str:
    case_ids = []
    for result in summary.results:
        values = result.missing_required_context if missing else result.irrelevant_context
        if values:
            case_ids.append(f"`{result.case_id}`")
    return ", ".join(case_ids) if case_ids else "none"


def _usage_summary(summary: EvaluationSummary) -> str:
    performance = summary.performance
    return (
        f"{performance.llm_call_count} calls, {_optional_int(performance.prompt_tokens)} prompt, "
        f"{_optional_int(performance.completion_tokens)} completion, "
        f"{_optional_int(performance.total_tokens)} total tokens"
    )


def _latency_summary(summary: EvaluationSummary, kind: Literal["llm", "semantic", "total"]) -> str:
    if kind == "llm":
        values = (
            summary.performance.average_llm_latency_ms,
            summary.performance.p50_llm_latency_ms,
            summary.performance.p95_llm_latency_ms,
        )
    elif kind == "semantic":
        values = (
            summary.semantic.average_retrieval_latency_ms,
            summary.semantic.p50_retrieval_latency_ms,
            summary.semantic.p95_retrieval_latency_ms,
        )
    else:
        values = (
            summary.performance.average_total_latency_ms,
            summary.performance.p50_total_latency_ms,
            summary.performance.p95_total_latency_ms,
        )
    return "/".join(f"{value:.3f}" for value in values)


def _format(value: float | None, percent: bool) -> str:
    if value is None:
        return "N/A"
    return f"{value * 100:.1f}%" if percent else f"{value:.3f}"


def _format_delta(left: float | None, right: float | None, percent: bool) -> str:
    if left is None or right is None:
        return "N/A"
    delta = right - left
    return f"{delta * 100:+.1f} pp" if percent else f"{delta:+.3f}"


def _optional_int(value: int | None) -> str:
    return f"{value:,}" if value is not None else "unavailable"


def _money(value: float | None) -> str:
    return f"${value:.6f}" if value is not None else "unavailable"


if __name__ == "__main__":
    raise SystemExit(main())
