from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from app.agent.context import AnalyticalContext
from app.agent.graph import build_graph
from app.authentication.gateway import UserIdentity
from app.authorization.gateway import (
    AuthorizationDeniedError,
    AuthorizationProviderUnavailableError,
)
from app.authorization.local import LocalPolicyAuthorizationGateway
from app.authorization.opa import OPAAuthorizationGateway
from app.data.fake import FakeDatabaseGateway
from app.data.gateway import TableMetadata
from app.governance.gateway import (
    GovernanceColumnMetadata,
    GovernanceSnapshot,
    GovernanceTableMetadata,
)
from app.llm.fake import FakeLLMGateway
from app.llm.gateway import LLMGateway, ResponseModelT, SQLGeneration
from app.metrics.fake import FakeMetricGateway
from app.security.sql_validation import SQLValidationResult, SQLValidator
from app.semantic.gateway import SemanticContext, SemanticGateway
from app.semantic.in_memory import InMemorySemanticGateway

POLICY_PATH = Path("infra/opa/data/local_roles.json")
VERTICAL_QUESTION = (
    "Show each department, its number of employees, total salary, average salary, "
    "and highest paid employee, ordered by total payroll."
)


def _identity(role: str) -> UserIdentity:
    return UserIdentity(subject_id=f"test-{role}", roles=(role,), provider="test")


class RecordingLLM(LLMGateway):
    def __init__(self) -> None:
        self.calls = 0
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
        self.calls += 1
        self.prompts.append(user)
        if response_model is not SQLGeneration:
            raise AssertionError("The authorization test uses SQL-only mode.")
        return response_model.model_validate(
            {
                "action": "execute",
                "sql": "SELECT e.full_name FROM analytics.employees e",
            }
        )


class NeverCalledLLM(LLMGateway):
    def __init__(self) -> None:
        self.calls = 0

    async def generate_structured(
        self,
        *,
        model_alias: str,
        system: str,
        user: str,
        response_model: type[ResponseModelT],
    ) -> ResponseModelT:
        del model_alias, system, user, response_model
        self.calls += 1
        raise AssertionError("Authorization should have stopped before the LLM.")


class RecordingSemanticGateway(SemanticGateway):
    def __init__(self) -> None:
        self.delegate = InMemorySemanticGateway()
        self.calls = 0
        self.available_tables: list[TableMetadata] = []

    async def retrieve_context(
        self,
        *,
        question: str,
        available_tables: list[TableMetadata],
        prior_context: AnalyticalContext | None,
    ) -> SemanticContext:
        self.calls += 1
        self.available_tables = available_tables
        return await self.delegate.retrieve_context(
            question=question,
            available_tables=available_tables,
            prior_context=prior_context,
        )


class OverBroadGovernanceGateway:
    def __init__(self) -> None:
        self.requested_tables: list[TableMetadata] = []

    async def get_metadata(self, tables: list[TableMetadata]) -> GovernanceSnapshot:
        self.requested_tables = tables
        employee = next(table for table in tables if table.table_name == "employees")
        return GovernanceSnapshot(
            provider="test_governance",
            tables={
                employee.identifier: GovernanceTableMetadata(
                    physical_identifier=employee.identifier,
                    source_id="om-employees",
                    source_fqn="service.database.analytics.employees",
                    columns={
                        "full_name": GovernanceColumnMetadata(
                            name="full_name",
                            description="Governed employee name",
                        ),
                        "salary": GovernanceColumnMetadata(
                            name="salary",
                            description="Restricted compensation metadata",
                            sensitivity=("PII.High",),
                        ),
                    },
                )
            },
        )

    async def close(self) -> None:
        return None


@dataclass(frozen=True)
class RecordingSQLValidator(SQLValidator):
    allowed_snapshots: list[list[TableMetadata]] = field(default_factory=list, compare=False)

    def validate(
        self,
        sql: str,
        *,
        allowed_schema: list[TableMetadata] | None = None,
        allowed_relations: frozenset[tuple[str, str]] | None = None,
    ) -> SQLValidationResult:
        self.allowed_snapshots.append(list(allowed_schema or []))
        return super().validate(
            sql,
            allowed_schema=allowed_schema,
            allowed_relations=allowed_relations,
        )


