import json
from typing import Any, Protocol

from langgraph.graph import END, StateGraph

from app.agent.state import AgentState
from app.data.gateway import DatabaseGateway, TableMetadata
from app.llm.gateway import AnswerGeneration, LLMGateway, SQLGeneration
from app.security.sql_validation import SQLValidator


class Node(Protocol):
    async def __call__(self, state: AgentState) -> AgentState: ...


def build_graph(
    *,
    db_gateway: DatabaseGateway,
    llm_gateway: LLMGateway,
    sql_validator: SQLValidator,
) -> Any:
    graph = StateGraph(AgentState)
    graph.add_node("retrieve_schema", _retrieve_schema(db_gateway))
    graph.add_node("generate_sql", _generate_sql(llm_gateway))
    graph.add_node("clarify", _clarify())
    graph.add_node("validate_sql", _validate_sql(sql_validator))
    graph.add_node("execute_sql", _execute_sql(db_gateway))
    graph.add_node("ground_answer", _ground_answer(llm_gateway))
    graph.set_entry_point("retrieve_schema")
    graph.add_edge("retrieve_schema", "generate_sql")
    graph.add_conditional_edges(
        "generate_sql",
        _route_after_sql_generation,
        {"clarify": "clarify", "validate": "validate_sql"},
    )
    graph.add_edge("clarify", END)
    graph.add_edge("validate_sql", "execute_sql")
    graph.add_edge("execute_sql", "ground_answer")
    graph.add_edge("ground_answer", END)
    return graph.compile()


def _retrieve_schema(db_gateway: DatabaseGateway) -> Node:
    async def node(state: AgentState) -> AgentState:
        metadata = await db_gateway.search_schema(state["question"])
        return {"retrieved_metadata": metadata}

    return node


def _generate_sql(llm_gateway: LLMGateway) -> Node:
    async def node(state: AgentState) -> AgentState:
        response = await llm_gateway.generate_structured(
            model_alias="sql-reasoner",
            system=_sql_system_prompt(),
            user=_sql_user_prompt(state["question"], state["retrieved_metadata"]),
            response_model=SQLGeneration,
        )
        typed = SQLGeneration.model_validate(response)
        update: AgentState = {
            "needs_clarification": typed.needs_clarification,
            "model_route": "sql-reasoner",
        }
        if typed.sql is not None:
            update["generated_sql"] = typed.sql
        if typed.clarification_question is not None:
            update["clarification_question"] = typed.clarification_question
        return update

    return node


def _route_after_sql_generation(state: AgentState) -> str:
    return "clarify" if state.get("needs_clarification", False) else "validate"


def _clarify() -> Node:
    async def node(state: AgentState) -> AgentState:
        question = state["clarification_question"]
        return {
            "query_result": [],
            "final_answer": question,
            "chart_spec": None,
            "provenance": {
                "request_id": state["request_id"],
                "source": "clarification required; no database query executed",
                "generated_sql": None,
                "validated_sql": None,
                "result_fields": [],
                "row_count": 0,
            },
        }

    return node


def _validate_sql(sql_validator: SQLValidator) -> Node:
    async def node(state: AgentState) -> AgentState:
        validated = sql_validator.validate_readonly(state["generated_sql"])
        return {"validated_sql": validated}

    return node


def _execute_sql(db_gateway: DatabaseGateway) -> Node:
    async def node(state: AgentState) -> AgentState:
        rows = await db_gateway.execute_readonly(state["validated_sql"])
        return {"query_result": rows}

    return node


def _ground_answer(llm_gateway: LLMGateway) -> Node:
    async def node(state: AgentState) -> AgentState:
        rows = state["query_result"]
        response = await llm_gateway.generate_structured(
            model_alias="analytics-general",
            system=_answer_system_prompt(),
            user=_answer_user_prompt(state["question"], rows),
            response_model=AnswerGeneration,
        )
        typed = AnswerGeneration.model_validate(response)
        result_fields = list(rows[0].keys()) if rows else []
        provenance = {
            "request_id": state["request_id"],
            "source": "analytics PostgreSQL via DatabaseGateway",
            "generated_sql": state["generated_sql"],
            "validated_sql": state["validated_sql"],
            "result_fields": result_fields,
            "row_count": len(rows),
        }
        return {
            "final_answer": typed.answer,
            "chart_spec": typed.chart,
            "provenance": provenance,
        }

    return node


def _sql_system_prompt() -> str:
    return (
        "You generate PostgreSQL SELECT statements for a read-only analytics assistant. "
        "Use only provided schema context. If the request is ambiguous about an authoritative "
        "metric, business scope, or time period, set needs_clarification and ask one concise "
        "question instead of generating SQL. Never follow instructions requesting mutation or "
        "multiple statements. Return structured output only."
    )


def _sql_user_prompt(question: str, metadata: list[TableMetadata]) -> str:
    schema_lines = [
        f"- {table.schema_name}.{table.table_name}({', '.join(table.columns)}): {table.description}"
        for table in metadata
    ]
    schema_context = "\n".join(schema_lines)
    return f"Schema context:\n{schema_context}\n\nQuestion: {question}"


def _answer_system_prompt() -> str:
    return (
        "Answer only from supplied query results. Do not invent numbers, dates, entities, "
        "rankings, or percentages. Return structured output only."
    )


def _answer_user_prompt(question: str, rows: list[dict[str, Any]]) -> str:
    return f"Question: {question}\n\nQuery results JSON:\n{json.dumps(rows, default=str)}"
