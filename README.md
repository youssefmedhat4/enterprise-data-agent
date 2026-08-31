# Enterprise Data Agent

Greenfield enterprise conversational analytics platform.

Backend v1 includes:

- FastAPI app with versioned analytics, liveness, and readiness endpoints.
- Environment-based configuration.
- Replaceable LLM gateway with LiteLLM and deterministic fake adapters.
- Typed LangGraph workflow.
- Replaceable database gateway with direct PostgreSQL and optional MCP Toolbox adapters.
- Dockerized synthetic PostgreSQL analytics database.
- SQLGlot read-only SQL validation.
- Automated unit, security, and vertical-slice tests.

The model-independent pipeline now also includes:

- Structured analytical thread context with injected LangGraph checkpoint persistence.
- An in-memory development checkpointer that is rejected for production configuration.
- Evidence-backed claims with deterministic grounding validation.
- AI-selected, schema-validated visualizations: the model picks the chart type and
  channels, the backend verifies every referenced column against the real result, and no
  executable chart code is ever accepted (ADR 0012).
- Separate internal and public provenance; SQL debug requires configuration and policy approval.
- OPA authorization, optional OpenMetadata governance, Wren governed metrics, and deterministic
  query routing.
- Vendor-neutral content-free request and LangGraph tracing.
- Versioned analytics responses and sanitized application error codes.

Conversation checkpoints are separate from the analytics `DatabaseGateway`. Set
`CONVERSATION_CHECKPOINT_PROVIDER=memory` for temporary development. Integrated and production
modes use `postgres` with a dedicated checkpoint database and writer identity; it never falls back
to process memory or uses the analytics read-only role.

## Local Development

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

The default configuration uses `DATABASE_PROVIDER=fake` and `LLM_PROVIDER=fake`. The API and
evaluation suite therefore require no database service, container runtime, or cloud credential.
The finalized responsibilities, API shape, security boundaries, production configuration rules,
and deferred work are documented in [`docs/backend-v1.md`](docs/backend-v1.md).

```powershell
.\.venv\Scripts\python.exe -m app.runtime
```

## Interactive PostgreSQL Analytics

PostgreSQL is not required for local development. On an environment with PostgreSQL and Docker
Compose available, set `DATABASE_PROVIDER=postgres` plus unique local values for
`POSTGRES_ADMIN_PASSWORD` and `EDA_READONLY_PASSWORD` in `.env`, then run:

```powershell
docker compose --env-file .env -f infra/compose/docker-compose.yml up -d
```

The optional compose service initializes a synthetic `enterprise_analytics` database and the
`eda_readonly` login. Initialization grants that login only `CONNECT`, schema `USAGE`, and
table `SELECT`, and sets `default_transaction_read_only=on`.

For any PostgreSQL database, configure the allowed schemas and bounded execution controls:

```dotenv
DATABASE_PROVIDER=postgres
DATABASE_URL=postgresql://enterprise_analytics_reader@host:5432/database
DB_ALLOWED_SCHEMAS=analytics
DB_REQUIRE_READ_ONLY=1
DB_QUERY_TIMEOUT_SECONDS=10
DB_MAX_ROWS=100
DB_MAX_RESULT_BYTES=1000000
DB_CATEGORICAL_COLUMNS=status,type,category,region
DB_CATEGORICAL_MAX_VALUES=20
DB_CATEGORICAL_MAX_VALUE_LENGTH=64
DB_CATEGORICAL_MAX_COLUMNS=50
```

The PostgreSQL adapter discovers tables, views, columns, types, nullability, primary keys, foreign
keys, and relationship paths from PostgreSQL catalogs. It retrieves only bounded values for the
configured low-cardinality column names, and caches schema metadata for
`DB_SCHEMA_CACHE_SECONDS`.

### Optional MCP Toolbox Connectivity

`DATABASE_PROVIDER=toolbox` selects an optional MCP Toolbox for Databases adapter while preserving
the same `DatabaseGateway` used by LangGraph. The current implementation targets Toolbox's stable
streamable HTTP `/mcp` endpoint and PostgreSQL `list_tables`/`execute_sql` tools. It does not expose
MCP tools to the LLM.

