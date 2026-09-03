# Backend v1 Architecture

## Scope

Backend v1 is a read-only conversational analytics service. It supports governed metrics (Wren by
default; ADR 0011) and ad-hoc SQL through one FastAPI contract. The repository includes a Next.js
client; production deployment architecture and interactive enterprise tenant setup remain outside
this milestone.

## Request Lifecycle

```text
FastAPI request
  -> AuthenticationGateway
  -> AuthorizationGateway (OPA in staging/production)
  -> PostgreSQL LangGraph checkpoint load
  -> QueryRouter
     -> governed metric: MetricRequestPlanner -> MetricGateway (Wren)
     -> ad-hoc analytics: authorized metadata -> SemanticGateway
                         -> LLMGateway -> SQLGlot -> DatabaseGateway
  -> common grounding, chart validation, provenance, and checkpoint save
  -> AnalyticsResponse
```

Authentication establishes identity; OPA owns permissions. OPA filters the physical PostgreSQL
catalog before OpenMetadata, Wren/in-memory semantics, prompt construction, or schema-aware SQL
validation. Governed metric IDs are authorized before metric planning and execution. Neither the LLM
nor database content can grant access.

Production authentication uses standards-based OIDC discovery and RS256 JWT validation. The
adapter verifies signature, issuer, audience, expiry, not-before, and issued-at values, then maps
configured claims into `UserIdentity`. This is compatible with tenant-specific Microsoft Entra ID
issuers. Local identity remains an explicit development-only adapter; authentication never embeds
authorization policy.

Conversation state is stored through a LangGraph checkpoint boundary. Development tests may use
memory. Integrated and production modes use `AsyncPostgresSaver` against a dedicated checkpoint
database/user, never the read-only analytics role. A `thread_id` loads and saves the same typed
analytical context across requests and process restarts.

## Component Ownership

| Component | Responsibility | Not responsible for |
| --- | --- | --- |
| PostgreSQL catalog | Physical schema truth | Business definitions, policy |
| OPA | Schema/table/column/metric authorization | Authentication, SQL generation |
| OpenMetadata | Optional descriptions, ownership, domains, glossary, sensitivity, lineage, freshness | SQL execution, metric formulas, authorization |
| Wren or in-memory semantics | Relevant business context | Authorization, execution |
| MetricGateway (Wren by default, Cube selectable) | Certified metric definitions and execution | Ad-hoc SQL fallback |
| LiteLLM `LLMGateway` | Logical alias to configured model provider | Data authorization |
| SQLGlot | AST safety, allowed-schema validation, bounded SELECT | Physical database permissions |
| `DatabaseGateway` | Read-only discovery and execution | Reasoning, policy, semantics |
| LangGraph checkpoint store | Durable conversation state | Analytics data access |

Direct PostgreSQL remains the primary database adapter. MCP Toolbox is optionally supported as a
connectivity transport through a PostgreSQL-specific `ToolboxDatabaseGateway`; it is never exposed
to the model. It repeats SQLGlot validation and read-only-role verification, and explicit selection
never falls back. Future SQL Server/MySQL support should use separate dialect-specific adapters
behind `DatabaseGateway`.

For multi-fact ad-hoc analysis, the shared SQL prompt requires an explicit final result grain,
independent pre-aggregation of one-to-many fact sources, and measure-specific filter scopes. The
in-memory semantic provider describes roster, project, invoice-line, and cost-entry grains and
states that project margin combines independently aggregated invoice and cost facts. It also keeps
active-headcount filtering separate from roster payroll and average salary. No post-hoc
`DISTINCT`/`SUM(DISTINCT ...)` correction or frontend row deduplication is used.

## Public API

`POST /analytics/query` accepts a question, optional thread ID, and optional debug request. Both
routes return `AnalyticsResponse` schema version `1.1` with:

- `request_id`, `thread_id`, and `status`
- grounded `answer`, `columns`, `rows`, and validated `chart`
- `sources`, public `provenance`, and `freshness`
- `warnings`, clarification fields, and execution metadata

The same thread ID resumes structured analytical context. Empty results retain typed columns.
Truncated SQL results and governed results that reach their configured limit carry warnings.
Errors use stable codes and sanitized messages; raw provider/database exceptions never enter the
response.

Generated and validated SQL are hidden by default. They are returned only when all three are true:
the request asks for debug data, `API_DEBUG_PROVENANCE_ENABLED=1`, and OPA/local policy grants the
identity `debug`. Internal provenance additionally retains authentication and authorization IDs,
authorized-scope summary, route/provider details, model aliases and physical models, token counts
when exposed, governance source/owner/freshness data, SQL validation/repair state, and timings.

Endpoints:

- `GET /health` and `GET /health/live`: process liveness
- `GET /health/ready`: database and persistent-checkpoint readiness; optionally governed
  metric provider readiness with `READINESS_REQUIRE_METRIC_PROVIDER=1`
