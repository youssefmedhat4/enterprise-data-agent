-- Learning loop: question memory embeddings, cluster membership, candidate
-- evidence, approved examples and business instructions.
--
-- Migration 002 created question_events, question_clusters,
-- knowledge_candidates and approved_query_examples. This adds what the learning
-- workflow needs on top: vectors so recurrence can be judged semantically as
-- well as structurally, explicit event-to-cluster membership, evidence counts
-- that justify a proposal, and the two approved stores that were missing.
--
-- Embedding provider, model and dimension are stored beside every vector.
-- Vectors produced by different models are not comparable, so mixing them would
-- silently corrupt similarity; recording the producer lets incompatible rows be
-- identified and reindexed instead.

-- ---------------------------------------------------------------------------
-- Question memory
-- ---------------------------------------------------------------------------

ALTER TABLE knowledge.question_events
    ADD COLUMN thread_id            text,
    ADD COLUMN model_profile        text,
    ADD COLUMN metric_keys          text[] NOT NULL DEFAULT '{}',
    ADD COLUMN embedding            vector(768),
    ADD COLUMN embedding_provider   text,
    ADD COLUMN embedding_model      text,
    ADD COLUMN embedding_dimension  integer
                                        CHECK (embedding_dimension IS NULL
                                               OR embedding_dimension > 0);

-- A vector is only interpretable alongside the model that produced it.
ALTER TABLE knowledge.question_events
    ADD CONSTRAINT question_events_embedding_is_described
    CHECK (
        embedding IS NULL
        OR (embedding_provider IS NOT NULL
            AND embedding_model IS NOT NULL
            AND embedding_dimension IS NOT NULL)
    );

CREATE INDEX question_events_recent
    ON knowledge.question_events (data_source_id, created_at DESC);

CREATE INDEX question_events_cluster
    ON knowledge.question_events (cluster_id);

ALTER TABLE knowledge.question_clusters
    ADD COLUMN representative_embedding vector(768),
    ADD COLUMN embedding_provider       text,
    ADD COLUMN embedding_model          text,
    ADD COLUMN embedding_dimension      integer
                                            CHECK (embedding_dimension IS NULL
                                                   OR embedding_dimension > 0);

ALTER TABLE knowledge.question_clusters
    ADD CONSTRAINT question_clusters_embedding_is_described
    CHECK (
        representative_embedding IS NULL
        OR (embedding_provider IS NOT NULL
            AND embedding_model IS NOT NULL
            AND embedding_dimension IS NOT NULL)
    );

CREATE INDEX question_clusters_activity
    ON knowledge.question_clusters (data_source_id, last_seen_at DESC);

-- Explicit membership. question_events.cluster_id records the current
-- assignment; this records that an event was counted as evidence exactly once,
-- so recounting cannot inflate occurrence_count.
CREATE TABLE knowledge.question_cluster_members (
    cluster_id      uuid NOT NULL,
    event_id        uuid NOT NULL
                        REFERENCES knowledge.question_events (id) ON DELETE CASCADE,
    data_source_id  uuid NOT NULL
                        REFERENCES knowledge.data_sources (id) ON DELETE CASCADE,
    similarity      double precision
                        CHECK (similarity IS NULL
                               OR (similarity >= -1 AND similarity <= 1)),
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (cluster_id, event_id),
    FOREIGN KEY (cluster_id, data_source_id)
        REFERENCES knowledge.question_clusters (id, data_source_id) ON DELETE CASCADE
);

CREATE INDEX question_cluster_members_event
    ON knowledge.question_cluster_members (event_id);

-- ---------------------------------------------------------------------------
-- Candidates
-- ---------------------------------------------------------------------------