```dotenv
DATABASE_PROVIDER=toolbox
TOOLBOX_MCP_URL=http://localhost:5000/mcp
TOOLBOX_SOURCE_ID=enterprise-postgres
TOOLBOX_DIALECT=postgres
TOOLBOX_EXECUTE_TOOL=execute_sql
TOOLBOX_SCHEMA_TOOL=list_tables
DB_ALLOWED_SCHEMAS=analytics
DB_REQUIRE_READ_ONLY=1
```

A current flat Toolbox configuration is provided at `infra/toolbox/tools.yaml`. Supply its
`TOOLBOX_POSTGRES_*` environment values to the Toolbox process and use the same physically
read-only role documented for direct PostgreSQL. The adapter verifies that role, rediscovers the
physical schema through Toolbox, revalidates every query with SQLGlot, and applies application
row/byte/time limits. Explicit Toolbox selection never falls back to direct PostgreSQL.

Toolbox's generic PostgreSQL execute tool is documented for human-in-the-loop developer use, so
this integration treats it strictly as transport. OPA, semantic context, governed metrics, SQL
safety, and database permissions remain outside Toolbox. See
`docs/decisions/0008-mcp-toolbox-database-adapter.md` for limitations and the multi-engine design.

The exact role-creation and verification procedure is documented in
[`docs/postgres-readonly-role.md`](docs/postgres-readonly-role.md). Staging and production reject
`DB_REQUIRE_READ_ONLY=0`; pooled connections also fail closed when the role default or privileges
are write-capable.

Use a configured local or approved cloud model through LiteLLM, then start one persistent
development chat thread:

```powershell
.\.venv\Scripts\enterprise-data-chat --debug
```

The command requires `DATABASE_PROVIDER=postgres` and `LLM_PROVIDER=litellm`; it never falls back
to Fake or DuckDB. Debug output includes validated SQL, selected schema IDs, semantic definition
IDs, source tables, execution time, and live-result metadata. If PostgreSQL data will be sent to a
cloud model, explicitly set `ALLOW_CLOUD_DATABASE_DATA=1` only for approved non-sensitive data.

PostgreSQL tests are opt-in:

```powershell
.\.venv\Scripts\pytest --run-postgres -m postgres tests\integration\test_postgres.py -vv
```

## Optional Wren Semantic Context

The default `SEMANTIC_PROVIDER=inmemory` remains deterministic. The Wren experiment adds a
single credential-free Wren `0.13.2` context service and an MDL v5 project without changing
database execution. Start it and compare bounded context selection with:

```powershell
docker compose --env-file .env -f infra/compose/docker-compose.yml --profile wren up -d --build wren
.\.venv\Scripts\python.exe -m pytest -m wren --run-wren -v
.\.venv\Scripts\enterprise-data-semantic-compare.exe "Which department has the highest payroll?"
```

See `docs/wren-semantic-layer.md` and
`docs/decisions/0002-wren-semantic-gateway.md` for the deployment and architecture decision.

## Governed Metrics

Official KPI queries use a separate `MetricGateway`; they do not let an LLM or caller replace
the reviewed formula. ADR 0011 selects Wren as the production metric direction, superseding
ADR 0003's original Cube Core choice; Wren remains credential-free and translation-only, and
`DatabaseGateway` executes the revalidated SQL through the existing read-only role. Start the
pinned local Wren profile and run the provider-independent 25-case metric suite with:

```powershell
docker compose --env-file .env -f infra/compose/docker-compose.yml --profile wren up -d --build postgres wren
$env:DATABASE_PROVIDER = "postgres"
.\.venv\Scripts\enterprise-data-metrics.exe --provider wren --output artifacts\metrics-wren.json
```

Cube Core remains fully implemented and selectable with `METRIC_PROVIDER=cube`:

