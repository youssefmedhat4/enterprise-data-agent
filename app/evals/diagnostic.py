import argparse
import asyncio
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, cast

import httpx
from pydantic import BaseModel, Field

from app.config import Settings
from app.data.gateway import TableMetadata
from app.evals.duckdb_gateway import DuckDBEvaluationGateway
from app.evals.loader import load_evaluation_cases
from app.evals.models import EvaluationCase, EvaluationResult, EvaluationSummary
from app.evals.runner import run_evaluations
from app.evals.sql_diagnostics import schema_hallucinations
from app.llm.factory import build_llm_gateway
from app.security.sql_validation import SQLValidator


class DiagnosticConfigurationError(RuntimeError):
    """Raised before the diagnostic when its local-model controls are invalid."""


class CaseSQLDiagnostic(BaseModel):
    case_id: str
    passed: bool
    hallucinated_tables: list[str] = Field(default_factory=list)
    hallucinated_columns: list[str] = Field(default_factory=list)
    join_failure: bool = False
    aggregation_failure: bool = False
    temporal_failure: bool = False
    clarification_correct: bool | None = None


class ModelDiagnostic(BaseModel):
    model: str
    evaluation: EvaluationSummary
    hallucinated_table_count: int
    hallucinated_column_count: int
    cases_with_hallucinated_tables: int
    cases_with_hallucinated_columns: int
    join_failures: int
    aggregation_failures: int
    temporal_failures: int
    diagnostics: list[CaseSQLDiagnostic]


class DiagnosticConclusion(BaseModel):
    classification: Literal["A", "B", "C"]
    statement: str
    evidence: list[str]


class ComparativeDiagnostic(BaseModel):
    dataset_sha256: str
    selected_case_ids: list[str]
    controls: dict[str, str | int]
    models: dict[str, ModelDiagnostic]
    conclusion: DiagnosticConclusion


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare two local Ollama SQL models.")
    parser.add_argument("--cases", type=Path, default=Path("evals/cases.json"))
    parser.add_argument(
        "--selection",
        type=Path,
        default=Path("evals/qwen_diagnostic_case_ids.json"),
    )
    parser.add_argument("--model-a", required=True)
    parser.add_argument("--model-b", required=True)
    parser.add_argument("--api-base", default="http://localhost:11434")
    parser.add_argument("--num-ctx", type=int, default=8192)
    parser.add_argument("--timeout-seconds", type=float, default=300)
    parser.add_argument("--max-output-tokens", type=int, default=2048)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        return asyncio.run(
            _run(
                cases_path=args.cases,
                selection_path=args.selection,
                models=[args.model_a, args.model_b],
                api_base=args.api_base,
                num_ctx=args.num_ctx,
                timeout_seconds=args.timeout_seconds,
                max_output_tokens=args.max_output_tokens,
                output_path=args.output,
                markdown_output_path=args.markdown_output,
            )
        )
    except DiagnosticConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2


