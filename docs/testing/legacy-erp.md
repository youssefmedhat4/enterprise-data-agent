# Legacy ERP test database

## Why it exists

The primary demo database is named the way the business talks: `employees`,
`departments`, `salary`. That makes it a poor test of semantic discovery,
because a system could score well on it by matching words rather than
understanding anything.

This fixture is deliberately unfamiliar. It is shaped like something inherited
from an older accounting package — abbreviated names, status codes resolved
through a lookup table, `CHAR(8)` dates, `Y`/`N` flags, effective-dated
compensation, header/detail invoices — and it is internally coherent and
queryable. Nothing in it is annotated with what a column "really means".

The point is not that the tests pass. It is to find where the architecture
breaks on a schema nobody designed for it.

## Starting it

```bash
docker compose --env-file .env -f infra/compose/docker-compose.yml --profile legacy up -d --wait legacy-postgres
```

It sits behind the `legacy` profile, so ordinary development never pays for a
container it does not query. It listens on `5434` and seeds itself on first
start from `infra/postgres/legacy/`.

`.env` needs `LEGACY_DATABASE_URL` and the reference must be allow-listed:

```
LEGACY_DATABASE_URL=postgresql://erp_readonly:...@localhost:5434/legacy_erp
ALLOWED_CONNECTION_REFS=DATABASE_URL,LEGACY_DATABASE_URL
```

The role is granted `SELECT` only and has `default_transaction_read_only = on`,
which the database gateway verifies before it will run anything.

## Schema

| Table | Holds |
| --- | --- |
| `emp_mst` | Employee master, status as a code |
| `emp_comp_hist` | Compensation *history*, one row in force per employee |
| `org_unit_lkp` | Organisational units, hierarchical |
| `cust_mst` | Customers |
| `prj_hdr` | Projects |
| `ar_inv_hdr` | Invoice headers |
| `ar_inv_ln` | Invoice detail lines |
| `gl_cost_txn` | Project cost postings |
| `code_lkp` | Code descriptions, keyed by domain |

`prj_hdr.cust_cd` and `prj_hdr.own_org_cd` deliberately carry **no** foreign
key, so discovery has to infer those joins rather than read them.

## Intentional traps

Each one is a mistake a plausible query makes, with the size of the error:

| Trap | Naive result | Correct |
| --- | --- | --- |
| Summing all compensation history | 15,595,000 | 6,345,000 |
| Counting compensation rows as people | 159 | 42 |
| Counting every employee as active | 60 | 42 |
| Including void invoices | 1,283,400 | 839,700 |
| Including reversed and unposted costs | 1,439,250 | 1,042,500 |

Also present: one employee with no current compensation row, terminated staff
who still have salary history, two organisational units both displaying
"Operations" (`OU2100`, `OU2200`), near-identical customers (`ACME Holdings`
vs `ACME Holding Co.`), near-identical project names, projects with no
invoices, projects with no costs, and an inactive organisational unit.

## Business rules

These are the yardstick. They are **not** given to the model during discovery;
they exist so a human can tell which of two answers is wrong.

- **Active headcount** — employees whose status code is `A`.
- **Current annual payroll** — `ann_sal_amt` from rows where `curr_flg = 'Y'`,
  never the sum of history.
- **Average current salary** — mean of those same rows; an employee without a
  current row is excluded rather than counted as zero.
- **Invoiced amount** — `qty * unit_amt - disc_amt` over lines whose header is
  not void.
- **Project cost** — postings that are `posted_flg = 'Y'` and
  `reversal_flg = 'N'`.
- **Project margin** — invoiced amount minus project cost.

`tests/fixtures/legacy_reference.sql` implements each of these independently of
anything the application generates.

## Onboarding it

Through the real flow, not by inserting semantic rows by hand:

1. Register it: `POST /knowledge/data-sources` with
   `connection_ref: LEGACY_DATABASE_URL` and `allowed_schemas: ["erp"]`, or
   **Knowledge → Data sources → Add data source** in the UI.
2. Scan it: `POST /knowledge/data-sources/{id}/scan`, or the **Scan** button.
   This reads schema metadata and runs live semantic discovery.
3. Review the proposals under **Knowledge → Schema review**. Everything arrives
   `PROPOSED`; nothing reaches runtime until it is approved.

## Rebuilding

The seed is arithmetic, never random, so a rebuild reproduces byte-identical
data and the reference results never drift. To start over:

```bash
docker compose -f infra/compose/docker-compose.yml --profile legacy down -v
```