```powershell
docker compose --env-file .env -f infra/compose/docker-compose.yml --profile cube up -d postgres cube
.\.venv\Scripts\enterprise-data-metrics.exe --provider cube --output artifacts\metrics-cube.json
```

Both `--provider` values are always directly selectable through this CLI, regardless of the
configured `METRIC_PROVIDER` default, so the two providers can still be benchmarked side by
side. See `docs/governed-metrics.md`, `docs/decisions/0011-wren-governed-metrics.md`, and
`docs/decisions/0003-governed-metric-layer.md` for the original evidence and the tradeoffs
this decision accepts.

## Query Routing

Deterministic safety runs first: mutation blocking and clarification never depend on a
model. Whether a question is *governed* is then decided semantically, not by matching
configured aliases. Authorized certified metrics are retrieved for the active datasource,
the model selects among only those candidates, and `MetricIntentValidator` decides what is
executable — a selection naming an unknown or unauthorized metric falls back to ad-hoc SQL
rather than executing. Governed plans run through Wren; ad-hoc questions continue through
semantic context, the SQL reasoner, SQLGlot, and `DatabaseGateway`.

A broad paraphrase such as "How much money does the organization commit to employee base
compensation each year?" reaches `annual_base_payroll` without containing any configured
alias.

## Learned Analytics Knowledge

Each database develops its own understanding, and a human approves all of it.

Terminal requests are recorded as question memory: what was asked and what *shape* of
analysis answered it, never the answer. Repeated questions with the same analytical
structure form per-datasource clusters, and a cluster that recurs and repeatedly succeeds
can produce a PROPOSED knowledge candidate — a derived metric, a query example, or a
business rule.

Nothing becomes authoritative without review. Approving a metric candidate re-validates its
dependencies, grain, expression and dimensions against the registry before certifying it;
derived metrics are bounded arithmetic over already-certified metrics, never SQL. Approved
query examples reach the model as reference only and every generated statement still passes
SQLGlot, current authorization and the read-only role. Rejected candidates are remembered so
the same proposal is not immediately recreated.

Question memory never answers a question. Current numbers always come from the live
database. Retention is opt-in via `QUESTION_MEMORY_ENABLED`; ordinary logs stay
content-free.

Reviewers use the Knowledge console at `/knowledge`, which requires the `knowledge_review`
capability — separate from analytics access, because reading data is not authority over what
the data means. See `docs/decisions/0014-analytics-learning-loop.md`.

Run the English, Arabic, and mixed-language routing baseline with:

```powershell
.\.venv\Scripts\enterprise-data-router-eval.exe `
  --output artifacts\router-baseline.json
```

See `docs/decisions/0004-query-router.md` for route precedence, continuation state, and the
metric-planning contract.

## Schema-Aware SQL Validation

Ad-hoc generated SQL passes a typed SQLGlot pipeline before database execution. The pipeline
enforces read-only safety, validates tables and columns against the bounded allowed metadata for
the request, resolves aliases/CTEs/subqueries, rejects system catalogs and unknown functions, and
applies the row limit. This allowed snapshot is separate from the smaller relevance-selected
prompt context. Projection stars are rejected while `COUNT(*)` remains supported.

Likely schema-generation errors may invoke `sql-reasoner` for exactly one structured repair. The
repaired candidate is revalidated from the start; safety failures are never repaired. Internal
provenance records attempts and timings, while candidate SQL remains hidden unless debug output is
explicitly enabled. See `docs/decisions/0005-schema-aware-sql-validation.md`.

Run the deterministic 25-case validation baseline with:

```powershell
.\.venv\Scripts\enterprise-data-sql-validation-eval.exe `
  --output artifacts\sql-validation-baseline.json
```

## Authentication and Authorization

FastAPI authenticates each request through `AuthenticationGateway`, then LangGraph obtains an
allowed schema/table/column/metric scope through `AuthorizationGateway` before QueryRouter. Local
development uses the explicit identity and policy file configured in `.env`; staging and
production require `AUTHORIZATION_PROVIDER=opa` and never fall back to local policy.

Start the optional OPA development service with:

