-- Datasource-scoped evaluation sets.
--
-- Changing a model, a prompt, a confirmed mapping, a business rule or a routing
-- rule is invisible until someone notices a wrong answer. These are the
-- questions whose answers are known, so a change that breaks one is caught by
-- running them rather than by a user finding out.
--
-- Two deliberate limits on what is stored. An expected result is a small
-- canonical comparison value, never a captured result set: a benchmark that
-- carried thousands of rows would grow without bound and turn every schema
-- change into a diff nobody reads. And a run records what was compared, not
-- what came back -- one canonical rendering of the actual value, truncated, so
-- history stays readable and business data does not accumulate here.

CREATE TYPE knowledge.evaluation_expectation AS ENUM (
    'SCALAR', 'TABLE', 'ROW_COUNT', 'EMPTY'
);

CREATE TYPE knowledge.evaluation_case_status AS ENUM ('ACTIVE', 'ARCHIVED');

CREATE TYPE knowledge.evaluation_outcome AS ENUM (
    'PASS', 'FAIL', 'ERROR', 'SKIPPED'
);

CREATE TABLE knowledge.evaluation_cases (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    data_source_id          uuid NOT NULL
                                REFERENCES knowledge.data_sources (id) ON DELETE CASCADE,
    name                    text NOT NULL,
    question                text NOT NULL,
    expectation             knowledge.evaluation_expectation NOT NULL,
    -- The canonical comparison value: a number, a small table, or a row count.
    -- Shape depends on `expectation` and is validated by the application.
    expected                jsonb NOT NULL,
    -- Absolute tolerance for numeric comparison. Zero means exact.
    tolerance               numeric NOT NULL DEFAULT 0 CHECK (tolerance >= 0),
    -- Ranking questions care about row order; most do not.
    ordered                 boolean NOT NULL DEFAULT false,
    expected_route          text,
    expected_metric_ids     text[] NOT NULL DEFAULT '{}',
    status                  knowledge.evaluation_case_status NOT NULL DEFAULT 'ACTIVE',
    created_by              text,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now(),
    UNIQUE (data_source_id, name)
);

CREATE INDEX evaluation_cases_lookup
    ON knowledge.evaluation_cases (data_source_id, status);

CREATE TABLE knowledge.evaluation_runs (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    data_source_id          uuid NOT NULL
                                REFERENCES knowledge.data_sources (id) ON DELETE CASCADE,
    model_profile           text NOT NULL,
    started_at              timestamptz NOT NULL DEFAULT now(),
    finished_at             timestamptz,
    case_count              integer NOT NULL DEFAULT 0 CHECK (case_count >= 0),
    passed                  integer NOT NULL DEFAULT 0 CHECK (passed >= 0),
    failed                  integer NOT NULL DEFAULT 0 CHECK (failed >= 0),
    errored                 integer NOT NULL DEFAULT 0 CHECK (errored >= 0),
    average_latency_ms      double precision NOT NULL DEFAULT 0,
    -- What the answers were produced against, so two runs are comparable.
    -- Never a credential: schema version and model, nothing that identifies a
    -- connection.
    configuration           jsonb NOT NULL DEFAULT '{}'::jsonb,
    triggered_by            text
);

CREATE INDEX evaluation_runs_recent
    ON knowledge.evaluation_runs (data_source_id, started_at DESC);

CREATE TABLE knowledge.evaluation_case_results (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id                  uuid NOT NULL
                                REFERENCES knowledge.evaluation_runs (id) ON DELETE CASCADE,
    case_id                 uuid NOT NULL
                                REFERENCES knowledge.evaluation_cases (id) ON DELETE CASCADE,
    outcome                 knowledge.evaluation_outcome NOT NULL,
    -- A short canonical rendering of what came back, for a reader comparing it
    -- with the expectation. Bounded by the application; never the result set.
    actual                  text,
    detail                  text,
    route                   text,
    latency_ms              double precision NOT NULL DEFAULT 0,
    UNIQUE (run_id, case_id)
);

CREATE INDEX evaluation_case_results_by_case
    ON knowledge.evaluation_case_results (case_id);
