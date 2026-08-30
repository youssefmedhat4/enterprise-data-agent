# ADR 0003: Governed Metric Layer

Date: 2026-08-24

## Status

**Superseded by [ADR 0011](0011-wren-governed-metrics.md).** The evidence, benchmark, and
capability comparison below are preserved as the historical record of the original
decision; they were not re-run or invalidated. ADR 0011 documents why the production
default was changed to `METRIC_PROVIDER=wren` despite this evidence.

Originally accepted: **B. CUBE_PRIMARY**.

## Context

Official business KPIs must have reviewed definitions that callers can slice and filter but
cannot rewrite. This responsibility is distinct from semantic context retrieval and physical
database access:

```text
SemanticGateway = business and data meaning
MetricGateway   = official KPI definition and value
DatabaseGateway = controlled physical query execution
```

The seven representative KPI definitions are `active_headcount`, `annual_base_payroll`,
`net_payroll`, `invoice_amount`, `project_cost`, `project_margin`, and
`budget_utilization`. The same provider-independent requests and deterministic fixture
expectations will be run against both candidates.

## Current Implementation Evidence

### Wren Cubes

The current Wren AI open-source MDL v5 schema supports cubes bound to one model or view,
with measures, dimensions, time dimensions, hierarchies, metadata, and a `refresh_time`
field. Its structured `CubeQuery` supports one cube, named measures and dimensions, one
time dimension, filters, limit, and offset. Wren 0.13.2 can translate that request to SQL
without executing it through `wren cube query --sql-only`; the MCP `query_cube` tool also
has an `sql_only` option, though the standard `--no-connect` server omits that tool.

Wren cubes do not independently define cross-cube relationships. A cube has one
`base_object`; multi-fact metrics therefore require a reviewed base view that resolves the
join grain before aggregation. The current implementation exposes structured translation,
not a materialized pre-aggregation service. `refresh_time` is schema metadata and is not,
by itself, evidence of an operational cache in the installed local path.

References:

- https://docs.getwren.ai/oss/reference/mdl
- https://docs.getwren.ai/oss/reference/cli
- https://docs.getwren.ai/oss/guides/cubes
- https://docs.getwren.ai/oss/sdk/wasm
- https://github.com/Canner/WrenAI

### Cube Core

Current Cube Core models entities as cubes with measures, dimensions, time dimensions,
joins, hierarchies, views, access policies, and optional pre-aggregations. Its stable REST
query contract accepts named measures/dimensions, filters, time dimensions, ordering,
limit, and offset; Core also exposes PostgreSQL-compatible Semantic SQL, GraphQL, and model
metadata APIs. Native execution expands a structured query against the model and sends the
resulting query to the configured data source. Cube Store and refresh workers become
operational dependencies when production pre-aggregations are enabled.

Cube Core is open source and self-hostable. Managed administration, collaboration, and
some agent-facing product surfaces must not be conflated with the Core runtime. The
experiment pins the current `v1.7.14` release and uses only Core's structured REST API.

References:

- https://docs.cube.dev/docs/introduction
- https://docs.cube.dev/docs/data-modeling/cubes
- https://docs.cube.dev/docs/data-modeling/joins
- https://docs.cube.dev/reference/data-modeling/measures
- https://docs.cube.dev/reference/core-data-apis/rest-api/query-format
- https://docs.cube.dev/docs/data-modeling/access-control/index
- https://docs.cube.dev/admin/deployment/core
- https://docs.cube.dev/admin/connect-to-data/data-sources/postgres
- https://github.com/cube-js/cube/releases/tag/v1.7.14

## Experimental Execution Architecture

Wren and Cube retain their native strengths behind one normalized `MetricGateway` result.

```text
Wren:
MetricQuery -> WrenCubeMetricGateway -> structured CubeQuery -> generated SQL
            -> SQLGlot -> DatabaseGateway -> read-only PostgreSQL

Cube:
MetricQuery -> CubeMetricGateway -> validated structured REST query -> Cube Core
            -> read-only PostgreSQL
```

For Wren, a dedicated translation-only MCP tool will expose `cube_query_to_sql` while the
service remains database-credential-free. Wren is not an execution or authorization
boundary.

