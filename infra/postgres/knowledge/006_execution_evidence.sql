-- Evidence that one recurring question was answered by SQL a run actually
-- validated and executed.
--
-- A query example is a claim that a particular SQL shape answers a particular
-- question well. Nothing else in the knowledge layer could support that claim:
-- question memory deliberately records only recurrence metadata -- route,
-- fingerprint, whether the run succeeded -- and never SQL, so that remembering
-- what people ask can never become a store of what their data looks like. That
-- stays true; this table is separate, narrower, and holds only what promoting
-- an example requires.
--
-- One row per cluster, replaced on each qualifying run, so this is bounded by
-- how many distinct question shapes a datasource has rather than by traffic.
-- Only successful, validated, grounded ad-hoc runs write here.
--
-- The SQL kept here is evidence for a reviewer and context for a model. It is
-- never executed: approval re-validates it against the current authorized
-- schema, and every future answer is freshly generated SQL that passes
-- SQLGlot, current authorization and the read-only role on its own.

CREATE TABLE knowledge.query_execution_evidence (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    data_source_id          uuid NOT NULL
                                REFERENCES knowledge.data_sources (id) ON DELETE CASCADE,
    cluster_id              uuid NOT NULL,
    question_text           text NOT NULL,
    validated_sql           text NOT NULL,
    schema_fingerprint      text,
    recorded_at             timestamptz NOT NULL DEFAULT now(),
    -- Evidence belongs to one cluster in one datasource. The composite foreign
    -- key is what stops a cluster from another database being referenced here.
    UNIQUE (data_source_id, cluster_id),
    FOREIGN KEY (cluster_id, data_source_id)
        REFERENCES knowledge.question_clusters (id, data_source_id) ON DELETE CASCADE
);

-- The SQL a candidate was proposed from, copied at generation time so review
-- reads a fixed statement rather than whatever the latest run happened to
-- produce. Null for candidates that are not query examples.
ALTER TABLE knowledge.knowledge_candidates
    ADD COLUMN evidence_sql                 text,
    ADD COLUMN evidence_schema_fingerprint  text;