- `POST /analytics/query`: analytics workflow

Every HTTP response includes `X-Request-ID`. OpenAPI documents success and typed error shapes.

## Grounding and Provenance

Governed metric and SQL results are normalized to `AnalyticalResult` before answer generation. The answer model sees
only the actual returned rows. Every non-empty response must include structured claims whose row,
field, and value match the result. Claims against absent fields, claims citing a row that does not
exist, claims whose value differs from the stored one, and claims on empty results all fail with a
typed grounding error.

Structured evidence is the boundary that actually prevents invented facts. A secondary sweep then
looks at numerals in the prose that no claim cited. A numeral passes if it is a value in the result,
the exact row count used in explicit result-shape wording, or a bounded integer used in explicit
rank/position wording. Small integers are not generally trusted: `3 projects` still requires result
evidence even when three is less than the row count. Every other numeral must appear in the result.

Failures carry a content-free developer code and structural indexes that are logged and never
serialized. Logs do not contain the answer, evidence values, result values, or rows. The public
message stays generic because the rejected content may be sensitive.

Visualizations are AI-selected and schema-validated (ADR 0012). The `analytics-general`
model chooses the chart type and channels as part of the same grounded answer call, using
the question, the returned rows, the result column types, and the row count. It returns
only a typed declarative specification — bar, line, area, pie, donut, or scatter, with
orientation, stacking, labels, value format, sort, and limit. There is no field in the
contract capable of carrying JavaScript, Vega, HTML, or any other executable payload, and
unknown fields are rejected.

`ChartValidator` is the authority over that choice: every referenced column must exist in
the returned rows, measures must be numeric, scatter additionally requires a numeric x,
and part-to-whole charts reject negative values and high-cardinality results. A column
name is never trusted because the model emitted it. An incompatible chart is dropped to
`chart = null` with a sanitized warning rather than failing the analysis, so the grounded
answer and the table always survive a bad visualization.

`value_format` describes the measure column as stored; `part_to_whole_display` separately
controls whether a pie or donut slice is labelled with its share of the total. That share
is derived at render time from the plotted values and never enters rows, claims, or
provenance. The frontend additionally lets a user re-present the same validated rows as
another compatible chart type; the AI's selection stays the default, alternatives are
filtered by the same rules the backend enforces, and no query, answer, or provenance
changes.

Public provenance contains source, source tables, result fields/metadata, execution timestamp, and
freshness. Sensitive subject IDs, policy decisions, physical model routing, SQL, detailed governance
metadata, and internal latency breakdowns remain internal unless a narrowly authorized debug view
explicitly allows its documented subset.

## Observability

`TraceService` is a vendor-neutral boundary with no-op and structured-logging adapters. It records
content-free spans for FastAPI requests and every explicit LangGraph node, covering authorization,
routing, governance/semantic retrieval, metric planning/execution, LLM generation, SQL validation
and repair, database execution, grounding, and context persistence. Attributes are limited to IDs,
route/provider names, status, latency, and exception class. Prompts, result rows, credentials, and
exception messages are not logged.

Set `OBSERVABILITY_PROVIDER=logging` to emit these spans through standard logging. OpenTelemetry
export and Langfuse can later implement the same boundary; neither is required for normal operation.

## Configuration and Secrets

Local development may copy `.env.example` to the Git-ignored `.env`; fake database, fake LLM,
local identity, local policy, in-memory checkpointing, and disabled governance are explicit
defaults. The integrated local stack instead selects PostgreSQL, PostgreSQL checkpoints, OPA,
Wren, and LiteLLM explicitly. No repository file contains credentials.

Production secrets must be injected as environment variables by the deployment platform or a
future OpenBao/Vault/company secret manager. Secret values use `SecretStr`, are excluded from
settings serialization, and must not be placed in images or manifests. Sending database-derived
content to cloud LLMs requires `ALLOW_CLOUD_DATABASE_DATA=1`.

`POST /analytics/query` accepts a bounded `model_profile` of `gemini_pro` (default) or `gemini`.
The server resolves the selected profile to the existing `analytics-general` and `sql-reasoner`
logical aliases for that request. It never accepts provider names, endpoint IDs, projects, regions,
or physical model IDs from the client. Both profiles are configuration-driven and use Vertex AI
with Application Default Credentials in the documented deployment. The selected physical aliases
remain internal, while the public response exposes only the safe profile and display name. There
is no profile fallback. Wren metric translation is unaffected by the selection, though the selected
`analytics-general` alias still grounds the final answer.

The cloud-data guard is evaluated against the physical aliases selected for the request. This
keeps both current cloud profiles fail-closed without allowing an unused cloud profile to block a
future explicitly selected local profile.

