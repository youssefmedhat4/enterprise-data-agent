# PostgreSQL Read-Only Application Role

The application must connect with a dedicated login that owns no database objects and has no
write privileges. Run the following as a database administrator after replacing the database,
schema, role, and password placeholders for the target environment.

```sql
CREATE ROLE enterprise_analytics_reader
    LOGIN
    PASSWORD '<set-through-your-secret-manager>'
    NOSUPERUSER
    NOCREATEDB
    NOCREATEROLE
    NOREPLICATION
    NOBYPASSRLS;

ALTER ROLE enterprise_analytics_reader SET default_transaction_read_only = on;

REVOKE CREATE, TEMPORARY ON DATABASE your_analytics_database
    FROM enterprise_analytics_reader;
REVOKE ALL ON SCHEMA your_analytics_schema FROM enterprise_analytics_reader;
REVOKE ALL ON ALL TABLES IN SCHEMA your_analytics_schema
    FROM enterprise_analytics_reader;

GRANT CONNECT ON DATABASE your_analytics_database TO enterprise_analytics_reader;
GRANT USAGE ON SCHEMA your_analytics_schema TO enterprise_analytics_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA your_analytics_schema
    TO enterprise_analytics_reader;

ALTER DEFAULT PRIVILEGES IN SCHEMA your_analytics_schema
    REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA your_analytics_schema
    GRANT SELECT ON TABLES TO enterprise_analytics_reader;
```

Run `ALTER DEFAULT PRIVILEGES` as each role that creates future tables, or use a controlled owner
role for all analytics objects. Views must also be invoker-safe and must not expose data outside
the reader's intended authorization boundary.

## Application Verification

With `DB_REQUIRE_READ_ONLY=1`, every new pooled connection is rejected unless all of these are
true:

- `default_transaction_read_only` is enabled;
- the login is not a superuser;
- the login cannot create objects in an allowed schema;
- the login has no `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`, or `TRIGGER` privilege on discovered
  relations.

Every analytical execution also opens a `READ ONLY` transaction. SQLGlot validation remains a
separate required boundary and the adapter validates again before execution.

## Verification Commands

These commands do not display the configured password:

```powershell
$env:DATABASE_PROVIDER = "postgres"
$env:DATABASE_URL = "postgresql://enterprise_analytics_reader@host:5432/database"
$env:DB_ALLOWED_SCHEMAS = "analytics"
$env:DB_REQUIRE_READ_ONLY = "1"
.\.venv\Scripts\pytest --run-postgres -m postgres tests\integration\test_postgres.py -vv
```

The integration test verifies discovery, live execution metadata, and that a mutation fails at
the database permission boundary.