async def _run(
    *,
    cases_path: Path,
    selection_path: Path,
    models: list[str],
    api_base: str,
    num_ctx: int,
    timeout_seconds: float,
    max_output_tokens: int,
    output_path: Path,
    markdown_output_path: Path,
) -> int:
    all_cases = load_evaluation_cases(cases_path)
    selected_ids = _load_selected_ids(selection_path)
    selected_cases = _select_cases(all_cases, selected_ids)
    installed_tags = await _installed_ollama_tags(api_base)
    physical_tags = [_physical_ollama_tag(model) for model in models]
    missing = sorted(set(physical_tags) - installed_tags)
    if missing:
        raise DiagnosticConfigurationError(
            "The requested Ollama model tags are not installed: " + ", ".join(missing)
        )

    schema = await _load_schema()
    runs: dict[str, ModelDiagnostic] = {}
    for model, physical_tag in zip(models, physical_tags, strict=True):
        settings = Settings(
            LLM_PROVIDER="litellm",
            LLM_MODEL_ANALYTICS_GENERAL=model,
            LLM_MODEL_SQL_REASONER=model,
            OLLAMA_API_BASE=api_base,
            OLLAMA_NUM_CTX=num_ctx,
            RUN_LOCAL_LLM_TESTS=True,
            RUN_CLOUD_LLM_TESTS=False,
            LLM_TIMEOUT_SECONDS=timeout_seconds,
            LLM_MAX_RETRIES=0,
            LLM_MAX_OUTPUT_TOKENS=max_output_tokens,
            LLM_REASONING_EFFORT="none",
        )
        if settings.model_provider(model) != "ollama":
            raise DiagnosticConfigurationError(
                f"Diagnostic models must use a local Ollama LiteLLM route: {model}"
            )
        summary = await run_evaluations(
            selected_cases,
            backend="duckdb",
            database_factory=DuckDBEvaluationGateway,
            llm_gateway=build_llm_gateway(settings),
            sql_validator=SQLValidator(max_rows=settings.query_row_limit),
            llm_backend="configured",
            dataset_sha256=hashlib.sha256(cases_path.read_bytes()).hexdigest(),
            configured_models=settings.model_aliases,
            evaluation_mode="sql",
        )
        runs[physical_tag] = _build_model_diagnostic(
            model=model,
            cases=selected_cases,
            summary=summary,
            schema=schema,
        )

    diagnostic = ComparativeDiagnostic(
        dataset_sha256=hashlib.sha256(cases_path.read_bytes()).hexdigest(),
        selected_case_ids=selected_ids,
        controls={
            "database_backend": "duckdb",
            "evaluation_mode": "sql",
            "ollama_api_base": api_base,
            "context_window": num_ctx,
            "max_output_tokens": max_output_tokens,
            "reasoning_effort": "none",
        },
        models=runs,
        conclusion=_conclude(runs),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(diagnostic.model_dump_json(indent=2) + "\n", encoding="utf-8")
    markdown_output_path.write_text(_render_markdown(diagnostic), encoding="utf-8")
    print(diagnostic.model_dump_json(indent=2))
    return 0


def _load_selected_ids(path: Path) -> list[str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not all(isinstance(value, str) for value in raw):
        raise DiagnosticConfigurationError("Diagnostic selection must be a JSON list of IDs.")
    selected_ids = cast(list[str], raw)
    if len(selected_ids) != len(set(selected_ids)):
        raise DiagnosticConfigurationError("Diagnostic case IDs must be unique.")
    return selected_ids


def _select_cases(cases: list[EvaluationCase], selected_ids: list[str]) -> list[EvaluationCase]:
    by_id = {case.id: case for case in cases}
    missing = [case_id for case_id in selected_ids if case_id not in by_id]
    if missing:
        raise DiagnosticConfigurationError(
            "Selected case IDs are absent from the evaluation dataset: " + ", ".join(missing)
        )
    return [by_id[case_id] for case_id in selected_ids]


async def _installed_ollama_tags(api_base: str) -> set[str]:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{api_base.rstrip('/')}/api/tags")
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise DiagnosticConfigurationError(
            f"Ollama is unavailable ({type(exc).__name__})."
        ) from exc
    return {
        str(item["name"])
        for item in response.json().get("models", [])
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }


def _physical_ollama_tag(model: str) -> str:
    provider, separator, tag = model.partition("/")
    if separator != "/" or provider not in {"ollama", "ollama_chat"} or not tag:
        raise DiagnosticConfigurationError(f"Invalid Ollama LiteLLM model identifier: {model}")
    return tag


async def _load_schema() -> list[TableMetadata]:
    database = DuckDBEvaluationGateway()
    try:
        return await database.search_schema("")
    finally:
        await database.close()


def _build_model_diagnostic(
    *,
    model: str,
    cases: list[EvaluationCase],
    summary: EvaluationSummary,
    schema: list[TableMetadata],
) -> ModelDiagnostic:
    cases_by_id = {case.id: case for case in cases}
    diagnostics = [
        _diagnose_case(cases_by_id[result.case_id], result, schema) for result in summary.results
    ]
    return ModelDiagnostic(
        model=model,
        evaluation=summary,
        hallucinated_table_count=sum(len(item.hallucinated_tables) for item in diagnostics),
        hallucinated_column_count=sum(len(item.hallucinated_columns) for item in diagnostics),
        cases_with_hallucinated_tables=sum(bool(item.hallucinated_tables) for item in diagnostics),
        cases_with_hallucinated_columns=sum(
            bool(item.hallucinated_columns) for item in diagnostics
        ),
        join_failures=sum(item.join_failure for item in diagnostics),
        aggregation_failures=sum(item.aggregation_failure for item in diagnostics),
        temporal_failures=sum(item.temporal_failure for item in diagnostics),
        diagnostics=diagnostics,
    )


def _diagnose_case(
    case: EvaluationCase,
    result: EvaluationResult,
    schema: list[TableMetadata],
) -> CaseSQLDiagnostic:
    tables, columns = schema_hallucinations(result.generated_sql, schema)
    failed = not result.passed
    return CaseSQLDiagnostic(
        case_id=case.id,
        passed=result.passed,
        hallucinated_tables=tables,
        hallucinated_columns=columns,
        join_failure=failed and case.category == "multi_table_join",
        aggregation_failure=failed and case.category == "aggregation",
        temporal_failure=failed and case.category == "temporal_reasoning",
        clarification_correct=(
            result.security.clarification_behavior
            if case.expected_security_behavior == "clarify"
            else None
        ),
    )


def _metric_accuracy(summary: EvaluationSummary, group: str, name: str) -> float:
    values = cast(Mapping[str, Any], getattr(summary, group))
    metric = values[name]
    accuracy = metric.accuracy
    return float(accuracy) if accuracy is not None else 0.0


def _conclude(runs: dict[str, ModelDiagnostic]) -> DiagnosticConclusion:
    if len(runs) != 2:
        raise DiagnosticConfigurationError("Exactly two model runs are required.")
    names = list(runs)
    first, second = runs[names[0]], runs[names[1]]
    first_accuracy = _metric_accuracy(first.evaluation, "sql", "result_accuracy")
    second_accuracy = _metric_accuracy(second.evaluation, "sql", "result_accuracy")
    delta = second_accuracy - first_accuracy
    shared_failures = len(
        {item.case_id for item in first.diagnostics if not item.passed}
        & {item.case_id for item in second.diagnostics if not item.passed}
    )
    evidence = [
        f"{names[0]} result accuracy: {first_accuracy:.1%}.",
        f"{names[1]} result accuracy: {second_accuracy:.1%}.",
        f"Absolute result-accuracy change: {delta:+.1%}.",
        f"Shared failed cases: {shared_failures}/{len(first.diagnostics)}.",
        f"Hallucinated columns: {first.hallucinated_column_count} vs "
        f"{second.hallucinated_column_count}.",
    ]
    if delta >= 0.25 and second_accuracy >= 0.60:
        return DiagnosticConclusion(
            classification="A",
            statement="Evidence suggests model capability is the dominant bottleneck.",
            evidence=evidence,
        )
    if abs(delta) <= 0.10 and second_accuracy < 0.50 and shared_failures >= 8:
        return DiagnosticConclusion(
            classification="B",
            statement=(
                "Evidence suggests context, schema, or semantic assistance is the dominant "
                "bottleneck."
            ),
            evidence=evidence,
        )
    return DiagnosticConclusion(
        classification="C",
        statement="Both model capability and context/schema assistance appear significant.",
        evidence=evidence,
    )


def _render_markdown(diagnostic: ComparativeDiagnostic) -> str:
    names = list(diagnostic.models)
    lines = [
        "# Qwen 9B vs 27B Diagnostic",
        "",
        "This diagnostic uses 12 unchanged cases from the 50-case evaluation dataset. Both "
        "models used the same SQL-only LangGraph path, prompts, schema context, SQLGlot policy, "
        "DuckDB fixture, and expected assertions.",
        "",
        f"- Dataset SHA-256: `{diagnostic.dataset_sha256}`",
        f"- Selected case IDs: {', '.join(f'`{value}`' for value in diagnostic.selected_case_ids)}",
        f"- Context window: `{diagnostic.controls['context_window']}`",
        "",
        "## Comparison",
        "",
        f"| Metric | {names[0]} | {names[1]} |",
        "|---|---:|---:|",
    ]
    metric_rows = [
        ("Result accuracy", "sql", "result_accuracy"),
        ("Execution success", "sql", "execution_success"),
        ("Relevant-table accuracy", "sql", "relevant_tables"),
        ("Structured output", "workflow", "structured_output_validity"),
        ("Clarification behavior", "security", "clarification_behavior"),
    ]
    for label, group, name in metric_rows:
        values = [
            _metric_accuracy(diagnostic.models[model].evaluation, group, name) for model in names
        ]
        lines.append(f"| {label} | {values[0]:.1%} | {values[1]:.1%} |")
    lines.extend(
        [
            f"| Hallucinated tables | {diagnostic.models[names[0]].hallucinated_table_count} | "
            f"{diagnostic.models[names[1]].hallucinated_table_count} |",
            f"| Hallucinated columns | {diagnostic.models[names[0]].hallucinated_column_count} | "
            f"{diagnostic.models[names[1]].hallucinated_column_count} |",
            f"| Join failures | {diagnostic.models[names[0]].join_failures} | "
            f"{diagnostic.models[names[1]].join_failures} |",
            f"| Aggregation failures | {diagnostic.models[names[0]].aggregation_failures} | "
            f"{diagnostic.models[names[1]].aggregation_failures} |",
            f"| Temporal failures | {diagnostic.models[names[0]].temporal_failures} | "
            f"{diagnostic.models[names[1]].temporal_failures} |",
            f"| Average model latency | "
            f"{diagnostic.models[names[0]].evaluation.performance.average_llm_latency_ms:.1f} ms | "
            f"{diagnostic.models[names[1]].evaluation.performance.average_llm_latency_ms:.1f} ms |",
            f"| Model calls | "
            f"{diagnostic.models[names[0]].evaluation.performance.llm_call_count} | "
            f"{diagnostic.models[names[1]].evaluation.performance.llm_call_count} |",
            f"| Total tokens | "
            f"{diagnostic.models[names[0]].evaluation.performance.total_tokens or 0} | "
            f"{diagnostic.models[names[1]].evaluation.performance.total_tokens or 0} |",
            f"| Infrastructure failures | "
            f"{diagnostic.models[names[0]].evaluation.infrastructure_failures} | "
            f"{diagnostic.models[names[1]].evaluation.infrastructure_failures} |",
            "",
            "## Per-Case Diagnostics",
            "",
            "| Case | Model | Passed | Hallucinated tables | Hallucinated columns | "
            "Failed metrics |",
            "|---|---|---:|---|---|---|",
        ]
    )
    for model_name in names:
        run = diagnostic.models[model_name]
        results = {result.case_id: result for result in run.evaluation.results}
        for item in run.diagnostics:
            result = results[item.case_id]
            lines.append(
                f"| `{item.case_id}` | `{model_name}` | {'yes' if item.passed else 'no'} | "
                f"{', '.join(item.hallucinated_tables) or 'none'} | "
                f"{', '.join(item.hallucinated_columns) or 'none'} | "
                f"{', '.join(result.failed_metrics) or 'none'} |"
            )
    lines.extend(
        [
            "",
            "## Conclusion",
            "",
            f"**{diagnostic.conclusion.classification}. {diagnostic.conclusion.statement}**",
            "",
            *[f"- {item}" for item in diagnostic.conclusion.evidence],
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
