# Local Qwen Text-to-SQL Baseline

## Status

The 50-case benchmark was not run. The required single real-model smoke test reached Ollama
through `LLMGateway -> LiteLLM -> Ollama`, but `qwen3.6:27b` could not load because the local
runtime ran out of memory. No benchmark result or language-to-SQL accuracy is fabricated here.

## Run Identity

- Dataset cases: 50, unchanged
- Dataset SHA-256: `94a6f36eee2ae5a7d9ca5a8360888762f745862f0628025ba2e003e2a8e65f55`
- Database backend: `duckdb`
- Evaluation mode: `sql`
- Ollama version: `0.32.15`
- Installed model tag: `qwen3.6:27b`
- LiteLLM model identifier: `ollama_chat/qwen3.6:27b` (LiteLLM 1.98.0 structured chat route)
- Model: 27.8B parameters, Q4_K_M quantization

## Infrastructure Outcome

Ollama attempted to start the model and reported an out-of-memory failure while allocating a
host buffer. The machine has an NVIDIA GeForce RTX 4060 Laptop GPU with 8,188 MiB reported VRAM.
`ollama ps` was empty after the failure, so GPU acceleration was not active for a loaded model.

This is an infrastructure failure, not a model-quality or SQL-accuracy failure. The full suite
was therefore not started, as required by the smoke-test gate.

## Reproduction

```powershell
$env:LLM_PROVIDER = "litellm"
$env:LLM_MODEL_ANALYTICS_GENERAL = "ollama_chat/qwen3.6:27b"
$env:LLM_MODEL_SQL_REASONER = "ollama_chat/qwen3.6:27b"
$env:OLLAMA_API_BASE = "http://localhost:11434"
$env:RUN_LOCAL_LLM_TESTS = "1"
$env:RUN_CLOUD_LLM_TESTS = "0"
$env:LLM_TIMEOUT_SECONDS = "300"
$env:LLM_MAX_RETRIES = "0"
$env:LLM_MAX_OUTPUT_TOKENS = "2048"
$env:LLM_REASONING_EFFORT = "none"
.\.venv\Scripts\pytest --run-local-llm -m local_llm `
  tests\integration\test_ollama_live.py::test_local_qwen_generates_safe_structured_sql -vv
```

After that smoke test passes, run:

```powershell
.\.venv\Scripts\enterprise-data-eval --backend duckdb --llm configured --mode sql `
  --output artifacts\baseline-qwen.json `
  --markdown-output docs\evaluation-qwen-baseline.md
```
