# ADR 0002: Wren as a Read-Only Semantic Context Service

Date: 2026-08-24

## Status

Accepted for the Wren semantic-layer experiment.

## Context

The current Wren AI open-source architecture is no longer the legacy multi-container GenBI
application. The active open-source distribution is the `wrenai` CLI/Python SDK, an MDL v5
project compiled to `target/mdl.json`, and an optional MCP server embedded in the CLI. The
legacy Wren AI service, UI, launcher, and ibis-server deployment are archived.

Current primary references:

- https://docs.getwren.ai/oss/reference/mdl
- https://docs.getwren.ai/oss/reference/cli
- https://docs.getwren.ai/oss/concepts/what_is_mdl
- https://github.com/Canner/WrenAI
- https://pypi.org/project/wrenai/0.13.2/

## Decision

### Required components

1. A version-controlled Wren MDL v5 project containing the synthetic enterprise models,
   physical table references, descriptions, calculated fields, and relationships.
2. Reviewable business rules under `knowledge/rules/` for semantics that are not safely
   inferable from the physical PostgreSQL catalog.
3. One local Wren `0.13.2` service running:

   ```text
   wren serve mcp --transport http --no-connect
   ```

4. `WrenSemanticGateway`, which calls only Wren's read-only schema/context tools and maps
   their response into the application's typed `SemanticContext`.

The service receives no PostgreSQL credentials. PostgreSQL catalog discovery remains the
source of truth for physical tables and columns. Wren adds business interpretation and the
gateway intersects Wren models with the currently discovered physical metadata.

### Optional components deferred

- Wren's LanceDB/embedding memory is unnecessary for the current nine-model schema. The
  adapter performs bounded deterministic selection when Wren returns its small-schema full
  context response.
- PostgreSQL connection profiles, `run_sql`, `dry_run`, and `query_cube` are disabled by
  `--no-connect`.
- Wren Cubes are deferred to the later governed-metrics experiment.
- Wren's GenBI apps and commercial UI/team services are not required.
- Wren's agent SDK packages are unnecessary because LangGraph already depends on the
  internal `SemanticGateway` contract.

### Text-to-SQL responsibility

Wren MDL can expand semantic-model SQL into physical SQL, and Wren's CLI includes agent
workflows. The current no-connect MCP API does not expose a stable natural-language-to-SQL
tool. This milestone therefore keeps:

```text
SemanticGateway -> LLMGateway/sql-reasoner -> candidate SQL
```

`SQL_GENERATION_PROVIDER=llm` is explicit. A future Wren SQL-generation adapter may be
evaluated separately, but it must converge on the same SQLGlot and DatabaseGateway path.

### Security boundary

Wren is context, not authorization or execution security. Authoritative execution remains:

```text
candidate SQL -> SQLGlot -> DatabaseGateway -> read-only PostgreSQL
```

The existing single-statement, SELECT-only, schema/table allowlist, timeout, row-limit,
result-size, provenance, and physical database-role controls remain mandatory.

## Consequences

- `SEMANTIC_PROVIDER=inmemory` preserves deterministic development behavior.
- `SEMANTIC_PROVIDER=wren` has no fallback and fails with a typed provider error when the
  Wren service or MCP client is unavailable.
- The Wren deployment is one credential-free context container, not a replacement database
  gateway.
- Physical metadata is not copied into application code. The MDL physical declarations are
  a generated/reviewed semantic artifact and are reconciled with live catalog metadata at
  retrieval time.
- First-class Wren cube measures remain intentionally out of scope. This milestone exposes
  model calculated fields and reusable business definitions without pre-empting the later
  Cube/Wren Cubes decision.
