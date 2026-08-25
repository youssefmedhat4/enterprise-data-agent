# ADR 0007: OpenMetadata Governance Ownership Boundary

Date: 2026-08-24

## Status

Accepted by implementation, pending architecture review.

## Context

Physical schema, business semantics, governed metrics, access policy, and catalog governance are
different sources of truth. Treating one system as the owner of all five would duplicate
definitions and blur enforcement boundaries. OpenMetadata is useful for descriptive and
governance context, but it must not become a query executor or an authorization bypass.

The implementation was checked against the current OpenMetadata 1.12 REST documentation. Table
metadata is retrieved by fully qualified name through `GET /v1/tables/name/{fqn}` with explicit
fields. Optional lineage uses `GET /v1/lineage/table/{id}` with bounded depth. Authentication uses
the deployment's bot/JWT token through an HTTP bearer header. The full self-hosted OpenMetadata
stack is intentionally not bundled or started for this adapter.

## Decision

Introduce a typed, read-only `GovernanceGateway` with two adapters:

- `DisabledGovernanceGateway` returns an empty snapshot and preserves existing behavior.
- `OpenMetadataGovernanceGateway` reads current v1 table and lineage REST resources.

Responsibility ownership is fixed as follows:

| Concern | Source of truth |
| --- | --- |
| Physical schemas, columns, types, and relationships | PostgreSQL catalog via `DatabaseGateway` |
| Business definitions and Text-to-SQL context | `SemanticGateway` (Wren or in-memory) |
| Official metric formulas and execution | Cube via `MetricGateway` |
| User access decisions | OPA via `AuthorizationGateway` |
| Owners, domains, glossary, classifications, sensitivity, lineage, freshness | OpenMetadata |

OpenMetadata metadata is requested only for the schema snapshot already filtered by OPA. The
gateway response is filtered a second time at the application boundary, including its columns and
lineage nodes, before descriptions enrich the physical snapshot. Catalog metadata cannot add a
table or column, change a physical type, key, or relationship, or broaden the validator allowlist.
The enriched authorized snapshot is then supplied to semantic retrieval and prompt construction;
the same authorized physical scope remains the SQLGlot validation boundary.

Metric definitions are not copied into OpenMetadata. OpenMetadata does not execute SQL and is not
called on the governed Cube execution path merely to duplicate metric ownership.

## Failure And Provenance

When governance is disabled, requests behave as before. When explicitly enabled for ad-hoc
context, connection errors, HTTP failures, missing required table entities, and malformed payloads
raise typed governance-provider errors. There is no fake-data or disabled-provider fallback.

Internal provenance records the governance provider, source entity IDs, owner display names,
catalog update time, and retrieval latency. These fields stay out of the public provenance view.
Catalog `updatedAt` is recorded as catalog freshness and is not represented as data freshness.

## Consequences

- Catalog enrichment cannot bypass OPA or expand SQL permissions.
- OpenMetadata remains independently deployable and replaceable behind one small interface.
- Direct REST avoids a large, version-coupled Python SDK dependency for this narrow integration.
- A deployment must align `OPENMETADATA_FQN_PREFIX` with its ingested service and database names.
- Data freshness remains empty until a reliable ingested freshness signal is available.

## References

- https://docs.open-metadata.org/v1.12.x/api-reference/data-assets/tables/retrieve
- https://docs.open-metadata.org/v1.12.x/api-reference/lineage/get
- https://docs.open-metadata.org/v1.12.x/api-reference/authentication
- https://docs.open-metadata.org/v1.12.x/quick-start/local-docker-deployment
