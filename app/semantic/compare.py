import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.config import Settings
from app.data.factory import build_database_gateway
from app.semantic.gateway import SemanticContext, SemanticProviderUnavailableError
from app.semantic.in_memory import InMemorySemanticGateway
from app.semantic.wren import MCPWrenContextClient, WrenSemanticGateway

DEFAULT_QUESTION = "Which department has the highest payroll?"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare bounded semantic context providers.")
    parser.add_argument("question", nargs="?")
    parser.add_argument("--questions", action="append", default=[])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    questions = args.questions or [args.question or DEFAULT_QUESTION]
    return asyncio.run(_run_many(questions, args.output))


async def _run(question: str) -> int:
    return await _run_many([question], None)


async def _run_many(questions: list[str], output_path: Path | None) -> int:
    settings = Settings()
    database = build_database_gateway(settings)
    try:
        wren_gateway = WrenSemanticGateway(
            MCPWrenContextClient(
                settings.wren_mcp_url,
                timeout_seconds=settings.wren_timeout_seconds,
            ),
            max_models=settings.wren_max_context_models,
            project_id=settings.wren_project_id,
        )
        comparisons: list[dict[str, Any]] = []
        for question in questions:
            tables = await database.search_schema(question)
            inmemory = await InMemorySemanticGateway().retrieve_context(
                question=question,
                available_tables=tables,
                prior_context=None,
            )
            wren = await wren_gateway.retrieve_context(
                question=question,
                available_tables=tables,
                prior_context=None,
            )
            comparisons.append(
                {"question": question, "inmemory": _summary(inmemory), "wren": _summary(wren)}
            )
    except SemanticProviderUnavailableError:
        print("Wren semantic service is unavailable; no fallback was used.", file=sys.stderr)
        return 2
    finally:
        await database.close()
    payload: dict[str, Any] | list[dict[str, Any]] = (
        comparisons[0] if len(comparisons) == 1 else comparisons
    )
    serialized = json.dumps(payload, indent=2, ensure_ascii=False)
    print(serialized)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(serialized + "\n", encoding="utf-8")
    return 0


def _summary(context: SemanticContext) -> dict[str, Any]:
    return {
        "provider": context.provider,
        "tables": context.table_ids,
        "models": context.model_ids,
        "relationships": context.relationship_ids,
        "definitions": context.definition_ids,
        "measures": context.measure_ids,
        "context_size_chars": context.context_size_chars,
        "retrieval_latency_ms": context.retrieval_latency_ms,
    }


if __name__ == "__main__":
    raise SystemExit(main())
