from typing import Any, TypedDict

from app.data.gateway import TableMetadata


class AgentState(TypedDict, total=False):
    request_id: str
    trace_id: str
    thread_id: str | None
    question: str
    retrieved_metadata: list[TableMetadata]
    generated_sql: str
    needs_clarification: bool
    clarification_question: str
    validated_sql: str
    query_result: list[dict[str, Any]]
    final_answer: str
    chart_spec: dict[str, Any] | None
    provenance: dict[str, Any]
    errors: list[str]
    model_route: str
