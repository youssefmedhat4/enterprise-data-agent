# ADR 0005: Schema-Aware SQL Validation and Controlled Repair

Date: 2026-08-24

## Status

Accepted by implementation, pending architecture review.

## Context

The ad-hoc analytics path already parsed generated SQL with SQLGlot, rejected mutations and
multiple statements, enforced the `analytics` schema, bounded result rows, and executed with a
physically read-only PostgreSQL role. That did not catch syntactically valid hallucinations such
as nonexistent columns, unresolved aliases, invalid CTE outputs, or unknown functions before a
database round trip.

The validator also needs to operate on the schema visible to the current request. A future OPA
authorization step may remove tables or columns from that view even when they exist physically.
Validation must therefore use supplied metadata, not independently discover the database.

## Decision

Extend `SQLValidator` into a full, typed pipeline:

1. Parse exactly one PostgreSQL statement with SQLGlot.
2. enforce the existing read-only and command safety rules;
3. check physical relations against a bounded `list[TableMetadata]` snapshot;
4. validate functions and system-catalog access;
5. resolve table aliases, columns, CTEs, derived tables, and query scopes;
6. apply the fixed row limit; and
7. return `SQLValidationResult` without exposing SQLGlot objects.

Only a small set of schema-generation mistakes can enter one explicit repair node. The repaired
candidate starts the complete validation pipeline again and can never be repaired a second time.
The governed Cube path remains independent and does not use this model repair mechanism.

## AST and Scoping Approach

Statement type, nested prohibited nodes, tables, columns, functions, CTEs, and stars are inspected
from the SQLGlot AST. SQLGlot's scope traversal distinguishes physical tables from CTE and derived
sources. Its `qualify` optimizer receives the bounded schema map and validates qualified columns,
unqualified ambiguity, projection aliases, grouping, ordering, HAVING expressions, subqueries,
and window expressions. Qualification runs on an AST copy; parser internals do not escape the
security module.

## Allowed-Schema Contract

`validate(sql, allowed_schema=...)` receives the bounded `TableMetadata` view returned through
the `DatabaseGateway`. This is distinct from the smaller semantic subset selected for prompt
context: retrieval relevance is not an authorization decision. The allowed snapshot contains only
permitted schemas, tables, columns, and types. A physical table or column omitted from this
snapshot is unavailable to the query even if it exists in PostgreSQL. The validator performs no
catalog lookup of its own.

This contract is the insertion point for future OPA filtering: authorization can reduce the
DatabaseGateway snapshot before semantic selection, SQL generation, and validation without
changing the validator or LangGraph nodes.

## Table Validation

Physical tables must be schema-qualified and present in the supplied snapshot. Aliases are
recorded and resolved through SQLGlot scopes. CTE names and derived-table aliases are not treated
as physical relations. Unknown relations, disallowed schemas, and references outside the selected
scope fail before execution.

## Column Validation

Columns are resolved against the allowed source columns in each query scope. The validator
supports qualified and unqualified references, joins, GROUP BY, HAVING, ORDER BY projection
aliases, and window expressions. It rejects unknown columns, aliases that cannot be resolved, and
unqualified columns that PostgreSQL would consider ambiguous.

## CTE and Subquery Handling

CTE and derived-table outputs are derived from their projected columns by SQLGlot scope analysis.
Nested sources may reference those outputs, but a CTE name is never looked up in physical
metadata. Invalid CTE names and nonexistent projected output columns are typed validation errors.

## Star Handling

Projection stars (`SELECT *` and `SELECT e.*`) are rejected. `COUNT(*)` remains valid because it
does not expose columns. Rejection is deliberate: leaving expansion to PostgreSQL could reveal a
physical column omitted by a future authorization-filtered snapshot, while silently rewriting a
model query would change its result contract. A later policy-controlled expansion feature may
expand only snapshot-approved columns before validation, but unrestricted physical expansion is
never permitted.

## Function Policy

The validator uses a conservative allowlist for ordinary analytical scalar, aggregate, date, and
window functions currently required by the application. This includes COUNT, SUM, AVG, MIN, MAX,
COALESCE, LOWER, UPPER, ROUND, DATE_TRUNC, EXTRACT, ROW_NUMBER, RANK, DENSE_RANK, LAG, and LEAD.
Unknown functions fail closed. A separate denylist documents dangerous PostgreSQL functions that
can read files, manipulate sequences, inspect or terminate server work, or invoke extensions.
Function additions require an intentional code and test change rather than relying on PostgreSQL
to resolve arbitrary calls.

## System Catalog Policy

Normal analytical SQL cannot read `pg_catalog`, `information_schema`, `pg_*` relations, or common
PostgreSQL role/catalog views. Application-controlled metadata discovery remains a separate
`DatabaseGateway` operation and does not use model-generated SQL.

## Repairable Errors

One repair may be attempted for likely generation mistakes:

- `unknown_table`
- `unknown_column`
- `ambiguous_column`
- `unresolved_alias`
- `invalid_cte_reference`
- `forbidden_schema`

The repair call uses the existing `LLMGateway` logical alias `sql-reasoner`. Its structured
`SQLRepair` input is limited to the question, original candidate, structured validation result,
selected schema, selected semantic context, and structured conversation context. It receives no
result rows, hidden benchmark answers, full enterprise schema, or chain-of-thought request.

## Non-Repairable Errors

Parsing failures, multiple statements, mutations and commands, system-catalog access, prohibited
or unknown functions, restricted stars, and invalid LIMIT expressions fail immediately. Security
violations are never transformed into a different query.

## One-Repair Rule

LangGraph has explicit `validate_sql` and `repair_sql` nodes. A repairable first failure follows
one edge to `repair_sql`, then returns to the same full validator. Success executes only the
repaired SQL. A second validation failure raises `SQLRepairFailedError`; there is no loop back to
repair and the `DatabaseGateway` is not called. Non-repairable errors raise immediately.

## Provenance

Internal provenance records validation attempt count, whether repair was attempted and succeeded,
the initial error code, final status, parse/schema/repair latencies, and both candidate SQL strings.
Candidate SQL is available only in debug-enabled provenance and remains absent from the default
public response.

## Architecture

```text
User -> QueryRouter -> ADHOC_ANALYTICS -> SemanticGateway -> SQL reasoner
                                                        |
                                                        v
                                                candidate SQL
                                                        |
                                                        v
                                      SQLGlot safety + schema validation
                                         |                         |
                                      valid                 repairable once
                                         |                         |
                                         |                  SQL repair model
                                         |                         |
                                         +<-- full revalidation <--+
                                         |
                                         v
                                  DatabaseGateway -> read-only PostgreSQL
                                         |
                                         v
                               AnalyticalResult -> grounding/provenance
```

## Consequences

- Hallucinated relations, columns, aliases, CTE outputs, and functions fail before PostgreSQL.
- The allowed schema is both the context and enforcement boundary, preparing for table/column OPA
  filtering without adding OPA in this milestone.
- Repair can recover a narrow class of model errors while preserving deterministic control and a
  strict upper bound of one additional model call.
- Conservative star and function policies may reject otherwise executable analytical SQL; each
  expansion of policy requires review and regression coverage.
- Validation adds small measured CPU latency but remains much cheaper than model inference.
- The physical read-only role, timeouts, row limits, and byte limits remain mandatory final
  defenses.
