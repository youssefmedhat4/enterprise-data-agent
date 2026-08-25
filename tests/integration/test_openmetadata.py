from __future__ import annotations

import httpx
import pytest

from app.data.gateway import ColumnMetadata, TableMetadata
from app.governance.gateway import GovernanceProviderUnavailableError
from app.governance.openmetadata import OpenMetadataGovernanceGateway


def _authorized_employees() -> TableMetadata:
    return TableMetadata(
        schema_name="analytics",
        table_name="employees",
        columns=["id", "full_name"],
        description="Employee physical schema",
        column_metadata=[
            ColumnMetadata("id", "integer", False),
            ColumnMetadata("full_name", "text", False),
        ],
    )


@pytest.mark.asyncio
async def test_openmetadata_current_rest_contract_is_mapped_and_filtered() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/v1/tables/name/service.database.analytics.employees"):
            return httpx.Response(
                200,
                json={
                    "id": "om-employees",
                    "fullyQualifiedName": "service.database.analytics.employees",
                    "description": "Governed employee directory",
                    "updatedAt": 1767225600000,
                    "owners": [
                        {
                            "id": "owner-1",
                            "name": "data-steward",
                            "displayName": "Data Steward",
                            "type": "user",
                        }
                    ],
                    "domains": [
                        {"name": "HR", "fullyQualifiedName": "Enterprise.HR"}
                    ],
                    "tags": [
                        {"tagFQN": "Tier.Tier1", "source": "Classification"},
                        {"tagFQN": "People.Employee", "source": "Glossary"},
                        {"tagFQN": "PII.Sensitive", "source": "Classification"},
                    ],
                    "columns": [
                        {"name": "id", "description": "Employee identifier"},
                        {
                            "name": "full_name",
                            "description": "Employee display name",
                            "tags": [
                                {"tagFQN": "People.Name", "source": "Glossary"}
                            ],
                        },
                        {
                            "name": "salary",
                            "description": "Restricted compensation metadata",
                            "tags": [
                                {"tagFQN": "PII.High", "source": "Classification"}
                            ],
                        },
                    ],
                },
            )
        if request.url.path.endswith("/v1/lineage/table/om-employees"):
            return httpx.Response(
                200,
                json={
                    "entity": {"id": "om-employees", "name": "employees"},
                    "nodes": [
                        {
                            "id": "om-secret",
                            "name": "secret_payroll",
                            "fullyQualifiedName": "service.database.analytics.secret_payroll",
                        }
                    ],
                    "upstreamEdges": [{"fromEntity": "om-secret"}],
                    "downstreamEdges": [],
                },
            )
        return httpx.Response(404, json={"message": "not found"})

    client = httpx.AsyncClient(
        base_url="http://openmetadata.test/api",
        transport=httpx.MockTransport(respond),
        headers={"Authorization": "Bearer test-token"},
    )
    gateway = OpenMetadataGovernanceGateway(
        api_url="http://openmetadata.test/api",
        fqn_prefix="service.database",
        timeout_seconds=1,
        client=client,
    )

    snapshot = await gateway.get_metadata([_authorized_employees()])
    table = snapshot.tables["analytics.employees"]

    assert len(requests) == 2
    assert requests[0].method == requests[1].method == "GET"
    assert requests[0].url.params["fields"] == "columns,owners,tags,domains,lifeCycle"
    assert table.source_id == "om-employees"
    assert table.owners[0].display_name == "Data Steward"
    assert table.domains == ("Enterprise.HR",)
    assert table.glossary_terms == ("People.Employee",)
    assert table.sensitivity == ("PII.Sensitive",)
    assert set(table.columns) == {"id", "full_name"}
    assert table.columns["full_name"].glossary_terms == ("People.Name",)
    assert table.lineage.upstream == ()
    assert table.freshness.catalog_updated_at is not None
    assert "salary" not in snapshot.model_dump_json()
    assert "secret_payroll" not in snapshot.model_dump_json()
    await client.aclose()


@pytest.mark.asyncio
async def test_openmetadata_unavailable_raises_typed_provider_error() -> None:
    def unavailable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("catalog unavailable", request=request)

    client = httpx.AsyncClient(
        base_url="http://openmetadata.test/api",
        transport=httpx.MockTransport(unavailable),
    )
    gateway = OpenMetadataGovernanceGateway(
        api_url="http://openmetadata.test/api",
        fqn_prefix="service.database",
        timeout_seconds=1,
        client=client,
    )

    with pytest.raises(GovernanceProviderUnavailableError):
        await gateway.get_metadata([_authorized_employees()])

    await client.aclose()
