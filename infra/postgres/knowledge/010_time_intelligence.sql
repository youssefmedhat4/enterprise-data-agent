-- Datasource-scoped time intelligence: a calendar, the columns that carry time,
-- and how each certified metric behaves across it.
--
-- Three things are being written down that were previously assumed.
--
-- A calendar is a fact about a company, not about language. "Fiscal year to
-- date" means a different range at a business whose year starts in July than at
-- one starting in January, and -- the part most often left implicit -- the same
-- July-to-July range is FY2026 at some companies and FY2027 at others. Nothing
-- can infer that, so it is configuration, and a policy nobody confirmed stays
-- DEFAULT rather than being treated as agreed.
--
-- A database has many dates. Guessing which one "last year" means, because its
-- name contains "date", is how an answer ends up confidently about the wrong
-- column. A temporal dimension is therefore reviewed metadata on a confirmed
-- semantic attribute, including how the column physically stores its value:
-- older systems keep dates as CHAR(8) text, and reading one safely needs a
-- declared strategy rather than a parsing expression a model wrote.
--
-- Metrics do not all behave the same across time. Invoiced revenue accumulates
-- over a period; headcount does not, and summing daily headcounts over a year
-- produces a number with no meaning. Saying which is which is what stops that.

CREATE TYPE knowledge.week_start AS ENUM (
    'MONDAY', 'TUESDAY', 'WEDNESDAY', 'THURSDAY', 'FRIDAY', 'SATURDAY', 'SUNDAY'
);

CREATE TYPE knowledge.fiscal_year_label AS ENUM ('START_YEAR', 'END_YEAR');

CREATE TYPE knowledge.time_policy_status AS ENUM ('DEFAULT', 'CONFIRMED');

CREATE TYPE knowledge.temporal_role AS ENUM (
    'EVENT_TIME', 'EFFECTIVE_START', 'EFFECTIVE_END', 'SNAPSHOT_DATE',
    'CREATED_AT', 'UPDATED_AT', 'LOAD_TIME', 'START_DATE', 'END_DATE'
);

CREATE TYPE knowledge.temporal_storage AS ENUM (
    'NATIVE_DATE', 'NATIVE_TIMESTAMP', 'TIMESTAMP_WITH_TIMEZONE', 'YYYYMMDD_TEXT'
);

CREATE TYPE knowledge.metric_temporal_behavior AS ENUM (
    'NONE', 'FLOW', 'SNAPSHOT'
);

-- One calendar per datasource.
CREATE TABLE knowledge.time_policies (
    id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    data_source_id              uuid NOT NULL UNIQUE
                                    REFERENCES knowledge.data_sources (id) ON DELETE CASCADE,
    -- An IANA zone name, validated by the application against the system
    -- database. Free text is how "EST" and "GMT+2" get stored and then
    -- mishandle daylight saving.
    timezone                    text NOT NULL DEFAULT 'UTC',
    week_start                  knowledge.week_start NOT NULL DEFAULT 'MONDAY',
    fiscal_year_start_month     integer NOT NULL DEFAULT 1
                                    CHECK (fiscal_year_start_month BETWEEN 1 AND 12),
    -- Capped at 28 so the fiscal year start exists in every month.
    fiscal_year_start_day       integer NOT NULL DEFAULT 1
                                    CHECK (fiscal_year_start_day BETWEEN 1 AND 28),
    fiscal_year_label           knowledge.fiscal_year_label NOT NULL DEFAULT 'START_YEAR',
    status                      knowledge.time_policy_status NOT NULL DEFAULT 'DEFAULT',
    version                     integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    updated_by                  text,
    created_at                  timestamptz NOT NULL DEFAULT now(),
    updated_at                  timestamptz NOT NULL DEFAULT now()
);

-- Which columns carry time, and what each one means.
CREATE TABLE knowledge.temporal_dimensions (
    id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    data_source_id              uuid NOT NULL
                                    REFERENCES knowledge.data_sources (id) ON DELETE CASCADE,
    semantic_attribute_id       uuid NOT NULL
                                    REFERENCES knowledge.semantic_attributes (id) ON DELETE CASCADE,
    role                        knowledge.temporal_role NOT NULL,
    storage                     knowledge.temporal_storage NOT NULL,
    -- Only a reviewer sets this: it answers "projects last year" and must be a
    -- decision rather than an inference from a column name.
    is_default_for_entity       boolean NOT NULL DEFAULT false,
    status                      knowledge.approval_status NOT NULL DEFAULT 'PROPOSED',
    -- The schema version this mapping was confirmed against, so a rescan can
    -- mark exactly the affected mappings stale and leave the rest alone.
    schema_fingerprint          text,
    reviewed_by                 text,
    created_at                  timestamptz NOT NULL DEFAULT now(),
    updated_at                  timestamptz NOT NULL DEFAULT now(),
    -- One temporal meaning per attribute per datasource.
    UNIQUE (data_source_id, semantic_attribute_id)
);

CREATE INDEX temporal_dimensions_lookup
    ON knowledge.temporal_dimensions (data_source_id, status);

-- How each certified metric behaves across time, and which column it measures
-- against. Null behaviour means nobody has said, which is different from
-- saying the metric is not temporal.
ALTER TABLE knowledge.metric_definitions
    ADD COLUMN temporal_behavior       knowledge.metric_temporal_behavior
                                           NOT NULL DEFAULT 'NONE',
    ADD COLUMN temporal_dimension_id   uuid
                                           REFERENCES knowledge.temporal_dimensions (id)
                                           ON DELETE SET NULL;

-- Evaluation cases involving relative time need a fixed anchor, or "revenue
-- year to date" quietly means something different every month and the
-- regression it was written to catch never fails twice the same way.
ALTER TABLE knowledge.evaluation_cases
    ADD COLUMN as_of timestamptz;

ALTER TABLE knowledge.evaluation_runs
    ADD COLUMN as_of timestamptz;
