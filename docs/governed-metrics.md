# Governed Metrics

## Boundary

`MetricGateway` owns official KPI definitions and structured metric queries. It remains
separate from `SemanticGateway` (business meaning) and `DatabaseGateway` (physical SQL
execution). A `MetricQuery` accepts only a metric ID, approved dimensions, approved filters,
an approved time dimension/grain, and a bounded limit. There is no caller-supplied formula
or SQL field.

The approved production direction is `METRIC_PROVIDER=wren` (ADR 0011, superseding ADR 0003's
original `cube` choice). `CubeMetricGateway` remains fully implemented and selectable with
`METRIC_PROVIDER=cube`; it is not removed, only no longer the default.

## Metrics

| Metric | Grain | Formula | Dimensions | Time | Unit |
|---|---|---|---|---|---|
| `active_headcount` | employee | count where normalized status is `active` | department, employment status | none | employees |
| `annual_base_payroll` | employee | sum annual base salary | department, employment status, currency | none | annual currency amount |
| `net_payroll` | employee-period | sum base + bonus - deductions | department, payroll status, currency | payroll period | currency amount |
| `invoice_amount` | invoice line | sum quantity * unit price | customer, invoice status, project, currency | issue date | currency amount |
| `project_cost` | cost entry | sum cost amount | project, category, customer, department | cost date | currency amount |
| `project_margin` | project | invoice amount - project cost | project, customer, department | none | currency amount |
| `budget_utilization` | project | 100 * project cost / approved budget | project, customer, department | none | percent |

Full source, null, and version metadata lives in `app/metrics/catalog.py`. Multi-fact project
metrics aggregate invoice and cost facts to project grain before combining them, preventing
fanout.

## Local Services

Start Wren's translation-only path (the default `METRIC_PROVIDER`):

```powershell
docker compose --env-file .env -f infra/compose/docker-compose.yml --profile wren up -d --build postgres wren
```

Wren never receives database credentials; it translates a `MetricQuery` to SQL only, and
`DatabaseGateway` executes the SQLGlot-revalidated result with the `eda_readonly` role.

Cube Core remains available as a selectable alternative provider:

```powershell
docker compose --env-file .env -f infra/compose/docker-compose.yml --profile cube up -d postgres cube
```

Cube Core is pinned to `v1.7.14`. The local profile uses development mode and must never be
treated as production authentication. Cube connects to PostgreSQL only as `eda_readonly`.

## Evaluation

```powershell
$env:DATABASE_PROVIDER = "postgres"
.\.venv\Scripts\enterprise-data-metrics.exe --provider wren --output artifacts/metrics-wren.json
.\.venv\Scripts\enterprise-data-metrics.exe --provider cube --output artifacts/metrics-cube.json
```

Provider selection is explicit. An unavailable selected provider raises
`metric_provider_unavailable`; there is no fallback to the other engine or to ad-hoc SQL.
Date ranges are inclusive in the provider-neutral contract. The Wren adapter converts the
inclusive end date to Wren's half-open interval; Cube receives the inclusive date range
directly.

## Security

Wren returns physical candidate SQL, which must pass SQLGlot before `DatabaseGateway`
executes it with the read-only role. Cube accepts only validated named members through its
structured REST API and executes natively with the same read-only role. The minimal Cube
profile does not expose a caller-controlled SQL path through `MetricGateway`.
