# Enterprise Data Agent

Greenfield enterprise conversational analytics platform.

This first vertical slice includes:

- FastAPI app with health and analytics endpoints.
- Environment-based configuration.
- Replaceable LLM gateway with LiteLLM and deterministic fake adapters.
- Typed LangGraph workflow.
- Replaceable database gateway with a direct PostgreSQL adapter.
- Dockerized synthetic PostgreSQL analytics database.
- SQLGlot read-only SQL validation.
- Automated unit, security, and vertical-slice tests.

## Local Development

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

The default configuration uses `DATABASE_PROVIDER=fake` and `LLM_PROVIDER=fake`. The API and
evaluation suite therefore require no database service, container runtime, or cloud credential.

```powershell
.\.venv\Scripts\uvicorn app.main:app --reload
```

## Optional PostgreSQL

PostgreSQL is not required for local development. On an environment with PostgreSQL and Docker
Compose available, set `DATABASE_PROVIDER=postgres` plus unique local values for
`POSTGRES_ADMIN_PASSWORD` and `EDA_READONLY_PASSWORD` in `.env`, then run:

```powershell
docker compose --env-file .env -f infra/compose/docker-compose.yml up -d
```

The optional compose service initializes a synthetic `enterprise_analytics` database and the
`eda_readonly` login. Initialization grants that login only `CONNECT`, schema `USAGE`, and
table `SELECT`, and sets `default_transaction_read_only=on`.

The PostgreSQL integration test skips when the service is unavailable and runs automatically when
`DATABASE_URL` is reachable.

## Optional Cloud LLM

Set `LLM_PROVIDER=litellm`, configure `LLM_MODEL_ANALYTICS_GENERAL` and
`LLM_MODEL_SQL_REASONER` with LiteLLM model identifiers, and provide the selected provider's
standard credential environment variable. Cloud use must be limited to synthetic or explicitly
approved non-sensitive data.

The paid live smoke test requires an additional explicit opt-in:

```powershell
$env:RUN_CLOUD_LLM_TESTS = "1"
.\.venv\Scripts\pytest -m cloud tests\integration\test_litellm_live.py
```

## Ask the first analytics question

```powershell
curl -X POST http://127.0.0.1:8000/analytics/query `
  -H "Content-Type: application/json" `
  -d "{\"question\":\"Show each department, its number of employees, total salary, average salary, and highest paid employee, ordered by total payroll.\"}"
```

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
.\.venv\Scripts\enterprise-data-eval --backend duckdb --llm configured
```

This requires `LLM_PROVIDER=litellm`, both logical `LLM_MODEL_*` aliases, and the provider's API
credential. No cloud credential is required for deterministic development.
