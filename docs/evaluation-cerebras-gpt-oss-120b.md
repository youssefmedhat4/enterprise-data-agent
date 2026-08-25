# Cerebras GPT-OSS 120B Cloud Baseline

## Status

The baseline is pending. No 50-case accuracy, latency, or token metrics are reported.

LiteLLM 1.98.0 confirms the physical identifier `cerebras/gpt-oss-120b` and advertises
JSON-schema response support. Both application aliases route to this identifier through the
existing `LLMGateway` and LiteLLM adapter.

The required single smoke request reached Cerebras but did not return a model response:

- Initial response: HTTP 402 `payment_required` for model access.
- Sanitized retry: `authentication_failed` for the configured credential.

The unchanged 50-case benchmark was not started because the smoke test did not pass. No
deterministic or reference SQL result is represented as a Cerebras model result, and
`artifacts/baseline-cerebras-gpt-oss-120b.json` has intentionally not been created.

## Reproduction

After enabling the Cerebras account/model and configuring a valid local credential:

```powershell
.\.venv\Scripts\pytest --run-cloud `
  tests\integration\test_cerebras_live.py::test_live_cerebras_structured_sql -vv
```

Only after that passes:

```powershell
.\.venv\Scripts\enterprise-data-eval --backend duckdb --llm configured --mode sql `
  --request-delay-seconds 2 `
  --reference-report artifacts\baseline-qwen35-9b.json `
  --secondary-reference-report artifacts\baseline-groq-qwen36-27b-final.json `
  --output artifacts\baseline-cerebras-gpt-oss-120b.json `
  --markdown-output docs\evaluation-cerebras-gpt-oss-120b.md
```
