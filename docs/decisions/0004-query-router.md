# ADR 0004: Deterministic Query Router and Governed Metric Planning

Date: 2026-08-24

## Status

Accepted by implementation, pending architecture review.

## Context

ADR 0003 selected Cube Core as the sole production source of governed KPI definitions.
The application therefore needs to distinguish questions that should use a certified Cube
measure from row-level or otherwise ad-hoc analytics that require semantic retrieval and
Text-to-SQL. Routing must happen before SQL generation and must not itself generate SQL,
invent a formula, execute data access, or expose model reasoning.

False governed-metric routing is especially dangerous: Cube can return a technically valid
answer for the wrong business intent. Provider failure must also never cause a governed
request to fall back to ad-hoc SQL, because that would bypass the governance decision.

## Decision

Use a deterministic-first `QueryRouter` backed by aliases stored with the governed
`MetricCatalog`. The effective route contract has four execution-relevant values:

- `governed_metric`
- `adhoc_analytics`
- `clarify`
- `block`

`LOOKUP` is represented by the `row_level_lookup` reason code on the ad-hoc route because it
does not yet have different execution behavior. Follow-up is represented by
`requires_prior_context=true` and `followup_reference`; the decision retains the prior
effective route instead of introducing a temporary route that cannot be executed.

Route decisions contain only confidence, a machine-readable reason code, candidate metric
IDs, and safe clarification/block fields. They contain no hidden reasoning prose.

## Routing Precedence

1. Detect prohibited write intent in English, Arabic, and mixed-language requests.
2. Detect explicit continuation references and retain the prior effective route.
3. Match exact catalog metric aliases.
4. Match catalog semantic aliases when aggregate intent is present.
5. Keep raw-record retrieval and non-governed calculations on the ad-hoc path.
6. When two or more catalog metrics are named in one question, route to ad-hoc
   analytics. Cube still executes one measure per request; a comparison table is
   therefore SQL, not a clarification loop.
7. Clarify only unresolved continuation when no prior analysis exists.
8. Default permitted analytical questions to ad-hoc analytics.

The router does not call an LLM. A future constrained classifier may be added only for
unresolved cases and must preserve these deterministic high-confidence decisions.

## Metric Planning

`MetricRequestPlanner` converts a governed decision into the existing provider-neutral
`MetricQuery`. It may emit only catalog metric IDs, approved dimensions and filters, an
approved time dimension/grain, bounded limit, and governed-member ordering. SQL, formulas,
Cube schema, and arbitrary expressions are absent from the contract. Catalog validation is
mandatory before execution.

The current normalized gateway executes one metric per request. Multi-metric
questions are routed to `adhoc_analytics` so Text-to-SQL can join the underlying
facts at one grain. The router does not issue hidden parallel Cube queries. A
clean multi-measure extension to `MetricGateway` remains the way to keep those
comparisons on the governed path later.

## Follow-Ups and Clarification

LangGraph checkpoints retain the minimum continuation state: effective route and the prior
typed `MetricQuery` for governed requests. A follow-up such as `Only Engineering` preserves
the metric and dimensions and adds a validated department filter. `by project` / `per X`
is treated as a dimension continuation of the current metric. A follow-up that names a
different catalog metric (`what about project cost by project`) switches the governed
measure instead of re-asking which analysis to continue. Ad-hoc follow-ups remain
ad-hoc and reuse the existing structured analytical context.

When prior context is absent and the user uses a continuation phrase, or a governed filter
is not representable, the graph clarifies or returns a typed planning failure before SQL or
metric execution. After a clarification turn, naming one catalog metric executes it.
No raw chain-of-thought is persisted.

## Execution and Convergence

```text
                         User Question
                              |
                         QueryRouter
                              |
          +-------------------+-------------------+
          |                                       |
   GOVERNED_METRIC                         ADHOC_ANALYTICS
          |                                       |
 MetricRequestPlanner                      SemanticGateway
          |                                       |
    MetricGateway                            SQL reasoner
          |                                       |
      Cube Core                              SQLGlot
          |                                       |
 readonly PostgreSQL                 DatabaseGateway
                                                  |
                                         readonly PostgreSQL
          |                                       |
          +-------------------+-------------------+
                              |
                       AnalyticalResult
                              |
                    Grounding / Provenance
                              |
                      Answer / Table / Chart

             CLARIFY and BLOCK execute no query
```

Both successful branches populate `AnalyticalResult`, which normalizes columns, rows,
column types, source type and identifiers, truncation, warnings, and execution metadata.
Grounding, chart validation, answer generation, and public API serialization remain shared.

## Fallback Policy

There is no cross-route fallback. `metric_provider_unavailable`,
`metric_planning_failure`, and `invalid_metric_query` stop the governed path. They never
invoke the SQL reasoner. SQL validation and the physical read-only role remain mandatory for
the ad-hoc path even though obvious mutation intent is blocked earlier.

Cube is the only production `METRIC_PROVIDER`. The frozen `WrenCubeMetricGateway` can still
be constructed through an explicitly named experiment builder for ADR 0003 reproduction;
it is not selectable through application production configuration.

## Provenance and Performance

Internal provenance now records route, reason code, confidence, metric ID/version,
dimensions, filters, provider, execution source, routing latency, planning latency, metric
retrieval latency, and metric execution latency. These diagnostics are exposed publicly only
under the existing explicit debug option.

The deterministic router suite completed with 100% route and metric-ID accuracy,
zero false governed-metric routes, and sub-millisecond development latency. These fixture
results validate the implemented contract, not unconstrained natural-language coverage.

## Security Implications

- Write blocking is early defense in depth, not the final database security boundary.
- The router never accepts SQL, formulas, or arbitrary Cube members.
- A failed Cube request cannot bypass governance through ad-hoc SQL.
- Clarification and blocking branches execute neither provider.
- Authorization remains a later milestone and must run before either data execution path.

## Consequences

- Governed KPI questions bypass the SQL reasoner and use Cube's certified definitions.
- Row-level requests containing metric-like nouns remain eligible for ad-hoc SQL.
- Catalog aliases and dimension metadata become routing/planning inputs and must evolve with
  the authoritative Cube model metadata to avoid drift.
- Deterministic matching is intentionally conservative; unsupported language defaults to
  ad-hoc or clarification rather than loosely selecting a metric.
- The legacy frozen Text-to-SQL evaluator explicitly keeps its SQL-only graph mode, so router
  changes do not rewrite historical model baselines.

No OpenMetadata, authentication, OPA, MCP Toolbox, Langfuse, frontend, or uncontrolled agent
loop is introduced by this decision.