For Cube, native execution is the supported architecture. The gateway accepts no arbitrary
SQL or formula input, validates all members against the governed catalog, and Cube connects
with the existing physically read-only role. SQLGlot remains mandatory for ad-hoc
Text-to-SQL and Wren-generated candidate SQL; it is not inserted after Cube's structured
API through a brittle SQL extraction workaround. Future OPA policy context can be passed at
the gateway boundary.

## Decision

Choose **B. CUBE_PRIMARY**. Cube Core is the production direction and eventual single
executable source of truth for governed KPI definitions. `MetricGateway` is the application
boundary and `CubeMetricGateway` uses Cube's structured REST API. Wren remains an optional
`SemanticGateway`, consistent with ADR 0002, but Wren Cubes must not become a second
production metric engine.

This decision is based on equivalent correctness plus materially stronger metric-serving
capabilities: a stable structured API, native metric execution, first-class access policies,
query caching, pre-aggregations, multiple downstream APIs, and a credible path for BI and
non-agent application reuse. It is not based merely on Cube having a larger feature list.

The selected execution path is:

```text
LangGraph or future QueryRouter
        |
        v
provider-neutral MetricQuery
        |
        v
MetricGateway -> CubeMetricGateway -> Cube REST API
                                      |
                                      v
                                Cube Core model
                                      |
                                      v
                              read-only PostgreSQL
```

The future QueryRouter is explicitly outside this ADR and has not been implemented.

## Alternatives

- **WREN_CUBES_PRIMARY** was rejected. It reuses the existing MDL and preserves the
  application's SQLGlot/DatabaseGateway execution path, but the installed cube runtime is a
  structured SQL translator rather than a mature metric-serving, caching, and
  pre-aggregation system. Cross-fact metrics require reviewed base views because each cube
  has one base object. OSS Wren access control with users/groups is not available.
- **WREN_CUBES_NOW_CUBE_LATER** was rejected. Both providers worked now, so adopting Wren
  temporarily would create an avoidable metric-definition migration and a period with two
  sources of truth.
- **NEED_SPECIFIC_FIX_BEFORE_DECISION** was rejected. Both live providers passed all cases;
  there is no correctness or infrastructure blocker that prevents a decision.

## Evidence

### Deterministic correctness and governance

The new `evals/metrics_cases.json` suite has 25 cases covering all seven KPIs, plain metrics,
dimensions, multiple dimensions, filters, time grouping, no-result behavior, row limits,
repeated requests, and invalid metric/dimension/filter attempts. Expected values were
derived directly from the synthetic fixture without an LLM.

| Result | Wren Cubes | Cube Core |
|---|---:|---:|
| Cases passed | 25/25 | 25/25 |
| Result correctness | 100% | 100% |
| Invalid member/formula attempts blocked | 3/3 | 3/3 |
| Provider errors | 0 | 0 |
| Database | read-only PostgreSQL | read-only PostgreSQL |

Both providers returned semantically equivalent values. Provider-specific time aliases and
wire values are normalized by the adapters; expected rows and formulas were not changed.

### Measured local performance

These are small-fixture development measurements, not production capacity claims. They were
captured sequentially on the same machine and PostgreSQL fixture.

| Measurement | Wren Cubes | Cube Core |
|---|---:|---:|
| p50 total query latency | 498.158 ms | 66.937 ms |
| p95 total query latency | 639.984 ms | 117.016 ms |
| mean total latency | 478.245 ms | 59.484 ms |
| mean translation/retrieval | 526.630 ms | 0.006 ms local mapping |
| mean execution/service call | 12.230 ms DB | 67.533 ms Cube + DB |
| observed container memory | 93.28 MiB | 195.7 MiB |
| restart-to-healthy | about 5.85 s | about 5.62 s |

Wren's measurement includes a fresh MCP session and cube translation per query. It could be
optimized, but that was not necessary to make the architectural decision. Cube's first query
was 76.855 ms and the repeated headcount query was 48.573 ms. Wren's corresponding values
were 561.868 ms and 494.903 ms.

### Modeling and operations

The equivalent experiment required five Wren metric views plus five cubes (221 YAML lines)
and five Cube models (213 YAML lines). Both are understandable at this size. Each development
path adds one container alongside PostgreSQL. The Cube image also launches a Cube Store
process inside the development container; a production pre-aggregation topology adds API
instances, refresh workers, and a Cube Store cluster. Wren is therefore operationally
simpler when used only as a translator, while Cube has the stronger scale and cache path.

### Capability comparison

