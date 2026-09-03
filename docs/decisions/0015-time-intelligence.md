# Time intelligence

## Status

Accepted.

## Context

People ask analytical questions in business time: "revenue year to date",
"invoiced last month", "fiscal YTD versus last year". Before this, those phrases
reached the model as prose and it produced date literals of its own.

That is wrong in a way that is hard to notice. "Fiscal year to date" is not a
fact about language — it depends on when a company's year starts, which
timezone its days begin in, and whether a July-to-July year is called FY2026 or
FY2027. A model asked for those boundaries produces plausible ones, and a
plausible boundary silently includes or excludes a day of business. The number
still looks like a number.

There is a second failure of the same shape: a model can write valid SQL that
omits the date filter entirely. The result covers all of history, passes every
guardrail, and reads as a right answer.

## Decision

**A datasource owns its calendar.** Timezone, week start, fiscal year start and
the fiscal labelling convention are stored per datasource. A calendar nobody has
confirmed is marked `DEFAULT`: calendar periods still resolve under it, because
a calendar year is January to December wherever you are, but a fiscal question is
declined rather than answered from an assumed January start.

**Temporal meaning is reviewed, not inferred.** A database has many dates. A
temporal dimension attaches a role (`EVENT_TIME`, `EFFECTIVE_START`, …) and a
storage strategy to a *confirmed semantic attribute*. Where several dates could
answer a question and no default was set, the system asks which one rather than
picking the column whose name contains "date".

**Legacy date storage is declared, never parsed by a model.** `YYYYMMDD_TEXT`
maps to one known-correct conversion emitted by trusted code, guarded so a
single malformed row cannot take the whole answer with it. The allowlist has no
member meaning "whatever the model suggests".

**Boundaries are computed, and every interval is half-open.** `[start, end)`.
Ending a day at 23:59:59 loses the last second, behaves differently for dates
and timestamps, and changes meaning when a column gains precision. Half-open
ranges tile: yesterday ends exactly where today begins, and a comparison period
is the same shape as the period it compares to.

**Local time decides the calendar; UTC carries the instants.** A day begins when
the business says it begins, so boundaries are computed on local dates in the
datasource's zone and then converted.

**One clock, injected.** Production reads real UTC time; tests and evaluation
runs pin an anchor. Without this, "year to date" is untestable and an evaluation
case about a relative period means something different every month.

**No extra model call on the normal path.** About thirty phrase shapes cover
what people actually ask, and they are recognised deterministically. Failing to
recognise a phrase is safe — the question is answered exactly as it was before
this layer existed. Recognising the wrong one is not, which is why patterns are
anchored and a bare "quarter" matches nothing.

**Metrics declare how they behave across time.** A `FLOW` accumulates, so a
year-to-date total is meaningful. A `SNAPSHOT` describes a moment, and summing
daily headcounts across a year is arithmetic on a category error; the system
declines rather than inventing history.

**A resolved period cannot silently disappear.** When a period was resolved, the
generated statement must constrain the column it was resolved against — checked
on the parsed tree, because a substring search finds the column in a `SELECT`
list and reports success for a query that filters nothing. A statement that lost
the period gets the one bounded repair a schema mistake already gets, then an
honest failure.

**Comparisons are the equivalent elapsed stretch.** Year on year compares eight
months against the same eight months, not against a full twelve. That
asymmetry is the most common way a flat year is made to look like a collapse.

## Consequences

A question with a time phrase costs exactly what it cost before: no additional
model call, one deterministic resolution.

A datasource with no temporal mappings has not opted in, and answers time
questions exactly as it did previously. Refusing there would break every
time-flavoured question on a database nobody has reviewed yet.

Three honest outcomes replace one dishonest one. Instead of answering over all
of history, the system resolves, asks which date is meant, or says the period
cannot be applied.

Cluster fingerprints carry the temporal *concept* rather than the dates, so
"revenue year to date" asked in two different months remains one recurring
pattern while staying distinct from "revenue last month".

The answer trace shows the calendar, the window, the comparison window and the
business name of the column the period was applied to — enough for a reader to
check the answer rather than trust that the phrase was understood.

## Alternatives considered

**Let the model resolve dates and validate the output.** Rejected: validating a
date range requires knowing the right one, which means computing it anyway.

**Store a single default date column per datasource.** Rejected: it answers
"projects last year" confidently and often about the wrong column, which is the
failure this design exists to prevent.

**Ask the model to classify time intent on every question.** Rejected as the
normal path: a network round trip and a source of variance on a decision with
one right answer. The existing planner still sees every question and may
classify what the parser does not recognise.
