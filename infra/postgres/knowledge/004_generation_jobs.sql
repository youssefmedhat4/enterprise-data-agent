-- Bounded job queue for knowledge candidate generation.
--
-- Generation calls a model, so it must not run inline on an analytics request
-- and must not run twice for the same cluster. A row here is a claim: a worker
-- takes one with SELECT ... FOR UPDATE SKIP LOCKED, so several API workers can
-- poll concurrently and exactly one proceeds. An in-process guard would not
-- survive a restart and would not coordinate across workers, which is the whole
-- problem this table exists to solve.
--
-- Deliberately a table rather than a broker. The volume is one job per cluster
-- crossing a threshold, and introducing a queue service for that would add an
-- operational dependency far larger than the need.

CREATE TYPE knowledge.job_status AS ENUM (
    'PENDING',
    'RUNNING',
    'SUCCEEDED',
    'FAILED'
);

CREATE TABLE knowledge.knowledge_generation_jobs (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    data_source_id  uuid NOT NULL
                        REFERENCES knowledge.data_sources (id) ON DELETE CASCADE,
    cluster_id      uuid NOT NULL,
    status          knowledge.job_status NOT NULL DEFAULT 'PENDING',
    attempt_count   integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    -- A short machine-readable code such as 'llm_rate_limited'. Never a
    -- provider message: those can quote request content back at us.
    last_error_code text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    started_at      timestamptz,
    completed_at    timestamptz,
    -- Not-yet-finished work is unique per cluster, so a threshold crossed twice
    -- cannot enqueue twice. Finished rows are exempt, which leaves an audit
    -- trail and lets a cluster be reconsidered later on new evidence.
    FOREIGN KEY (cluster_id, data_source_id)
        REFERENCES knowledge.question_clusters (id, data_source_id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX knowledge_generation_jobs_one_open_per_cluster
    ON knowledge.knowledge_generation_jobs (cluster_id)
    WHERE status IN ('PENDING', 'RUNNING');

CREATE INDEX knowledge_generation_jobs_claimable
    ON knowledge.knowledge_generation_jobs (status, created_at);

CREATE INDEX knowledge_generation_jobs_scope
    ON knowledge.knowledge_generation_jobs (data_source_id, status);
