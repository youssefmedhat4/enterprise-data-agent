# Evaluation Baseline

The baseline suite contains 50 synthetic enterprise analytics cases in `evals/cases.json`.
`scripts/build_eval_dataset.py` is the maintainable source used to regenerate that JSON file.

## Backend Semantics

- `fake` uses deterministic fixture rows to test graph flow, structured output, grounding,
  provenance, clarification, and blocking. SQL execution success and result accuracy are `null`.
- `duckdb` executes validated SQL against an in-memory synthetic enterprise schema. It is an
  evaluation dependency only and is not part of the production architecture.
- `postgres` uses the production-facing `PostgresDatabaseGateway`. It requires an explicitly
  reachable PostgreSQL service and never falls back to DuckDB or fake data.

Reference SQL is parsed and safety-validated as PostgreSQL before execution. The harness does not
rewrite PostgreSQL SQL to make DuckDB pass. A dialect incompatibility is reported as an execution
failure and remains visible in the case result.

## Metric Semantics

Reports separate workflow, SQL, answer, security, and performance metrics. A metric that cannot be
measured for a backend is `null` at case level and has `applicable: 0` in aggregate output.

Security cases pass only when SQL validation blocks the request before database execution.
Ambiguous cases pass only when the graph returns a clarification with no SQL execution. Numeric
grounding compares numbers in the answer with values in executed or fixture result rows.

Accuracy is also grouped by category, difficulty, and language. Deterministic retries are reported
as zero; cloud retry count remains `null` because LiteLLM does not expose the actual internal retry
count through the current adapter.
