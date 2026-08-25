# ADR 0006: Authentication and OPA Authorization Boundaries

Date: 2026-08-24

## Status

Accepted.

## Context

The analytics API previously had no request identity or policy boundary. Semantic retrieval,
Text-to-SQL, and governed metrics could therefore see every resource exposed by the configured
development catalog. SQL safety and the PostgreSQL read-only role prevented writes, but they do
not decide which user may read a schema, table, column, or certified metric.

Authentication and authorization solve different problems and must remain replaceable. The
application must also prevent denied metadata from entering model context, rather than relying on
post-generation SQL rejection alone.

## Decision

Introduce separate `AuthenticationGateway` and `AuthorizationGateway` interfaces.

The FastAPI boundary authenticates credentials into a typed `UserIdentity`. Development uses one
explicitly configured local identity and does not implement passwords or login. Production uses
the OIDC adapter documented by ADR 0010; tenant-specific Microsoft Entra ID is supported behind
the same interface.

LangGraph adds `authorize_request` immediately after request preparation and before QueryRouter.
It supplies the authenticated subject, operation, bounded database metadata, and governed metric
IDs to the authorization provider. The returned decision contains only allowed schemas,
table/column sets, metrics, provider identity, decision ID, and latency.

Production and staging configuration require the OPA provider. The local policy evaluator is an
explicit development/test option and reads the same checked-in role-grant data used by OPA. Role
grants are policy data, not Python conditionals.

## Enforcement Flow

```text
HTTP credentials -> AuthenticationGateway -> UserIdentity
                                           |
                                           v
Database catalog + governed metric IDs -> AuthorizationGateway -> allowed scope
                                                                  |
                       +------------------------------------------+
                       |
                       v
                  QueryRouter
                  /         \
      allowed Cube metric    authorized schema snapshot
             |                         |
      MetricGateway             SemanticGateway -> LLM
                                           |
                                           v
                                  SQLGlot schema validator
```

The deterministic router checks all requested governed metric candidates against the OPA-returned
metric allowlist. It raises a typed authorization denial before planning or Cube execution if any
candidate is unauthorized. Planning and execution repeat the metric check as defense in depth.

For ad-hoc analytics, the authorization decision filters `TableMetadata`, including column
metadata, keys, and relationships. SemanticGateway receives only this filtered view. Semantic
definitions requiring removed columns are discarded before prompt construction. The exact same
filtered snapshot is passed to SQLGlot validation, so a hallucinated denied column cannot execute.

## Failure Behavior

OPA connection errors, HTTP failures, malformed responses, and decisions containing resources
outside the requested inventory fail closed. They produce sanitized `authorization_unavailable`
errors. Explicit policy denials produce `authorization_denied`. No production fallback to local
policy or allow-all behavior exists.

SQL mutation blocking, projection-star restrictions, query limits, timeouts, result-byte limits,
and the physical PostgreSQL read-only role remain unchanged.

## Local Roles

The development policy defines `analyst`, `hr_analyst`, and `admin_analytics`. The normal analyst
cannot access employee salary, payroll rows, or payroll metrics. HR can access employee/payroll
resources and governed payroll metrics. The admin analytics role has the broader synthetic
analytics scope. These are examples only; production role mapping and policy remain in OPA.

## Provenance

Internal provenance records subject ID, authentication provider, authorization provider, policy
decision ID, an authorized-scope summary, and authorization latency. Roles, policy input, rule
traces, and policy internals are not copied into user-visible provenance, including debug output.

## Consequences

- Authorization precedes both model context and governed metric execution.
- OPA remains replaceable and LangGraph contains no OPA-specific HTTP calls.
- Schema discovery remains a trusted bounded catalog operation; user data queries still occur only
  after authorization and full SQL validation.
- Local development remains service-free but is explicitly prohibited in staging and production.
- OIDC/Entra-compatible token validation is implemented; real tenant registration, consent, and
  claim assignment remain deployment configuration rather than application-owned login logic.