@pytest.mark.asyncio
async def test_authorized_request_succeeds_with_internal_auth_provenance() -> None:
    graph = build_graph(
        db_gateway=FakeDatabaseGateway(),
        llm_gateway=FakeLLMGateway(),
        sql_validator=SQLValidator(),
        authorization_gateway=LocalPolicyAuthorizationGateway(POLICY_PATH),
    )

    result = cast(
        dict[str, Any],
        await graph.ainvoke(
            {
                "request_id": "authorized",
                "trace_id": "authorized",
                "thread_id": None,
                "question": VERTICAL_QUESTION,
                "user_identity": _identity("hr_analyst"),
            }
        ),
    )

    provenance = result["internal_provenance"]
    assert result["execution_metadata"].status == "completed"
    assert provenance.authenticated_subject_id == "test-hr_analyst"
    assert provenance.authentication_provider == "test"
    assert provenance.authorization_provider == "local_policy"
    assert provenance.authorization_decision_id.startswith("local-")
    assert "annual_base_payroll" in provenance.authorized_scope.metrics


@pytest.mark.asyncio
async def test_unauthorized_salary_never_reaches_semantic_or_llm_context() -> None:
    semantic = RecordingSemanticGateway()
    llm = RecordingLLM()
    validator = RecordingSQLValidator()
    governance = OverBroadGovernanceGateway()
    graph = build_graph(
        db_gateway=FakeDatabaseGateway(),
        llm_gateway=llm,
        sql_validator=validator,
        semantic_gateway=semantic,
        authorization_gateway=LocalPolicyAuthorizationGateway(POLICY_PATH),
        governance_gateway=governance,
        generate_answer=False,
    )

    await graph.ainvoke(
        {
            "request_id": "restricted-column",
            "trace_id": "restricted-column",
            "thread_id": None,
            "question": "List employee names and compensation details",
            "user_identity": _identity("analyst"),
        }
    )

    semantic_employees = next(
        table for table in semantic.available_tables if table.table_name == "employees"
    )
    validated_employees = next(
        table
        for table in validator.allowed_snapshots[0]
        if table.table_name == "employees"
    )
    assert "salary" not in semantic_employees.columns
    requested_employees = next(
        table for table in governance.requested_tables if table.table_name == "employees"
    )
    assert "salary" not in requested_employees.columns
    assert "salary" not in validated_employees.columns
    assert "salary [" not in llm.prompts[0]
    assert "analytics.employees.salary" not in llm.prompts[0]
    assert "Restricted compensation metadata" not in llm.prompts[0]


@pytest.mark.asyncio
async def test_unauthorized_governed_metric_is_denied_before_cube() -> None:
    metrics = FakeMetricGateway()
    llm = NeverCalledLLM()
    graph = build_graph(
        db_gateway=FakeDatabaseGateway(),
        llm_gateway=llm,
        sql_validator=SQLValidator(),
        metric_gateway=metrics,
        enable_query_router=True,
        authorization_gateway=LocalPolicyAuthorizationGateway(POLICY_PATH),
    )

    with pytest.raises(AuthorizationDeniedError, match="authorized scope"):
        await graph.ainvoke(
            {
                "request_id": "restricted-metric",
                "trace_id": "restricted-metric",
                "thread_id": None,
                "question": "Total annual payroll by department",
                "user_identity": _identity("analyst"),
            }
        )

    assert metrics.queries == []
    assert llm.calls == 0


@pytest.mark.asyncio
async def test_opa_unavailable_fails_closed_before_semantic_or_llm() -> None:
    def unavailable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("OPA unavailable", request=request)

    client = httpx.AsyncClient(
        base_url="http://opa.test",
        transport=httpx.MockTransport(unavailable),
    )
    authorizer = OPAAuthorizationGateway(
        base_url="http://opa.test",
        decision_path="/v1/data/enterprise/analytics/decision",
        timeout_seconds=1,
        client=client,
    )
    semantic = RecordingSemanticGateway()
    llm = NeverCalledLLM()
    database = FakeDatabaseGateway()
    graph = build_graph(
        db_gateway=database,
        llm_gateway=llm,
        sql_validator=SQLValidator(),
        semantic_gateway=semantic,
        authorization_gateway=authorizer,
    )

    with pytest.raises(AuthorizationProviderUnavailableError):
        await graph.ainvoke(
            {
                "request_id": "opa-down",
                "trace_id": "opa-down",
                "thread_id": None,
                "question": "List employee names",
                "user_identity": _identity("admin_analytics"),
            }
        )

    assert semantic.calls == 0
    assert llm.calls == 0
    assert database.executed_sql == []
    await client.aclose()


def test_production_configuration_requires_opa() -> None:
    from app.config import Settings

    with pytest.raises(ValueError, match="AUTHORIZATION_PROVIDER=opa"):
        Settings(APP_ENV="production", AUTHORIZATION_PROVIDER="local")