The deployment must inject `APP_ENV=staging` or `APP_ENV=production` in the process environment.
For those values the settings loader omits `.env` entirely and reads injected environment/file
secret sources only.

Staging and production fail configuration validation when read-only verification is disabled, OPA
is not selected, or fake database/LLM, local authentication, or in-memory checkpointing is selected.
An enabled OPA, OpenMetadata, metric, semantic, MCP, database, or model provider never silently
falls back to another provider.

## Security Boundaries

- OPA-filtered tables and columns are the maximum schema snapshot available to semantics, the LLM,
  and SQL validation; OpenMetadata can enrich but cannot broaden it.
- Governed metrics are checked before the configured `MetricGateway` and cannot fall back to
  ad-hoc SQL.
- SQLGlot permits exactly one bounded read-only query, rejects projection stars and system catalogs,
  and validates tables/columns against the authorized snapshot.
- Mutation, multi-statement, unsafe-function, and authorization failures are not repairable. Repaired
  SQL is revalidated from the beginning.
- PostgreSQL and Toolbox PostgreSQL identities must be physically read-only. Application checks do
  not replace grants, default read-only transactions, governed views, or future row-level security.
- Schema descriptions, observed database values, metadata, and rows are explicitly treated as
  untrusted data rather than instructions.
- The checkpoint writer belongs to a separate PostgreSQL database and has no analytics grants.
- Cloud LLM routes cannot receive schema or result content unless
  `ALLOW_CLOUD_DATABASE_DATA=1`; there is no provider fallback.

## Local Commands

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
gcloud auth application-default login
.\scripts\start_backend.ps1
```

`start_backend.ps1` starts the analytics PostgreSQL database, dedicated checkpoint PostgreSQL,
OPA, and Wren, then runs FastAPI. On Windows use this script or `python -m app.runtime`; that entry
point selects the event loop required by psycopg. The required integrated settings are:

```dotenv
DATABASE_PROVIDER=postgres
CONVERSATION_CHECKPOINT_PROVIDER=postgres
CHECKPOINT_DATABASE_URL=postgresql://eda_checkpoint:${CHECKPOINT_PASSWORD}@localhost:5433/enterprise_checkpoints
AUTHENTICATION_PROVIDER=local
AUTHORIZATION_PROVIDER=opa
METRIC_PROVIDER=wren
SEMANTIC_PROVIDER=inmemory
GOVERNANCE_PROVIDER=disabled
LLM_PROVIDER=litellm
LLM_MODEL_GEMINI_ANALYTICS_GENERAL=vertex_ai/gemini-2.5-flash
LLM_MODEL_GEMINI_SQL_REASONER=vertex_ai/gemini-2.5-flash
LLM_MODEL_GEMINI_PRO_ANALYTICS_GENERAL=vertex_ai/gemini-3.1-pro-preview
LLM_MODEL_GEMINI_PRO_SQL_REASONER=vertex_ai/gemini-3.1-pro-preview
LLM_GEMINI_VERTEXAI_LOCATION=global
VERTEXAI_PROJECT=your-project-id
VERTEXAI_LOCATION=global
ALLOW_CLOUD_DATABASE_DATA=1
```

ADC supplies credentials; no API key, service-account key, or bearer token belongs in repository
files. `ALLOW_CLOUD_DATABASE_DATA=1` is still required because schema and result context leave the
process. Provider or model failure returns a typed error and never falls back to another profile.
The LiteLLM gateway itself remains provider-neutral, so an approved deployment can configure a
different physical route without changing LangGraph.

Production replaces local authentication with OIDC:

```dotenv
AUTHENTICATION_PROVIDER=oidc
OIDC_ISSUER=https://login.microsoftonline.com/<tenant-id>/v2.0
OIDC_AUDIENCE=<application-client-id-or-api-identifier>
OIDC_DISCOVERY_URL=https://login.microsoftonline.com/<tenant-id>/v2.0/.well-known/openid-configuration
OIDC_SUBJECT_CLAIM=sub
OIDC_ROLES_CLAIM=roles
```

Quality checks:

```powershell
.\.venv\Scripts\pytest
.\.venv\Scripts\ruff check .
.\.venv\Scripts\mypy .
git diff --check
```

## Intentional Limitations

- One analytics request targets one datasource; cross-database joins are not supported.
- Production Entra tenant consent and interactive login are deployment-specific.
- No Kubernetes or production deployment/high-availability architecture.
- OpenMetadata adapter is mocked/tested, but a full deployment has not been validated here.
- The OIDC/Entra-compatible adapter is implemented and locally verified with signed JWTs; actual
  tenant consent, claims, and interactive login remain deployment-specific.
- No full OpenTelemetry/Langfuse/metrics stack.
- MCP Toolbox currently has a PostgreSQL adapter only; SQL Server/MySQL adapters are deferred.
- Checkpointing uses one PostgreSQL writer and is intentionally not a distributed HA design.
