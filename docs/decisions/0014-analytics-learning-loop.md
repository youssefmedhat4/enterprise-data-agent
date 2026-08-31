# ADR 0014: Per-Datasource Analytics Learning Loop

Date: 2026-08-31

## Status

Accepted. Extends ADR 0011 (Wren governed execution) and the datasource-scoped
semantic registry, and completes the dynamic architecture those started.

## Context

Governed routing became semantic rather than alias-driven, and metric
definitions moved into a datasource-scoped registry. What remained was the
learning half: the system could answer a question well, but it could not notice
that the same question kept being asked, and it had no way to turn that
observation into reusable knowledge that a human had agreed to.

The risk in building that is not technical difficulty. It is that a learning
system quietly becomes an authority nobody approved — inventing metrics,
caching answers, or letting one database's knowledge bleed into another.

## Decision

A bounded, human-gated loop:

```text
question -> terminal result
   -> QuestionEvent (what was asked, what shape answered it)
      -> cluster (same datasource, same analytical structure)
         -> [threshold] one generation call -> PROPOSED candidate
            -> human review
               |- reject -> remembered, suppresses regeneration
               `- approve -> validation -> CERTIFIED / APPROVED -> reindex
```

Nothing in that chain runs on a live analytics request except recording the
event. Candidate generation is an admin workflow, so an ordinary question still
costs the same model calls it did before.

### Memory records shape, never answers

`QuestionEvent` has no field for rows, measures, or answer text, and a test
asserts that against the contract rather than against behaviour. This is the
boundary that stops memory becoming an answer cache. A question asked today is
always executed against the live database; memory can suggest *how to interpret*
a question, never *what the number is*.

Question text is controlled product data, retained only behind
`QUESTION_MEMORY_ENABLED`, which defaults off. Ordinary logs stay content-free.

### Fingerprints are deterministic and free of literals

Members are sorted and case-folded, so the same plan yields the same string on
every process and run. A filter contributes its dimension and operator and never
its operand: `customer = 'ACME Secret Account'` becomes `filter:customer:eq`.
Fingerprints can therefore be stored, indexed, and shown to a reviewer without
carrying customer names or salary figures out of the database. Ad-hoc structure
is read from parsed SQL rather than its text, and a parse failure degrades to a
marker instead of raising — failing to remember must not fail a request that
already succeeded.

### Clustering is structural first

Events join a cluster when fingerprints match exactly: the same metrics at the
same grain. Embedding similarity is recorded and used for summarisation, but it
never overrides structure. Questions phrased alike but analysed at different
grains stay separate, because a cluster mixing grains cannot produce one correct
reusable definition. A cluster is addressed by
`(data_source_id, structural_fingerprint)`, so identical wording asked of two
databases cannot reach the same cluster.

### Derived metrics are arithmetic, not code

A METRIC candidate carries a typed expression tree over already-certified metric
keys. There is no node able to hold SQL, a function, a column, a table, or
Python, so a model proposing a metric cannot smuggle execution into a
definition. Division by zero yields null: a per-employee figure for a department
with no employees is undefined, zero would be a false statement, and raising
would fail a whole result over one empty group.

### Approval validates; it does not flip a status

`approve_metric` re-validates against the registry *as it is at approval time*,
because a dependency may have been deprecated since the proposal. It refuses
unknown dependencies, self-reference, cycles, excessive nesting, and any
dimension an input metric does not itself offer. That last rule found a real
constraint in the demo catalog: revenue per employee cannot be grouped by
department, because `invoice_amount` is dimensioned by customer and project but
not department. The metric is refused rather than certified with a grain
execution would have to guess.

Rejection is remembered and suppresses regeneration from the same evidence.
Without that, the next event would re-propose exactly what a human just
declined, and review would mean nothing.

### Approved examples are context, never shortcuts

A stored example reaches the model as reference material. The model still writes
SQL for the current question, and that SQL still passes SQLGlot, current schema
validation, current authorization, and the read-only role. Storing a query does
not make it trusted to run: schemas change and permissions differ per caller, so
an example asserts "this shape worked once", not "run this".

Examples whose tables are not all inside the caller's authorized schema are
withheld. Offering one would reveal that a table exists, turning approved
knowledge into an authorization side channel.

### Business instructions are retrieved only when relevant

An instruction reaches a prompt when the plan uses a metric it governs or the
question mentions one of its concepts. Appending every instruction to every
request would bury the relevant one and spend context on guidance the question
never touches.

### Staleness is dependency-aware

A schema change marks only knowledge bound to what changed. Semantic mappings
whose table or column disappeared become STALE; neighbours stay CONFIRMED.
Examples go stale only if they recorded a fingerprint of their own. Business
definitions usually outlive a schema change and are left alone. Nothing is
deleted: a reviewer has to be able to see what broke and re-map it. Marking
everything stale on any change would destroy review work and train reviewers to
re-approve without reading.

### Isolation is structural

Every learning table carries `data_source_id`, every lookup is parameterised by
it, and composite foreign keys stop a child row pointing at a parent in another
datasource. Conversation threads are scoped by datasource too, so a thread
started against one database cannot supply prior context to another, and
switching datasource in the client starts a fresh analysis rather than a hidden
continuation.

### Review authority is a separate capability

`knowledge_review` is distinct from analytics access. Being allowed to read data
is not authority over what the data is defined to mean, so an ordinary analyst
can query all day and still gets 403 from every administration route.

## Consequences

- Recording adds one write per terminal request and no model call. Candidate
  generation costs one call per eligible cluster, in an admin workflow.
- Vectors are stored with the provider, model, and dimension that produced them.
  Vectors from different models are not comparable, so recording the producer
  lets incompatible rows be found and reindexed rather than silently corrupting
  similarity.
- A reviewer is now a required participant. Knowledge accumulates only as fast
  as someone approves it, which is the intended trade.

## Risks

- Structural equality for clustering is strict. Genuinely equivalent questions
  that differ in grain will form separate clusters and may each fall below the
  proposal threshold. Loosening this is tempting and would be wrong: the
  alternative is a cluster whose members disagree about what was asked.
- The registry falls back to in-memory when no connection pool is supplied. That
  is correct and seeded, but it is not shared across processes; wiring the pool
  through the API is outstanding.
