# ADR 0011: Wren as the Governed Metric Provider

## Status

Accepted. Supersedes [ADR 0003](0003-governed-metric-layer.md).

## Context

ADR 0003 selected Cube Core as the production `MetricGateway` provider based on measured
p50/p95 latency, native access control, and pre-aggregation capability, and explicitly
rejected keeping Wren as a second production metric engine. That evidence was not wrong and
is preserved unchanged in ADR 0003.

This ADR records a directed architecture change: the project now standardizes on Wren for
governed metrics, reversing ADR 0003's provider choice while keeping its component boundary
intact. `WrenCubeMetricGateway` already existed as the frozen experiment behind
`build_experimental_wren_metric_gateway`; this decision promotes it to the default
production path.

## Decision

`METRIC_PROVIDER` defaults to `wren`. `app/metrics/factory.py::build_metric_gateway` routes
on the configured provider; `CubeMetricGateway` remains fully implemented and selectable
with `METRIC_PROVIDER=cube`, so nothing about the Cube path is deleted. Selection stays
explicit with no cross-provider fallback, consistent with AGENTS.md.

The execution boundary is unchanged from ADR 0003:

```text
LangGraph / QueryRouter
        |
        v
provider-neutral MetricQuery
        |
        v
MetricGateway -> WrenCubeMetricGateway -> structured CubeQuery -> generated SQL
                                        -> SQLGlot -> DatabaseGateway -> read-only PostgreSQL
```

Wren remains translation-only and credential-free, consistent with ADR 0002: it never
receives database credentials and never executes a query itself. Every candidate it
produces is revalidated by SQLGlot before `DatabaseGateway` runs it through the existing
physically read-only role. This is the same safety boundary Wren already had as an
experiment; promoting it to the default does not weaken it.

## Consequences

The tradeoffs ADR 0003 measured still apply and are accepted, not disproven, by this
decision:

- **Latency.** ADR 0003 measured Wren p50/p95 total query latency at roughly 7x Cube's
  (498 ms vs 67 ms p50 on the same fixture). Governed-metric requests will be slower under
  Wren than they were under Cube.
- **Access control.** Wren's OSS engine has no built-in row/column-level security; any
  metric-level authorization must come from OPA in front of `MetricGateway`, not from Wren
  itself.
- **Cross-fact metrics.** A Wren cube has one base object. Metrics spanning multiple fact
  tables need a reviewed base view to resolve the join grain before aggregation, same as
  documented in ADR 0003.
- **No pre-aggregation/cache runtime.** Wren's `refresh_time` is schema metadata, not an
  operational cache. Every governed-metric request re-translates and re-executes.

What changes operationally:

- `scripts/start_backend.ps1` brings up the `wren` compose profile and service instead of
  `cube`.
- `.env.example` defaults to `METRIC_PROVIDER=wren`; `CUBE_API_URL`/`CUBE_API_TOKEN` remain
  present for anyone who opts back into `METRIC_PROVIDER=cube`.
- `build_experimental_wren_metric_gateway` is renamed to `build_wren_metric_gateway` — it is
  no longer an experiment once it is the default.
- The `enterprise-data-metrics` benchmark CLI is unchanged: `--provider wren` and
  `--provider cube` both remain directly selectable regardless of `METRIC_PROVIDER`, so the
  two providers can still be benchmarked side by side.

## Risks

- ADR 0003's 25-case suite proved contract and fixture correctness on synthetic data, not
  production concurrency or latency at scale. Re-run `evals/metrics_cases.json` against Wren
  before any non-development deployment to confirm this still holds.
- No metric-level access-policy tests exist yet for either provider through OPA. This must
  land before Wren governed metrics are exposed to users with different authorization scopes.
- If cross-fact governed metrics are added later, they require new reviewed Wren base views;
  this is added modeling work Cube's directed joins did not need.

## Migration Path

1. Treat `app/metrics/catalog.py` definitions as the source of truth; Wren's MDL cubes must
   keep matching them, same requirement ADR 0003 already stated for whichever provider is
   primary.
2. Re-run `evals/metrics_cases.json` against `--provider wren` after any MDL change and keep
   the report alongside this ADR.
3. Add OPA-backed metric-level authorization tests before any deployment with
   non-uniform user access to governed metrics.
4. If Cube is later removed rather than kept as a selectable alternative, do that as its own
   ADR rather than silently deleting `CubeMetricGateway`.
