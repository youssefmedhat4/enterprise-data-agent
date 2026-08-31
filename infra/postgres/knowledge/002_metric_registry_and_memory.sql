-- Datasource-scoped metric registry, question memory, and learning loop.
--
-- Continues the isolation discipline of 001: every table carries
-- data_source_id, and child rows are pinned to their parent's datasource by
-- composite foreign keys rather than by application discipline alone.

CREATE TYPE knowledge.metric_status AS ENUM (
    'PROPOSED',
    'CERTIFIED',
    'REJECTED',
    'DEPRECATED',
    'STALE'
);

CREATE TYPE knowledge.candidate_type AS ENUM (
    'METRIC',
    'QUERY_EXAMPLE',
    'BUSINESS_RULE'
);

CREATE TYPE knowledge.candidate_status AS ENUM (
    'PROPOSED',
    'APPROVED',
    'REJECTED'
);

-- ---------------------------------------------------------------------------
-- Metric registry: the runtime source of truth for governed definitions
-- ---------------------------------------------------------------------------

CREATE TABLE knowledge.metric_definitions (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    data_source_id      uuid NOT NULL
                            REFERENCES knowledge.data_sources (id) ON DELETE CASCADE,
    metric_key          text NOT NULL,
    display_name        text NOT NULL,
    description         text NOT NULL DEFAULT '',
    business_meaning    text NOT NULL DEFAULT '',
    version             integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    status              knowledge.metric_status NOT NULL DEFAULT 'PROPOSED',
    -- A semantic expression over confirmed concepts, never raw executable SQL.
    -- Wren compiles governed execution; this column is documentation and
    -- dependency tracking, and is never sent to a database as a statement.
    semantic_expression text,
    grain               text,
    unit                text,
    null_behavior       text,
    owner               text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    approved_at         timestamptz,
    approved_by         text,
    UNIQUE (data_source_id, metric_key, version),
    UNIQUE (id, data_source_id)
);

CREATE INDEX metric_definitions_lookup
    ON knowledge.metric_definitions (data_source_id, status);

CREATE TABLE knowledge.metric_dimensions (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    data_source_id      uuid NOT NULL
                            REFERENCES knowledge.data_sources (id) ON DELETE CASCADE,
    metric_id           uuid NOT NULL,
    dimension_key       text NOT NULL,
    display_name        text NOT NULL DEFAULT '',
    description         text NOT NULL DEFAULT '',
    data_type           text NOT NULL DEFAULT 'string',
    is_time_dimension   boolean NOT NULL DEFAULT false,
    allowed_operators   text[] NOT NULL DEFAULT '{}',
    -- Optional link to the confirmed semantic attribute backing this dimension.
    -- When present, EntityResolver resolves values against the real column
    -- instead of guessing from a name.
    semantic_attribute_id uuid,
    UNIQUE (metric_id, dimension_key, is_time_dimension),
    FOREIGN KEY (metric_id, data_source_id)
        REFERENCES knowledge.metric_definitions (id, data_source_id) ON DELETE CASCADE,
    FOREIGN KEY (semantic_attribute_id, data_source_id)
        REFERENCES knowledge.semantic_attributes (id, data_source_id) ON DELETE SET NULL
);

CREATE INDEX metric_dimensions_lookup
    ON knowledge.metric_dimensions (data_source_id, metric_id);

-- Free-text concepts that describe what a metric means. These feed retrieval
-- documents; they are NOT routing aliases and carry no matching authority.
CREATE TABLE knowledge.metric_concepts (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    data_source_id      uuid NOT NULL
                            REFERENCES knowledge.data_sources (id) ON DELETE CASCADE,
    metric_id           uuid NOT NULL,
    concept             text NOT NULL,
    UNIQUE (metric_id, concept),
    FOREIGN KEY (metric_id, data_source_id)
        REFERENCES knowledge.metric_definitions (id, data_source_id) ON DELETE CASCADE
);

CREATE TABLE knowledge.metric_dependencies (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    data_source_id      uuid NOT NULL
                            REFERENCES knowledge.data_sources (id) ON DELETE CASCADE,
    metric_id           uuid NOT NULL,
    depends_on_metric_key text NOT NULL,
    UNIQUE (metric_id, depends_on_metric_key),
    FOREIGN KEY (metric_id, data_source_id)
        REFERENCES knowledge.metric_definitions (id, data_source_id) ON DELETE CASCADE
);

