"""Run one request with an approved example in scope, and watch what executes.

Built as a support helper because the interesting assertion is not about any
one node: it is about the whole path from stored knowledge to the database.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from app.agent.state import AgentState
from app.authorization.gateway import (
    AuthorizationDecision,
    AuthorizationGateway,
    AuthorizationRequest,
)
from app.data.gateway import (
    ColumnMetadata,
    DatabaseExecutionMetadata,
    DatabaseGateway,
    DatabaseQueryResult,
    DatabaseSource,
    ResultColumnMetadata,
    TableMetadata,
)
from app.knowledge.guidance import ApprovedQueryExample, InMemoryGuidanceStore
from app.llm.gateway import LLMGateway, ResponseModelT, SQLGeneration
from app.security.sql_validation import SQLValidator

SOURCE = UUID("11111111-1111-1111-1111-111111111111")


class RecordingGateway(DatabaseGateway):
    """A database that remembers every statement it was asked to run."""

    def __init__(self) -> None:
        self.executed: list[str] = []

    def source(self) -> DatabaseSource:
        return DatabaseSource(identifier="postgres:test", dialect="postgres")

    async def health_check(self) -> bool:
        return True

    async def search_schema(self, question: str) -> list[TableMetadata]:
        del question
        return [
            TableMetadata(
                schema_name="erp",
                table_name="emp_comp_hist",
                columns=["emp_no", "ann_sal_amt", "curr_flg", "eff_dt_chr"],
                description="compensation history",
                column_metadata=[
                    ColumnMetadata(name="emp_no", data_type="integer", nullable=False),
                    ColumnMetadata(
                        name="ann_sal_amt", data_type="numeric", nullable=False
                    ),
                    ColumnMetadata(name="curr_flg", data_type="char", nullable=False),
                    ColumnMetadata(
                        name="eff_dt_chr", data_type="char", nullable=False
                    ),
                ],
            )
        ]

    async def execute_readonly(
        self, sql: str, parameters: Sequence[Any] = ()
    ) -> DatabaseQueryResult:
        del parameters
        self.executed.append(sql)
        return DatabaseQueryResult(
            rows=[{"emp_no": 1001}],
            columns=[ResultColumnMetadata(name="emp_no", data_type="integer")],
            metadata=DatabaseExecutionMetadata(
                duration_ms=1,
                executed_at=datetime.now(UTC),
                row_count=1,
                result_bytes=16,
                truncated=False,
                live=True,
            ),
        )

    async def close(self) -> None:
        return None


class _ScriptedSQL(LLMGateway):
    """Writes its own SQL, and records the prompt it was given."""

    def __init__(self, sql: str) -> None:
        self._sql = sql
        self.prompts: list[str] = []

    async def generate_structured(
        self,
        *,
        model_alias: str,
        system: str,
        user: str,
        response_model: type[ResponseModelT],
    ) -> ResponseModelT:
        del model_alias, system
        self.prompts.append(user)
        if response_model is SQLGeneration:
            return response_model.model_validate(
                {
                    "action": "execute",
                    "sql": self._sql,
                    "analysis": {"intent": "compensation"},
                }
            )
        raise AssertionError(f"unexpected response model {response_model.__name__}")


class _AllowAll(AuthorizationGateway):
    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        return AuthorizationDecision(
            allowed=True,
            provider="test",
            table_columns={table.identifier: table.columns for table in request.tables},
            allowed_schemas=tuple(
                sorted({table.schema_name for table in request.tables})
            ),
            allowed_metrics=request.metrics,
        )

    async def close(self) -> None:
        return None


async def run_with_approved_example(
    *, question: str, approved_sql: str, fresh_sql: str
) -> tuple[list[str], str]:
    """Answer `question` with `approved_sql` already approved for this source.

    Returns the statements the database was actually asked to run, and the
    prompt the model received.
    """
    from app.agent.graph import build_graph

    guidance = InMemoryGuidanceStore()
    await guidance.approve_example(
        ApprovedQueryExample(
            data_source_id=SOURCE,
            question=question,
            query_pattern=approved_sql,
            semantic_plan="Compare each employee's current row to the prior one.",
        ),
        was_successful=True,
        was_validated=True,
    )

    database = RecordingGateway()
    llm = _ScriptedSQL(fresh_sql)
    graph = build_graph(
        db_gateway=database,
        llm_gateway=llm,
        sql_validator=SQLValidator(
            max_rows=1000, allowed_schemas=frozenset({"erp"})
        ),
        generate_answer=False,
        guidance_store=guidance,
        data_source_id=SOURCE,
        authorization_gateway=_AllowAll(),
    )
    state: AgentState = {
        "question": question,
        "request_id": str(uuid4()),
        "trace_id": str(uuid4()),
        "thread_id": f"{SOURCE}:{uuid4()}",
    }
    await graph.ainvoke(state)
    sql_prompt = next(
        (prompt for prompt in llm.prompts if "Schema context" in prompt), ""
    )
    return database.executed, sql_prompt
