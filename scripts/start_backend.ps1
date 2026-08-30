$ErrorActionPreference = "Stop"

$compose = "infra/compose/docker-compose.yml"
if (-not (Test-Path -LiteralPath ".env")) {
    throw "Create .env from .env.example and configure local passwords and Vertex settings first."
}

docker compose --env-file .env -f $compose --profile wren --profile opa up -d `
    --wait postgres checkpoint-postgres wren opa

$env:DATABASE_PROVIDER = "postgres"
$env:CONVERSATION_CHECKPOINT_PROVIDER = "postgres"
$env:AUTHORIZATION_PROVIDER = "opa"
$env:METRIC_PROVIDER = "wren"
$env:LLM_PROVIDER = "litellm"

Write-Host "Infrastructure is healthy. Starting API at http://localhost:8000"
& .\.venv\Scripts\enterprise-data-api.exe
