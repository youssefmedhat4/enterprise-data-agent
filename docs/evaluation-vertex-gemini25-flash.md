# Vertex AI Gemini 2.5 Flash Baseline

This document labels the preserved baseline artifact correctly. The run used
Vertex AI through LiteLLM with `vertex_ai/gemini-2.5-flash`, the DuckDB
evaluation backend, SQL-only mode, and the frozen 50-case dataset.

The original JSON remains unchanged at
`artifacts/baseline-vertex-gemini25-flash.json`. Its metrics reflect the v1
evaluator and must not be interpreted as v2 semantic-comparison results.

## Preserved Results

| Metric | Result |
|---|---:|
| Scored case pass rate | 20.8% (10/48) |
| Structured output | 100.0% (48/48 scored cases) |
| SQL parse validity | 100.0% (42/42) |
| Relevant-table accuracy | 95.2% (40/42) |
| SQL safety validation | 97.7% (42/43) |
| SQL execution success | 91.1% (41/45) |
| SQL result accuracy | 17.8% (8/45) |
| Clarification behavior | 100.0% (2/2) |
| Recorded adversarial outcomes | 100.0% (1/1) |

## Run Identity

- Provider: Vertex AI
- Physical model: `gemini-2.5-flash`
- LiteLLM identifier: `vertex_ai/gemini-2.5-flash`
- Model calls: 50
- Total tokens: 45,437
- Average model latency: 3,132.556 ms
- Median model latency: 2,613.581 ms
- p95 model latency: 7,982.993 ms
- Infrastructure failures: 2
- Dataset SHA-256: `94A6F36EEE2AE5A7D9CA5A8360888762F745862F0628025BA2E003E2A8E65F55`
- Preserved artifact SHA-256: `A419736E9B70AB9B8DBC1178D87AF7CDCB863AB07E971AFA7BFEEC01649B5F95`

## Interpretation

The root-cause audit found that many v1 `result_accuracy=false` outcomes were
evaluator false negatives, missing schema/value context, missing business
semantics, or missing action/thread contracts. The preserved percentages above
are historical measurements. A future paid rerun must write a new v2 artifact
and comparison report rather than overwrite this baseline.
