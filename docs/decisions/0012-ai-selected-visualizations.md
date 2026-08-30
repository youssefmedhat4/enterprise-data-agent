# ADR 0012: AI-Selected, Schema-Validated Visualizations

Date: 2026-08-25

## Status

Accepted.

## Context

The original `ChartSpec` supported four types (`bar`, `line`, `pie`, `donut`) with a
single `x`, a single `y`, and an optional `series`. Two problems followed from that:

1. **Visually repetitive answers.** A ranking, a monthly trend, and a two-measure
   comparison all rendered as the same vertical bar chart, because the contract could
   not express orientation, stacking, multiple measures, or a relationship between two
   numeric columns.
2. **The model was never actually asked to choose.** The answer-generation system prompt
   said nothing about charts, and the user prompt sent only the question and the rows —
   no column types and no row count. The model was expected to fill in a chart it had
   received neither guidance nor type information to reason about.

The second point matters more than the first. Widening the contract without also giving
the planner guidance and metadata would have produced a richer contract that was still
filled in blindly.

## Decision

The `analytics-general` model selects the visualization as part of the existing grounded
answer call, and the backend independently validates that selection against the rows that
were actually returned.

No second LLM call is introduced. `AnswerGeneration` already returns `answer`, `claims`,
and `chart` together, and the visualization decision depends on exactly the data the
answer call already has. Splitting it would double model latency and cost to move a
decision across a boundary it does not need to cross.

### Division of responsibility

```text
analytics-general  ->  chooses the visualization semantically
ChartSpec          ->  bounds what can even be expressed
ChartValidator     ->  verifies the choice against real rows
frontend renderer  ->  draws known primitives only
```

The model chooses; code constrains. There is deliberately no deterministic
"if two columns then bar" router — that would defeat the purpose of the change.

### The contract is data, never code

`ChartSpec` contains only enums, bounded strings, and column names. There is no field in
which the model could return JavaScript, JSX, Recharts configuration, a Vega expression,
or HTML, and `extra="forbid"` rejects an attempt to invent one. This is enforced by test.

### Two layers of validation

Structural rules that hold regardless of the data live in the Pydantic model, so an
incoherent spec never leaves the model boundary: measures must be unique, `x` cannot also
be a measure, `series` and multiple `measures` are mutually exclusive, part-to-whole
charts take exactly one measure and no series.

Data-dependent rules live in `ChartValidator`, which is the authority. **A column name is
never trusted because the model returned it** — every referenced field is checked against
the actual result, measures must be numeric, scatter additionally requires a numeric `x`,
and part-to-whole charts reject negative values and high-cardinality results.

### Invalid charts degrade, they do not fail

A rejected chart returns `chart = null` plus a sanitized warning. The grounded answer,
the claims, and the table are unaffected. A visualization is an aid to an answer, not the
answer, so a bad chart must never cost the user a good analysis.

`ChartValidator` further distinguishes two kinds of problem. A *data-integrity* problem
(missing column, non-numeric measure, negative slice) drops the chart. A *cosmetic*
mismatch (horizontal orientation on a pie, stacking with a single series) is normalised
and the chart is kept, because discarding a usable chart over an inapplicable field would
be a worse outcome than silently correcting it.

## Consequences

- `AnalyticsResponse.schema_version` moves from `1.0` to `1.1`. The `chart` field replaces
  `y: str` with `measures: list[str]` and adds orientation, mode, labels, value format,
  sort, and limit. This is a breaking change to that field, taken deliberately rather than
  carrying a redundant `y`-plus-`measures` pair forever. The frontend is the only consumer
  and is updated in the same change.
- The answer user prompt now includes result column types and row count. This is bounded
  metadata about data the model already received in full; it introduces no new
  authorization path and stays downstream of authentication, OPA, and execution.
- Sorting and limiting are display-only. They reorder or truncate rows that already exist
  and never alter a value; truncation continues to be disclosed in the UI.

## Alternatives considered

- **A deterministic chart router.** Rejected: rules like "two columns implies bar" are
  what produced the repetitive output, and they cannot weigh the user's actual question.
- **A second LLM call dedicated to visualization.** Rejected: it doubles latency and cost
  for a decision that needs exactly the context the answer call already holds.
- **Keeping `y` alongside `measures` for compatibility.** Rejected: two representations of
  the same concept invites drift, and the only consumer lives in this repository.
- **Histogram support.** Rejected for now. A histogram requires computing bin counts that
  do not exist in `AnalyticalResult`, which would mean the chart displaying values the
  grounding boundary never verified. A distribution can be requested as pre-binned counts
  in SQL and rendered as a bar chart, which keeps every plotted value traceable to a row.

## Risks

- The model can still choose a *poor but valid* chart — a line chart over unordered
  categories, for example. Validation covers correctness and safety, not taste. Ordering
  semantics are not machine-checkable from column types alone.
- `value_format` is a display hint from the model, not a verified property of the data. A
  measure labelled `currency` is not proof of a currency column.
- Governed-metric results report every column type as `unknown`, so for that route the
  planner infers type from values rather than declared types.
