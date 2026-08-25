import argparse
import asyncio
import hashlib
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, cast

from app.config import Settings
from app.data.postgres import PostgresDatabaseGateway
from app.evals.deterministic_llm import DeterministicEvaluationLLM
from app.evals.duckdb_gateway import DuckDBEvaluationGateway
from app.evals.loader import load_evaluation_cases
from app.evals.models import EvaluationMode, EvaluationResult, EvaluationSummary
from app.evals.report import (
    render_cerebras_comparison,
    render_cloud_comparison,
    render_groq_qwen_comparison,
    render_local_sql_baseline,
)
from app.evals.runner import BackendName, build_fake_database_factory, run_evaluations
from app.llm.factory import build_llm_gateway
from app.llm.gateway import LLMGateway
from app.security.sql_validation import SQLValidator
from app.semantic.factory import build_semantic_gateway


class EvaluationConfigurationError(RuntimeError):
    """Raised before evaluation when an explicitly selected backend is not configured."""


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run synthetic analytics evaluations.")
    parser.add_argument("--cases", type=Path, default=Path("evals/cases.json"))
    parser.add_argument("--backend", choices=("fake", "duckdb", "postgres"), default="fake")
    parser.add_argument("--llm", choices=("deterministic", "configured"), default="deterministic")
    parser.add_argument("--mode", choices=("full", "sql"), default="full")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--comparison-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    parser.add_argument("--request-delay-seconds", type=float)
    parser.add_argument("--reference-report", type=Path)
    parser.add_argument("--secondary-reference-report", type=Path)
    parser.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="Run only this case ID; repeat to select multiple cases.",
    )
    args = parser.parse_args(argv)
    try:
        return asyncio.run(
            _run(
                args.cases,
                cast(BackendName, args.backend),
                cast(Literal["deterministic", "configured"], args.llm),
                cast(EvaluationMode, args.mode),
                args.output,
                args.comparison_output,
                args.markdown_output,
                args.request_delay_seconds,
                args.reference_report,
                args.secondary_reference_report,
                args.case_id,
            )
        )
    except EvaluationConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2


async def _run(
    cases_path: Path,
    backend: BackendName,
    llm_backend: Literal["deterministic", "configured"],
    evaluation_mode: EvaluationMode,
    output_path: Path | None,
    comparison_output_path: Path | None,
    markdown_output_path: Path | None,
    request_delay_seconds: float | None,
    reference_report_path: Path | None,
    secondary_reference_report_path: Path | None,
    case_ids: list[str],
) -> int:
    settings = Settings()
    cases = load_evaluation_cases(cases_path)
    if case_ids:
        requested = set(case_ids)
        available = {case.id for case in cases}
        missing = sorted(requested - available)
        if missing:
            raise EvaluationConfigurationError(
                "Unknown evaluation case IDs: " + ", ".join(missing) + "."
            )
        cases = [case for case in cases if case.id in requested]
    dataset_sha256 = hashlib.sha256(cases_path.read_bytes()).hexdigest()
    sql_validator = SQLValidator(max_rows=settings.query_row_limit)
    effective_delay = (
        settings.evaluation_request_delay_seconds
        if request_delay_seconds is None
        else request_delay_seconds
    )
    if effective_delay < 0:
        raise EvaluationConfigurationError("Request delay must be non-negative.")

    if backend == "fake":
        database_factory = build_fake_database_factory(cases, sql_validator)
    elif backend == "duckdb":
        database_factory = DuckDBEvaluationGateway
    else:
        postgres_settings = Settings(DATABASE_PROVIDER="postgres")

        def database_factory() -> PostgresDatabaseGateway:
            return PostgresDatabaseGateway(postgres_settings)

    llm_gateway: LLMGateway
    if llm_backend == "deterministic":
        llm_gateway = DeterministicEvaluationLLM(cases)
        retry_count = 0
    else:
        _validate_configured_configuration(settings)
        llm_gateway = build_llm_gateway(settings)
        retry_count = None

    summary = await run_evaluations(
        cases,
        backend=backend,
        database_factory=database_factory,
        llm_gateway=llm_gateway,
        sql_validator=sql_validator,
        retry_count=retry_count,
        llm_backend=llm_backend,
        dataset_sha256=dataset_sha256,
        configured_models=settings.model_aliases if llm_backend == "configured" else {},
        evaluation_mode=evaluation_mode,
        request_delay_seconds=effective_delay,
        progress_callback=_print_progress,
        semantic_gateway=build_semantic_gateway(settings),
        semantic_provider=settings.semantic_provider,
        sql_generation_provider=settings.sql_generation_provider,
    )
    report = summary.model_dump_json(indent=2)
    if hasattr(sys.stdout, "reconfigure"):
        cast(Any, sys.stdout).reconfigure(encoding="utf-8")
    print(report)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report + "\n", encoding="utf-8")
    if comparison_output_path is not None:
        if llm_backend != "configured":
            raise EvaluationConfigurationError("--comparison-output requires --llm configured.")
        deterministic = await run_evaluations(
            cases,
            backend=backend,
            database_factory=database_factory,
            llm_gateway=DeterministicEvaluationLLM(cases),
            sql_validator=sql_validator,
            retry_count=0,
            llm_backend="deterministic",
            dataset_sha256=dataset_sha256,
            evaluation_mode=evaluation_mode,
            semantic_gateway=build_semantic_gateway(settings),
            semantic_provider=settings.semantic_provider,
            sql_generation_provider=settings.sql_generation_provider,
        )
        comparison_output_path.parent.mkdir(parents=True, exist_ok=True)
        comparison_output_path.write_text(
            render_cloud_comparison(deterministic, summary),
            encoding="utf-8",
        )
    if markdown_output_path is not None:
        markdown_output_path.parent.mkdir(parents=True, exist_ok=True)
        markdown = render_local_sql_baseline(summary)
        if reference_report_path is not None:
            reference = EvaluationSummary.model_validate_json(
                reference_report_path.read_text(encoding="utf-8")
            )
            if reference.dataset_sha256 != summary.dataset_sha256:
                raise EvaluationConfigurationError(
                    "Reference and candidate reports use different evaluation datasets."
                )
            schema_database = DuckDBEvaluationGateway()
            try:
                schema = await schema_database.search_schema("")
            finally:
                await schema_database.close()
            if "cerebras" in summary.performance.provider_calls or any(
                model.startswith("cerebras/") for model in summary.configured_models.values()
            ):
                secondary = None
                if secondary_reference_report_path is not None:
                    secondary = EvaluationSummary.model_validate_json(
                        secondary_reference_report_path.read_text(encoding="utf-8")
                    )
                    if secondary.dataset_sha256 != summary.dataset_sha256:
                        raise EvaluationConfigurationError(
                            "Secondary reference and candidate reports use different "
                            "evaluation datasets."
                        )
                markdown = render_cerebras_comparison(
                    reference,
                    secondary,
                    summary,
                    schema=schema,
                )
            else:
                markdown = render_groq_qwen_comparison(reference, summary, schema=schema)
        markdown_output_path.write_text(
            markdown,
            encoding="utf-8",
        )
    return 0 if summary.failed_cases == 0 else 1