```powershell
docker compose --env-file .env -f infra/compose/docker-compose.yml --profile opa up -d opa
```

Then set `AUTHORIZATION_PROVIDER=opa`. The normal analyst role excludes employee salary, payroll
tables, and payroll metrics; HR and analytics-admin examples are defined in
`infra/opa/data/local_roles.json`. Identity roles and policy internals are not exposed publicly.
See `docs/decisions/0006-authentication-and-opa-authorization.md`.

Production selects `AUTHENTICATION_PROVIDER=oidc`. The adapter uses OIDC discovery and signed JWT
validation, verifies issuer/audience/time claims, and maps configured identity claims into
`UserIdentity` before OPA. Tenant-specific Microsoft Entra ID configuration is supported; no
password store or authorization rules live in the authentication adapter.

## Integrated Backend Startup

For the live synthetic stack on this machine, configure the ignored `.env`, authenticate Google
ADC, then run:

```powershell
gcloud auth application-default login
.\scripts\start_backend.ps1
```

The script starts analytics PostgreSQL, dedicated checkpoint PostgreSQL, OPA, and Wren before
FastAPI. Qwen 3.6 27B is the default request profile and resolves both logical aliases to the
self-hosted Vertex AI endpoint in this project (ADR 0013). Gemini 2.5 Flash is an approved
per-request alternative through `vertex_ai/gemini-2.5-flash` and ADC. Wren metric planning stays
independent of this choice. The Qwen endpoint is a provisioned GPU, costs approximately $4.50 per
hour while deployed, and bills continuously until it is undeployed. A future Ollama deployment only
changes the `LLM_MODEL_*` aliases and `OLLAMA_API_BASE`; no graph code changes. See
[`docs/backend-v1.md`](docs/backend-v1.md) for the complete lifecycle and security boundaries.

## Optional OpenMetadata Governance

OpenMetadata is an optional read-only `GovernanceGateway`; it enriches the OPA-authorized
physical schema with catalog descriptions, ownership, domains, glossary terms, classifications,
sensitivity labels, bounded lineage, and catalog freshness. It neither executes SQL nor owns
governed metric formulas. `GOVERNANCE_PROVIDER=disabled` preserves the service-free development
path. To use an existing deployment, configure:

```dotenv
GOVERNANCE_PROVIDER=openmetadata
OPENMETADATA_API_URL=http://openmetadata-host:8585/api
OPENMETADATA_JWT_TOKEN=
OPENMETADATA_FQN_PREFIX=database_service.database_name
OPENMETADATA_INCLUDE_LINEAGE=1
```

The FQN prefix identifies the OpenMetadata database service and database; the adapter appends the
authorized physical schema and table. When enabled, missing, unavailable, or malformed required
catalog metadata fails with a typed sanitized error. No OpenMetadata service is included in the
local stack. See `docs/decisions/0007-openmetadata-governance-boundary.md`.

## Optional Cloud LLM

Set `LLM_PROVIDER=litellm`, configure `LLM_MODEL_ANALYTICS_GENERAL` and
`LLM_MODEL_SQL_REASONER` with provider/model identifiers understood by LiteLLM, and set the
matching environment credential. Gemini uses `GEMINI_API_KEY`; OpenAI support remains available
through `OPENAI_API_KEY`. Keys are read as Pydantic secrets and are not included in reports. Cloud
use must be limited to synthetic or explicitly approved non-sensitive data.

The analytics API accepts only the server-approved `qwen` and `gemini` profile names. Qwen is the
default. The server resolves those names to both logical aliases for that request; physical model
IDs, projects, endpoints, and regions never come from the browser. The selected profile is used for
SQL and grounded answer generation, and provider failure is returned as an error rather than
falling back to the other profile. The current Gemini profile uses Vertex ADC, not
`GEMINI_API_KEY`.

The Gemini smoke test uses LiteLLM identifier `gemini/gemini-2.5-flash` and requires an additional
explicit opt-in:

