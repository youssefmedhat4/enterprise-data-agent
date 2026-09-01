-- Datasource-scoped data quality assertions.
--
-- The system already knows whether its SQL is correct. It does not know whether
-- the data underneath is worth trusting, and a correct query over a table that
-- stopped loading yesterday produces a confident, wrong answer.
--
-- Deliberately small. This is not an observability platform: a handful of
-- assertion types, each configured by a human, each scoped to one datasource,
-- each answerable by one bounded read-only query.
--
-- History is bounded to the most recent checks per assertion, because the
-- question a reader asks is "is this healthy now, and did it just change" --
-- not "what did it look like last spring".

CREATE TYPE knowledge.quality_assertion_type AS ENUM (
    'FRESHNESS', 'ROW_COUNT', 'NULL_RATE', 'UNIQUE', 'ACCEPTED_VALUES', 'CUSTOM_SAFE_SQL'
);

CREATE TYPE knowledge.quality_status AS ENUM (
    'HEALTHY', 'WARNING', 'STALE', 'FAILING', 'UNKNOWN'
);

CREATE TABLE knowledge.quality_assertions (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    data_source_id          uuid NOT NULL
                                REFERENCES knowledge.data_sources (id) ON DELETE CASCADE,
    name                    text NOT NULL,
    assertion_type          knowledge.quality_assertion_type NOT NULL,
    -- Which table this speaks about, so a warning can be attached to the
    -- answers that actually read it and to no others.
    schema_name             text NOT NULL,
    table_name              text NOT NULL,
    column_name             text,
    -- Type-specific configuration, validated by the application:
    -- FRESHNESS {max_age_minutes}, NULL_RATE {max_ratio},
    -- ROW_COUNT {min_rows}, ACCEPTED_VALUES {values: [...]},
    -- CUSTOM_SAFE_SQL {sql, min_value?, max_value?}.
    configuration           jsonb NOT NULL DEFAULT '{}'::jsonb,
    enabled                 boolean NOT NULL DEFAULT true,
    created_by              text,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now(),
    UNIQUE (data_source_id, name)
);

CREATE INDEX quality_assertions_by_table
    ON knowledge.quality_assertions (data_source_id, schema_name, table_name);

CREATE TABLE knowledge.quality_check_results (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    assertion_id            uuid NOT NULL
                                REFERENCES knowledge.quality_assertions (id) ON DELETE CASCADE,
    data_source_id          uuid NOT NULL
                                REFERENCES knowledge.data_sources (id) ON DELETE CASCADE,
    status                  knowledge.quality_status NOT NULL,
    -- One measured number and a short human sentence. Never rows, never a
    -- sample of the data the assertion looked at.
    observed                double precision,
    detail                  text,
    checked_at              timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX quality_check_results_recent
    ON knowledge.quality_check_results (assertion_id, checked_at DESC);