ALTER TABLE knowledge.knowledge_candidates
    ADD COLUMN description               text NOT NULL DEFAULT '',
    -- Structured proposal. For a METRIC this holds the bounded expression tree
    -- and dependency list; there is no column able to carry executable SQL.
    ADD COLUMN proposal_payload          jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN evidence_count            integer NOT NULL DEFAULT 0
                                             CHECK (evidence_count >= 0),
    ADD COLUMN successful_evidence_count integer NOT NULL DEFAULT 0
                                             CHECK (successful_evidence_count >= 0),
    ADD COLUMN rejection_reason          text,
    ADD COLUMN version                   integer NOT NULL DEFAULT 1
                                             CHECK (version >= 1),
    ADD COLUMN updated_at                timestamptz NOT NULL DEFAULT now();

ALTER TABLE knowledge.knowledge_candidates
    ADD CONSTRAINT knowledge_candidates_evidence_consistent
    CHECK (successful_evidence_count <= evidence_count);

-- ---------------------------------------------------------------------------
-- Approved query examples
-- ---------------------------------------------------------------------------

ALTER TABLE knowledge.approved_query_examples
    ADD COLUMN normalized_question  text NOT NULL DEFAULT '',
    ADD COLUMN source_cluster_id    uuid,
    ADD COLUMN embedding            vector(768),
    ADD COLUMN embedding_provider   text,
    ADD COLUMN embedding_model      text,
    ADD COLUMN embedding_dimension  integer
                                        CHECK (embedding_dimension IS NULL
                                               OR embedding_dimension > 0),
    ADD COLUMN updated_at           timestamptz NOT NULL DEFAULT now();

ALTER TABLE knowledge.approved_query_examples
    ADD CONSTRAINT approved_query_examples_embedding_is_described
    CHECK (
        embedding IS NULL
        OR (embedding_provider IS NOT NULL
            AND embedding_model IS NOT NULL
            AND embedding_dimension IS NOT NULL)
    );

ALTER TABLE knowledge.approved_query_examples
    ADD CONSTRAINT approved_query_examples_cluster_same_datasource
    FOREIGN KEY (source_cluster_id, data_source_id)
        REFERENCES knowledge.question_clusters (id, data_source_id) ON DELETE SET NULL;

-- A stored example is reasoning context, never a shortcut. Refuse anything that
-- is not a single read at write time, so a mutating statement cannot be stored
-- and later shown to the model as an approved pattern.
ALTER TABLE knowledge.approved_query_examples
    ADD CONSTRAINT approved_query_examples_are_read_only
    CHECK (
        query_pattern !~* '\y(insert|update|delete|drop|alter|truncate|grant|revoke|create)\y'
    );

-- ---------------------------------------------------------------------------
-- Business instructions
-- ---------------------------------------------------------------------------

CREATE TABLE knowledge.business_instructions (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    data_source_id      uuid NOT NULL
                            REFERENCES knowledge.data_sources (id) ON DELETE CASCADE,
    title               text NOT NULL,
    instruction         text NOT NULL,
    -- Concepts and metrics this guidance applies to. Retrieval uses these so an
    -- instruction reaches a prompt only when it is relevant, rather than every
    -- instruction being appended to every request.
    semantic_concepts   text[] NOT NULL DEFAULT '{}',
    metric_keys         text[] NOT NULL DEFAULT '{}',
    status              knowledge.approval_status NOT NULL DEFAULT 'PROPOSED',
    source_candidate_id uuid REFERENCES knowledge.knowledge_candidates (id)
                            ON DELETE SET NULL,
    schema_fingerprint  text,
    embedding           vector(768),
    embedding_provider  text,
    embedding_model     text,
    embedding_dimension integer CHECK (embedding_dimension IS NULL
                                       OR embedding_dimension > 0),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    approved_at         timestamptz,
    approved_by         text,
    UNIQUE (data_source_id, title),
    CONSTRAINT business_instructions_embedding_is_described CHECK (
        embedding IS NULL
        OR (embedding_provider IS NOT NULL
            AND embedding_model IS NOT NULL
            AND embedding_dimension IS NOT NULL)
    )
);

CREATE INDEX business_instructions_lookup
    ON knowledge.business_instructions (data_source_id, status);