def _print_progress(index: int, total: int, result: EvaluationResult) -> None:
    if result.passed:
        status = "passed"
    elif result.infrastructure_error is not None:
        status = f"infrastructure_error:{result.infrastructure_error}"
    else:
        status = "model_failure" if result.failure_type == "model" else "failed"
    print(f"[{index}/{total}] {result.case_id} - {status}", file=sys.stderr)


def _validate_configured_configuration(settings: Settings) -> None:
    if settings.llm_provider != "litellm":
        raise EvaluationConfigurationError(
            "configured LLM evaluation requires LLM_PROVIDER=litellm."
        )
    fake_aliases = [
        alias for alias, model in settings.model_aliases.items() if model.startswith("fake/")
    ]
    if fake_aliases:
        raise EvaluationConfigurationError(
            "configured LLM evaluation requires physical model IDs for: "
            + ", ".join(sorted(fake_aliases))
            + "."
        )
    supported_providers = {
        "cerebras",
        "gemini",
        "groq",
        "ollama",
        "openai",
        "vertex_ai",
        "zai",
    }
    unsupported_aliases = [
        alias
        for alias, model in settings.model_aliases.items()
        if settings.model_provider(model) not in supported_providers
    ]
    if unsupported_aliases:
        raise EvaluationConfigurationError(
            "No configured LiteLLM provider mapping is available for: "
            + ", ".join(sorted(unsupported_aliases))
            + "."
        )
    missing_keys = sorted(
        {
            key_name
            for alias, model in settings.model_aliases.items()
            if alias not in settings.api_keys_by_alias
            if (key_name := settings.required_api_key_name(model)) is not None
        }
    )
    if missing_keys:
        raise EvaluationConfigurationError(
            "Configured LiteLLM evaluation requires: " + ", ".join(missing_keys) + "."
        )
    if (
        any(
            settings.model_provider(model) == "vertex_ai"
            for model in settings.model_aliases.values()
        )
        and not settings.vertex_ai_project
    ):
        raise EvaluationConfigurationError(
            "Configured Vertex AI evaluation requires VERTEXAI_PROJECT."
        )
    providers = {settings.model_provider(model) for model in settings.model_aliases.values()}
    cloud_providers = providers & {
        "cerebras",
        "gemini",
        "groq",
        "openai",
        "vertex_ai",
        "zai",
    }
    if cloud_providers and not settings.run_cloud_llm_tests:
        raise EvaluationConfigurationError(
            "Set RUN_CLOUD_LLM_TESTS=1 to explicitly allow live cloud calls."
        )
    if "ollama" in providers and not settings.run_local_llm_tests:
        raise EvaluationConfigurationError(
            "Set RUN_LOCAL_LLM_TESTS=1 to explicitly allow local Ollama calls."
        )


def _validate_cloud_configuration(settings: Settings) -> None:
    """Backward-compatible validation entry point for cloud evaluation tests."""
    _validate_configured_configuration(settings)


if __name__ == "__main__":
    raise SystemExit(main())
