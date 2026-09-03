# Wren Semantic Layer Development Guide

## Architecture

```text
PostgreSQL catalog discovery
        |
        v
physical TableMetadata -------------------------+
        |                                       |
        | reconcile physical table references  |
        v                                       v
Wren MDL v5 -> Wren MCP context service -> WrenSemanticGateway
                                                   |
                                                   v
                                        bounded SemanticContext
                                                   |
                                                   v
LangGraph -> LLMGateway/sql-reasoner -> SQLGlot -> DatabaseGateway -> PostgreSQL
```

Wren has no PostgreSQL profile in this deployment and runs with `--no-connect`. It cannot
execute SQL or replace database authorization. The application intersects Wren model table
references with the physical metadata discovered by `DatabaseGateway` before context reaches
the model prompt.

## Components

- Wren `0.13.2`, pinned in `infra/wren/Dockerfile`.
- MDL v5 source under `semantic/wren/`.
- One local-only MCP HTTP service at `http://localhost:8080/mcp`.
- Application MCP client through `WrenSemanticGateway`.
- In-memory semantic provider remains the default and is never used as a Wren fallback.

The legacy Wren GenBI application stack, database profiles, semantic memory, Wren Cubes,
query execution, and native/agent Text-to-SQL are not enabled.

## Start Wren

```powershell
docker compose --env-file .env -f infra/compose/docker-compose.yml --profile wren up -d --build wren
docker compose --env-file .env -f infra/compose/docker-compose.yml ps wren
```

The container validates and compiles the MDL before starting the context service. Generated
`semantic/wren/target/` and `.wren/` state are ignored by Git.

## Select the Provider

```dotenv
SEMANTIC_PROVIDER=wren
WREN_MCP_URL=http://localhost:8080/mcp
WREN_TIMEOUT_SECONDS=10
WREN_MAX_CONTEXT_MODELS=6
WREN_PROJECT_ID=enterprise_analytics
SQL_GENERATION_PROVIDER=llm
```

`SEMANTIC_PROVIDER=wren` fails with `semantic_provider_unavailable` if the service cannot be
reached. There is no silent fallback.

## Verify

```powershell
.\.venv\Scripts\python.exe -m pytest -m wren --run-wren -v
```

These integration tests verify bounded context retrieval, physical-schema reconciliation, and
clean failure when the explicitly selected Wren service is unavailable.
