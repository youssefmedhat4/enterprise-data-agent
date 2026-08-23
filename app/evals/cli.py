import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, cast

from app.config import Settings
from app.data.postgres import PostgresDatabaseGateway
from app.evals.deterministic_llm import DeterministicEvaluationLLM
from app.evals.duckdb_gateway import DuckDBEvaluationGateway
from app.evals.loader import load_evaluation_cases
from app.evals.runner import BackendName, build_fake_database_factory, run_evaluations
from app.llm.factory import build_llm_gateway
from app.llm.gateway import LLMGateway
from app.security.sql_validation import SQLValidator


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run synthetic analytics evaluations.")
    parser.add_argument("--cases", type=Path, default=Path("evals/cases.json"))
    parser.add_argument("--backend", choices=("fake", "duckdb", "postgres"), default="fake")
    parser.add_argument("--llm", choices=("deterministic", "configured"), default="deterministic")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    return asyncio.run(
        _run(
            args.cases,
            cast(BackendName, args.backend),
            cast(Literal["deterministic", "configured"], args.llm),
            args.output,
        )
    )


async def _run(
    cases_path: Path,
    backend: BackendName,
    llm_backend: Literal["deterministic", "configured"],
    output_path: Path | None,
) -> int:
    settings = Settings()
    cases = load_evaluation_cases(cases_path)
    sql_validator = SQLValidator(max_rows=settings.query_row_limit)

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
        if settings.llm_provider != "litellm":
            raise RuntimeError("Configured LLM evaluation requires LLM_PROVIDER=litellm.")
        llm_gateway = build_llm_gateway(settings)
        retry_count = None

    summary = await run_evaluations(
        cases,
        backend=backend,
        database_factory=database_factory,
        llm_gateway=llm_gateway,
        sql_validator=sql_validator,
        retry_count=retry_count,
    )
    report = summary.model_dump_json(indent=2)
    if hasattr(sys.stdout, "reconfigure"):
        cast(Any, sys.stdout).reconfigure(encoding="utf-8")
    print(report)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report + "\n", encoding="utf-8")
    return 0 if summary.failed_cases == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