| Capability | Wren Cubes 0.13.2 | Cube Core 1.7.14 |
|---|---|---|
| Governed measures | Yes | Yes |
| Dimensions | Yes | Yes |
| Time dimensions | Yes; one per CubeQuery | Yes; multiple structured time dimensions |
| Hierarchies | Yes | Yes |
| Relationships | Through base model/view; one base object per cube | First-class directed cube joins and views |
| Calculated metrics | Aggregate/arithmetic expressions; cross-fact work needs a base view | Calculated and multi-stage measures |
| Structured queries | CubeQuery via CLI/MCP/WASM | REST query API and client SDKs |
| PostgreSQL | Yes | Yes |
| Caching | `refresh_time` metadata; no managed cache demonstrated in this path | In-memory query cache and refresh keys |
| Pre-aggregation | Structured aggregate SQL; no materialized rollup runtime demonstrated | First-class pre-aggregations with Cube Store |
| REST API | No dedicated OSS cube REST API | Yes |
| SQL API | CLI/engine SQL, not a PostgreSQL network endpoint | PostgreSQL-compatible Semantic SQL API |
| GraphQL API | No | Yes, with documented limitations |
| MCP | OSS MCP includes cube query when connected | Cube platform MCP; not established as a Cube Core endpoint |
| Access control | OSS engine lacks user/group RLS/CLS; commercial capability | Core access policies support groups, row/member rules, and masking |
| Self-hosted OSS | Yes, Apache 2.0 core | Yes, Apache 2.0 backend |
| Operational complexity | One translator container in this experiment | One dev container; multi-role topology for production pre-aggregations |
| Reuse existing Wren model | Native | No; separate model required |
| Agent integration complexity | Custom translation-only MCP tool plus SQL planning | Direct structured REST adapter |
| p50 query latency here | 498.158 ms | 66.937 ms |
| Correctness here | 25/25 | 25/25 |

Cube's MCP, hosted administration, workbooks, dashboards, and AI product features are not
treated as Cube Core evidence. Conversely, Wren commercial row/column access control is not
treated as an OSS engine capability.

## Consequences

- `METRIC_PROVIDER=cube` is the default direction. Selection remains explicit and has no
  fallback.
- Cube executes structured metric requests natively. It receives only approved member names
  and uses `eda_readonly`; it does not accept arbitrary user SQL through `MetricGateway`.
- SQLGlot remains mandatory for ad-hoc Text-to-SQL and for the Wren experimental generated-SQL
  path. It is not forced after Cube through a brittle SQL extraction step.
- Cube's development mode disables authentication and is local-only. Production must use
  authenticated Core APIs, security-context mapping, access policies, network controls, and
  the existing read-only database boundary. OPA can be integrated before `MetricGateway` in
  the later authorization milestone.
- Wren continues as an optional semantic context provider. That decision is independent of
  the governed metric source of truth.
- The Wren metric definitions and adapter are retained only long enough to reproduce and
  review this experiment. They are not an accepted dual-production architecture.

## Risks

- Multi-fact measures can fan out if invoice and cost facts are joined before each is
  aggregated to project grain.
- Native Cube execution has a different SQL-observability and validation path from Wren.
- Development-mode service configuration is not a production access-control design.
- Cube has higher idle memory use and greater production operational burden when
  pre-aggregations are enabled.
- The provider-neutral catalog currently mirrors formula descriptions and member metadata.
  Leaving it hand-maintained after Cube becomes authoritative would create drift.
- The 25-case synthetic suite proves contract and fixture correctness, not production data
  scale, concurrency, cache behavior, or authorization policy correctness.

## Migration Path

1. Review and accept this ADR and the frozen Wren/Cube result artifacts.
2. Before implementing the QueryRouter, remove the Wren metric cubes/views and production
   selection path, retaining Wren only behind `SemanticGateway`.
3. Make Cube's model/Meta API the source for executable definitions and generated descriptive
   metadata so `app/metrics/catalog.py` does not become a manually duplicated formula store.
4. Add production API authentication, security-context-to-group mapping, access-policy tests,
   and secrets-manager configuration before any non-synthetic deployment.
5. Introduce pre-aggregations only when measured workload evidence justifies the additional
   refresh-worker and Cube Store topology.
6. Keep provider-independent metric IDs, `MetricQuery`, normalized results, typed failures,
   and provenance stable while the future QueryRouter is added.