```powershell
$env:LLM_PROVIDER = "litellm"
$env:LLM_MODEL_ANALYTICS_GENERAL = "gemini/gemini-2.5-flash"
$env:LLM_MODEL_SQL_REASONER = "gemini/gemini-2.5-flash"
$env:GEMINI_API_KEY = "<secret>"
$env:RUN_CLOUD_LLM_TESTS = "1"
.\.venv\Scripts\pytest --run-cloud -m cloud `
  tests\integration\test_litellm_live.py::test_live_gemini_litellm_structured_output
```

Groq Qwen 3.6 27B uses LiteLLM identifier `groq/qwen/qwen3.6-27b`. The evaluation
loop is sequential, and `EVALUATION_REQUEST_DELAY_SECONDS=2` provides a conservative
free-tier pacing baseline. LiteLLM honors provider `Retry-After` headers during configured
retries; exhausted rate limits are reported as infrastructure failures.

```powershell
$env:LLM_PROVIDER = "litellm"
$env:LLM_MODEL_ANALYTICS_GENERAL = "groq/qwen/qwen3.6-27b"
$env:LLM_MODEL_SQL_REASONER = "groq/qwen/qwen3.6-27b"
$env:GROQ_API_KEY = "<secret>"
$env:RUN_CLOUD_LLM_TESTS = "1"
$env:LLM_MAX_RETRIES = "2"
$env:EVALUATION_REQUEST_DELAY_SECONDS = "2"
.\.venv\Scripts\pytest --run-cloud -m cloud `
  tests\integration\test_groq_live.py::test_live_groq_qwen_structured_sql -vv
```

After the smoke test passes:

```powershell
.\.venv\Scripts\enterprise-data-eval --backend duckdb --llm configured --mode sql `
  --request-delay-seconds 2 `
  --reference-report artifacts\baseline-qwen35-9b.json `
  --output artifacts\baseline-groq-qwen36-27b.json `
  --markdown-output docs\evaluation-groq-qwen36-27b.md
```

### Cerebras GPT-OSS 120B

LiteLLM 1.98.0 registers Cerebras GPT-OSS 120B as `cerebras/gpt-oss-120b` with
JSON-schema response support. Both logical aliases remain configuration-driven:

```powershell
$env:LLM_PROVIDER = "litellm"
$env:LLM_MODEL_ANALYTICS_GENERAL = "cerebras/gpt-oss-120b"
$env:LLM_MODEL_SQL_REASONER = "cerebras/gpt-oss-120b"
$env:CEREBRAS_API_KEY = "<secret>"
$env:RUN_CLOUD_LLM_TESTS = "1"
$env:LLM_MAX_RETRIES = "2"
$env:EVALUATION_REQUEST_DELAY_SECONDS = "2"
.\.venv\Scripts\pytest --run-cloud `
  tests\integration\test_cerebras_live.py::test_live_cerebras_structured_sql -vv
```

Only after the single smoke test passes, run the unchanged SQL-only benchmark sequentially:

```powershell
.\.venv\Scripts\enterprise-data-eval --backend duckdb --llm configured --mode sql `
  --request-delay-seconds 2 `
  --reference-report artifacts\baseline-qwen35-9b.json `
  --secondary-reference-report artifacts\baseline-groq-qwen36-27b-final.json `
  --output artifacts\baseline-cerebras-gpt-oss-120b.json `
  --markdown-output docs\evaluation-cerebras-gpt-oss-120b.md
```

## Optional Local Ollama LLM

The local Qwen path uses the same `LLMGateway` and LiteLLM adapter as cloud providers. The
development baseline uses installed tag `qwen3.5:9b`, addressed through LiteLLM 1.98.0's
structured chat adapter as `ollama_chat/qwen3.5:9b`. The larger installed
`ollama_chat/qwen3.6:27b` route remains configurable. Local calls have their own explicit opt-in
and require no API key:

