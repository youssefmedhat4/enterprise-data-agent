# ADR 0008: Conditional MCP Toolbox Database Adapter

Date: 2026-08-24

## Status

Accepted as an optional connectivity adapter with mandatory external safety controls.

## Context

The application needs replaceable connectivity for PostgreSQL today and potentially SQL Server,
MySQL, and other analytical databases later. MCP Toolbox for Databases now exposes a stable MCP
endpoint and prebuilt schema and SQL tools for multiple engines. The v1 migration disables its
legacy native `/api` by default in favor of the standard `/mcp` endpoint.

For PostgreSQL, current Toolbox 1.9 documentation provides `postgres-list-tables`, which returns
detailed table, column, constraint, owner, and comment JSON, and `postgres-execute-sql`, which
accepts one SQL string. The execute tool is explicitly described as intended for human-in-the-loop
developer workflows rather than production agents. It performs connectivity and execution, not
application authorization or read-only policy enforcement.

## Decision

Adopt Toolbox conditionally behind the existing `DatabaseGateway`:

```text
LangGraph -> DatabaseGateway -> ToolboxDatabaseGateway
                              -> MCPToolboxTransport -> Toolbox /mcp -> database
```

LangGraph, `SemanticGateway`, OPA authorization, QueryRouter, Cube, and SQL generation contain no
MCP calls. Direct `PostgresDatabaseGateway` remains available and unchanged in responsibility.
Provider selection is explicit; a configured Toolbox outage raises typed database errors and
never falls back to PostgreSQL, Fake, or another source.

The PostgreSQL Toolbox adapter:

- invokes only configured schema and execution tool names;
- maps current detailed `list_tables` JSON to typed physical `TableMetadata`;
- filters discovery to configured schemas before OPA receives the inventory;
- verifies `default_transaction_read_only`, role capability, schema creation rights, and table
  mutation privileges through a fixed internal query before accepting the source;
- revalidates every user query with SQLGlot against discovered relations before tool invocation;
- rejects bound parameters because the generic execute tool exposes only a SQL-string parameter;
- enforces application timeout, row-limit SQL, and bounded result serialization;
- records `mcp_toolbox`, source identifier, and dialect in internal provenance.

The physical Toolbox database user must still be read-only. The generic execute tool is not a
security layer and is never exposed as an LLM tool. OPA-filtered schema context and the graph's
schema-aware SQL validation remain mandatory before ad-hoc execution; adapter validation is a
second check.

## Multi-Database Boundary

The standard MCP transport is reusable. Each future engine receives a small engine-specific
`DatabaseGateway` adapter responsible for its Toolbox tool names, schema-result mapping, dialect
validation, read-only verification, and result normalization. The graph continues to depend only
on `DatabaseGateway`; a single adapter with growing PostgreSQL/MySQL/SQL Server conditionals is
not the target design.

## Limitations

- Current PostgreSQL `list_tables` covers ordinary and partitioned tables, while the direct
  PostgreSQL adapter has richer view/materialized-view/foreign-table discovery.
- Toolbox materializes a tool result before the application byte limit can reject it. SQL row
  limits and database-governed analytical views remain important.
- Generic `execute_sql` has no bound-parameter contract. Parameterized application queries require
  a dedicated configured Toolbox tool or an engine adapter that supports binding.
- No live Toolbox service was required for this milestone; the MCP transport and current payload
  contract are covered with deterministic transport tests.

## References

- https://github.com/googleapis/mcp-toolbox/blob/main/UPGRADING.md
- https://mcp-toolbox.dev/integrations/postgres/tools/postgres-list-tables/
- https://mcp-toolbox.dev/integrations/postgres/tools/postgres-execute-sql/
- https://mcp-toolbox.dev/integrations/postgres/prebuilt-configs/postgresql/
- https://mcp-toolbox.dev/integrations/postgres/source/
