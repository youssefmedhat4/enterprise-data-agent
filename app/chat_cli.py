import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from typing import Any, cast
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver

from app.agent.graph import build_graph
from app.agent.state import AgentState
from app.config import Settings
from app.data.factory import build_database_gateway
from app.errors import normalize_error
from app.llm.factory import build_llm_gateway
from app.metrics.factory import build_metric_gateway
from app.security.sql_validation import SQLValidator
from app.semantic.factory import build_semantic_gateway


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Interactive read-only analytics against configured PostgreSQL."
    )
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--thread-id", default=None)
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_run(debug=args.debug, thread_id=args.thread_id))
    except KeyboardInterrupt:
        return 130


async def _run(*, debug: bool, thread_id: str | None) -> int:
    try:
        settings = Settings()
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    if settings.database_provider not in {"postgres", "toolbox"}:
        print(
            "Configuration error: enterprise-data-chat requires "
            "DATABASE_PROVIDER=postgres or toolbox.",
            file=sys.stderr,
        )
        return 2
    if settings.llm_provider != "litellm":
        print(
            "Configuration error: arbitrary interactive questions require LLM_PROVIDER=litellm.",
            file=sys.stderr,
        )
        return 2

    database = build_database_gateway(settings)
    llm = build_llm_gateway(settings)
    semantics = build_semantic_gateway(settings)
    metrics = build_metric_gateway(settings, database=database)
    validator = SQLValidator(
        allowed_schemas=frozenset(settings.database_allowed_schemas),
        max_rows=settings.query_row_limit,
    )
    graph = build_graph(
        db_gateway=database,
        llm_gateway=llm,
        sql_validator=validator,
        checkpointer=InMemorySaver(),
        semantic_gateway=semantics,
        sql_generation_provider=settings.sql_generation_provider,
        metric_gateway=metrics,
        enable_query_router=True,
    )
    active_thread = thread_id or str(uuid4())
    config = {"configurable": {"thread_id": active_thread}}
    try:
        await database.health_check()
        print(f"Connected to {database.source().identifier} (read-only verified).")
        print(f"Thread: {active_thread}. Enter /quit to exit.")
        while True:
            try:
                question = (await asyncio.to_thread(input, "\nQuestion: ")).strip()
            except EOFError:
                return 0
            if not question:
                continue
            if question.casefold() in {"/quit", "/exit", "quit", "exit"}:
                return 0
            request_id = str(uuid4())
            try:
                raw = await graph.ainvoke(
                    {
                        "request_id": request_id,
                        "trace_id": request_id,
                        "thread_id": active_thread,
                        "question": question,
                    },
                    config=config,
                )
                result = cast(AgentState, raw)
                _print_result(result, debug=debug)
            except Exception as exc:
                error = normalize_error(exc, request_id=request_id)
                print(f"Error [{error.code}]: {error.safe_message}", file=sys.stderr)
    finally:
        await metrics.close()
        await database.close()


def _print_result(result: AgentState, *, debug: bool) -> None:
    print(f"\nAnswer: {result['final_answer']}")
    rows = result.get("query_result", [])
    if rows:
        print("\n" + _render_table(rows))
    execution = result["execution_metadata"]
    print(
        f"\nExecution: {execution.duration_ms:.3f} ms; "
        f"rows={execution.row_count}; live={str(execution.live).lower()}"
    )
    for warning in result.get("warnings", []):
        print(f"Warning: {warning}")
    if not debug:
        return
    print(f"SQL: {result.get('validated_sql') or 'none'}")
    print("Selected schema: " + ", ".join(result.get("selected_schema_ids", [])))
    print(
        "Semantic definitions: " + (", ".join(result.get("semantic_definition_ids", [])) or "none")
    )
    print(f"Semantic provider: {result.get('semantic_provider', 'inmemory')}")
    print("Semantic models: " + (", ".join(result.get("semantic_model_ids", [])) or "none"))
    print(
        "Semantic relationships: "
        + (", ".join(result.get("semantic_relationship_ids", [])) or "none")
    )
    print("Semantic measures: " + (", ".join(result.get("semantic_measure_ids", [])) or "none"))
    provenance = result["internal_provenance"]
    print("Source tables: " + (", ".join(provenance.tables) or "none"))


def _render_table(rows: list[dict[str, Any]]) -> str:
    columns = list(rows[0])
    values = [
        [json.dumps(row.get(column), ensure_ascii=False, default=str) for column in columns]
        for row in rows
    ]
    widths = [
        min(40, max(len(column), *(len(row[index]) for row in values)))
        for index, column in enumerate(columns)
    ]

    def render_row(row: list[str]) -> str:
        return " | ".join(
            value[:width].ljust(width) for value, width in zip(row, widths, strict=True)
        )

    separator = "-+-".join("-" * width for width in widths)
    return "\n".join((render_row(columns), separator, *(render_row(row) for row in values)))


if __name__ == "__main__":
    raise SystemExit(main())