-- ---------------------------------------------------------------------------
-- Question memory
-- ---------------------------------------------------------------------------

-- Product data, deliberately separate from observability logs. Stores how
-- people ask and what analytical structure answered them. It never stores
-- result rows or numeric answers: this memory must not be able to answer a
-- current question from a stale number.
CREATE TABLE knowledge.question_events (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    data_source_id          uuid NOT NULL
                                REFERENCES knowledge.data_sources (id) ON DELETE CASCADE,
    question_text           text NOT NULL,
    normalized_question     text NOT NULL,
    route                   text NOT NULL,
    success                 boolean NOT NULL DEFAULT false,
    grounded                boolean NOT NULL DEFAULT false,
    validated               boolean NOT NULL DEFAULT false,
    semantic_plan_summary   text,
    structural_fingerprint  text NOT NULL,
    cluster_id              uuid,
    created_at              timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX question_events_scope
    ON knowledge.question_events (data_source_id, structural_fingerprint);

CREATE TABLE knowledge.question_clusters (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    data_source_id          uuid NOT NULL
                                REFERENCES knowledge.data_sources (id) ON DELETE CASCADE,
    canonical_summary       text NOT NULL,
    structural_fingerprint  text NOT NULL,
    occurrence_count        integer NOT NULL DEFAULT 0 CHECK (occurrence_count >= 0),
    successful_count        integer NOT NULL DEFAULT 0 CHECK (successful_count >= 0),
    first_seen_at           timestamptz NOT NULL DEFAULT now(),
    last_seen_at            timestamptz NOT NULL DEFAULT now(),
    status                  text NOT NULL DEFAULT 'ACTIVE',
    -- A fingerprint identifies a cluster only WITHIN a datasource, so the same
    -- wording asked of two databases can never share a cluster.
    UNIQUE (data_source_id, structural_fingerprint),
    UNIQUE (id, data_source_id),
    CHECK (successful_count <= occurrence_count)
);

ALTER TABLE knowledge.question_events
    ADD CONSTRAINT question_events_cluster_same_datasource
    FOREIGN KEY (cluster_id, data_source_id)
        REFERENCES knowledge.question_clusters (id, data_source_id) ON DELETE SET NULL;

-- ---------------------------------------------------------------------------
-- Learning loop
-- ---------------------------------------------------------------------------

CREATE TABLE knowledge.knowledge_candidates (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    data_source_id          uuid NOT NULL
                                REFERENCES knowledge.data_sources (id) ON DELETE CASCADE,
    candidate_type          knowledge.candidate_type NOT NULL,
    display_name            text NOT NULL,
    rationale               text NOT NULL DEFAULT '',
    proposed_expression     text,
    proposed_grain          text,
    structural_fingerprint  text NOT NULL,
    cluster_id              uuid,
    status                  knowledge.candidate_status NOT NULL DEFAULT 'PROPOSED',
    created_at              timestamptz NOT NULL DEFAULT now(),
    reviewed_at             timestamptz,
    reviewed_by             text,
    -- A rejected candidate stays on record so the same proposal is not
    -- immediately regenerated from the same recurring pattern.
    UNIQUE (data_source_id, candidate_type, structural_fingerprint),
    FOREIGN KEY (cluster_id, data_source_id)
        REFERENCES knowledge.question_clusters (id, data_source_id) ON DELETE SET NULL
);

CREATE INDEX knowledge_candidates_lookup
    ON knowledge.knowledge_candidates (data_source_id, status);

CREATE TABLE knowledge.approved_query_examples (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    data_source_id          uuid NOT NULL
                                REFERENCES knowledge.data_sources (id) ON DELETE CASCADE,
    question                text NOT NULL,
    semantic_plan           text NOT NULL DEFAULT '',
    -- Context for reasoning only. Never executed directly: every run still goes
    -- through current authorization, schema validation, SQLGlot, and the
    -- read-only role.
    query_pattern           text NOT NULL,
    schema_fingerprint      text,
    status                  knowledge.approval_status NOT NULL DEFAULT 'PROPOSED',
    origin_query_id         text,
    created_at              timestamptz NOT NULL DEFAULT now(),
    approved_at             timestamptz
);

CREATE INDEX approved_query_examples_lookup
    ON knowledge.approved_query_examples (data_source_id, status);
