# ADR 0009: Backend v1 API and Observability Boundaries

Date: 2026-08-24

## Status

Accepted.

## Decision

Cube governed metrics and ad-hoc SQL normalize into one `AnalyticalResult` and one versioned
`AnalyticsResponse`. Grounding and chart validation operate on that normalized result, so neither
route has a weaker answer contract. Public provenance is intentionally small; detailed identity,
policy, provider, governance, model, SQL, and timing data stays internal. SQL debug output requires
an explicit request, deployment switch, and authorization-policy capability.

Observability uses a replaceable `TraceService` rather than importing an exporter into FastAPI or
LangGraph nodes. The first adapters are no-op and content-free structured logging. Future
OpenTelemetry or Langfuse integrations must preserve the rule that prompts, credentials, SQL, and
result rows are not recorded by default.

Liveness reports process availability. Readiness checks the selected database and can require Cube
through explicit configuration. Provider selection remains strict; readiness and request failures
do not trigger fallback.

## Consequences

- Frontend clients consume one stable shape and stable typed errors across both query routes.
- Empty and truncated results remain explicit and grounded.
- Vendor observability can be added without changing graph logic.
- Rich diagnostics remain available internally without becoming an accidental data-exfiltration
  surface.