```powershell
$env:LLM_PROVIDER = "litellm"
$env:LLM_MODEL_ANALYTICS_GENERAL = "ollama_chat/qwen3.5:9b"
$env:LLM_MODEL_SQL_REASONER = "ollama_chat/qwen3.5:9b"
$env:OLLAMA_API_BASE = "http://localhost:11434"
$env:OLLAMA_NUM_CTX = "8192"
$env:RUN_LOCAL_LLM_TESTS = "1"
$env:LLM_TIMEOUT_SECONDS = "300"
$env:LLM_MAX_OUTPUT_TOKENS = "2048"
$env:LLM_REASONING_EFFORT = "none"
.\.venv\Scripts\pytest --run-local-llm -m local_llm `
  tests\integration\test_ollama_live.py::test_local_qwen_generates_safe_structured_sql
```

The test skips cleanly if Ollama or the configured tag is unavailable. Normal `pytest` never
calls Ollama.

## Ask the first analytics question

```powershell
curl -X POST http://127.0.0.1:8000/analytics/query `
  -H "Content-Type: application/json" `
  -d "{\"question\":\"Show each department, its number of employees, total salary, average salary, and highest paid employee, ordered by total payroll.\",\"model_profile\":\"qwen\"}"
```

The response contract is version `1.1` and returns `request_id`, `thread_id`, the safe model
profile/display name, `answer`, `columns`,
`rows`, a safe chart spec, public provenance, clarification fields, warnings, and execution
metadata. Send the returned `thread_id` with a follow-up question to continue the analytical
thread. Internal provenance retains query IDs, filters, time ranges, model aliases, and SQL, while
the default public response omits SQL and internal correlation details.

## Run checks

```powershell
.\.venv\Scripts\pytest
.\.venv\Scripts\ruff check .
.\.venv\Scripts\mypy .
```

## Run Evaluations

```powershell
.\.venv\Scripts\enterprise-data-eval --backend fake --llm deterministic
.\.venv\Scripts\enterprise-data-eval --backend duckdb --llm deterministic
```

Backend selection is explicit; the CLI never falls back to another database. `fake` measures
workflow behavior using deterministic fixtures and reports SQL execution/result accuracy as not
applicable. `duckdb` is an embedded evaluation-only adapter that executes the PostgreSQL-oriented
reference SQL without changing production SQL. `postgres` uses the production-facing adapter and
requires a reachable `DATABASE_URL`.

Write a JSON report with `--output artifacts\evaluation-report.json`. The 50-case suite covers
lookups, aggregations, joins, CTEs/subqueries, window functions, temporal and comparative analysis,
follow-ups, ambiguity, adversarial requests, English, Arabic, and mixed language.

Run the same cases against a configured LiteLLM cloud model with:

```powershell
$env:LLM_PROVIDER = "litellm"
$env:LLM_MODEL_ANALYTICS_GENERAL = "gemini/gemini-2.5-flash"
$env:LLM_MODEL_SQL_REASONER = "gemini/gemini-2.5-flash"
$env:GEMINI_API_KEY = "<secret>"
$env:RUN_CLOUD_LLM_TESTS = "1"
.\.venv\Scripts\enterprise-data-eval --backend duckdb --llm configured `
  --output artifacts\baseline-cloud.json `
  --comparison-output docs\evaluation-cloud-baseline.md
```

This command fails before any case runs unless LiteLLM, both non-fake logical `LLM_MODEL_*`
aliases, their matching provider credentials, and `RUN_CLOUD_LLM_TESTS=1` are explicitly
configured. It never falls back to deterministic SQL. No cloud credential is required for
deterministic development.

Run the SQL-focused local Qwen baseline only after the single smoke test passes:

```powershell
.\.venv\Scripts\enterprise-data-eval --backend duckdb --llm configured --mode sql `
  --output artifacts\baseline-qwen35-9b.json `
  --markdown-output docs\evaluation-qwen35-9b-baseline.md
```

SQL mode makes one `sql-reasoner` call per case and does not call `analytics-general` for prose.
It still retrieves schema context, validates generated SQL with SQLGlot, executes against DuckDB,
and compares results with the unchanged evaluation expectations. Infrastructure failures are
reported separately and excluded from scored model accuracy.
