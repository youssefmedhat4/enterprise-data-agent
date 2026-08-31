-- Datasource-scoped semantic registry.
--
-- This schema lives in the INTERNAL application database, never in a customer's
-- analytics database. Analytics databases stay read-only to this system.
--
-- Every table holding learned or approved knowledge carries data_source_id and
-- is constrained so a row can only ever reference rows from the same
-- datasource. Isolation is enforced by the schema, not only by query discipline.

CREATE SCHEMA IF NOT EXISTS knowledge;

-- pgvector powers semantic retrieval. Requires the pgvector/pgvector image.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TYPE knowledge.approval_status AS ENUM (
    'PROPOSED',
    'CONFIRMED',
    'REJECTED',
    'STALE'
);

CREATE TYPE knowledge.datasource_status AS ENUM (
    'REGISTERED',
    'SCANNING',
    'READY',
    'ERROR',
    'DISABLED'
);

-- ---------------------------------------------------------------------------
-- Datasource registry
-- ---------------------------------------------------------------------------

-- connection_ref is a SECRET REFERENCE (an env var name), never a DSN and never
-- a password. The CHECK keeps obvious connection strings out of the column.
CREATE TABLE knowledge.data_sources (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name                text NOT NULL UNIQUE,
    database_type       text NOT NULL,
    connection_ref      text NOT NULL,
    status              knowledge.datasource_status NOT NULL DEFAULT 'REGISTERED',
    schema_fingerprint  text,
    is_default          boolean NOT NULL DEFAULT false,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    last_scanned_at     timestamptz,
    CONSTRAINT data_sources_connection_ref_is_not_a_dsn
        CHECK (position('://' in connection_ref) = 0
               AND connection_ref NOT ILIKE '%password%')
);

-- At most one default datasource.
CREATE UNIQUE INDEX data_sources_single_default
    ON knowledge.data_sources (is_default)
    WHERE is_default;

-- ---------------------------------------------------------------------------
-- Confirmed semantic model
-- ---------------------------------------------------------------------------

CREATE TABLE knowledge.semantic_entities (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    data_source_id      uuid NOT NULL
                            REFERENCES knowledge.data_sources (id) ON DELETE CASCADE,
    source_schema       text NOT NULL,
    source_table        text NOT NULL,
    entity_name         text NOT NULL,
    description         text,
    confidence          double precision
                            CHECK (confidence IS NULL
                                   OR (confidence >= 0 AND confidence <= 1)),
    reason_code         text,
    status              knowledge.approval_status NOT NULL DEFAULT 'PROPOSED',
    schema_fingerprint  text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    reviewed_at         timestamptz,
    UNIQUE (data_source_id, source_schema, source_table),
    -- Enables the composite foreign keys below, which pin child rows to the
    -- same datasource as their parent entity.
    UNIQUE (id, data_source_id)
);

CREATE INDEX semantic_entities_lookup
    ON knowledge.semantic_entities (data_source_id, status);

CREATE TABLE knowledge.semantic_attributes (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    data_source_id      uuid NOT NULL
                            REFERENCES knowledge.data_sources (id) ON DELETE CASCADE,
    entity_id           uuid NOT NULL,
    source_column       text NOT NULL,
    concept_name        text NOT NULL,
    description         text,
    data_type           text,
    is_identifier       boolean NOT NULL DEFAULT false,
    confidence          double precision
                            CHECK (confidence IS NULL
                                   OR (confidence >= 0 AND confidence <= 1)),
    status              knowledge.approval_status NOT NULL DEFAULT 'PROPOSED',
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    reviewed_at         timestamptz,
    UNIQUE (entity_id, source_column),
    -- An attribute can never point at an entity in a different datasource.
    FOREIGN KEY (entity_id, data_source_id)
        REFERENCES knowledge.semantic_entities (id, data_source_id) ON DELETE CASCADE
);

CREATE INDEX semantic_attributes_lookup
    ON knowledge.semantic_attributes (data_source_id, status);

CREATE TABLE knowledge.semantic_relationships (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    data_source_id      uuid NOT NULL
                            REFERENCES knowledge.data_sources (id) ON DELETE CASCADE,
    from_entity_id      uuid NOT NULL,
    to_entity_id        uuid NOT NULL,
    from_column         text NOT NULL,
    to_column           text NOT NULL,
    relationship_name   text NOT NULL,
    cardinality         text CHECK (
                            cardinality IS NULL
                            OR cardinality IN ('one_to_one', 'many_to_one',
                                               'one_to_many', 'many_to_many')
                        ),
    confidence          double precision
                            CHECK (confidence IS NULL
                                   OR (confidence >= 0 AND confidence <= 1)),
    status              knowledge.approval_status NOT NULL DEFAULT 'PROPOSED',
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    reviewed_at         timestamptz,
    CHECK (from_entity_id <> to_entity_id OR from_column <> to_column),
    -- Both endpoints are pinned to this row's datasource.
    FOREIGN KEY (from_entity_id, data_source_id)
        REFERENCES knowledge.semantic_entities (id, data_source_id) ON DELETE CASCADE,
    FOREIGN KEY (to_entity_id, data_source_id)
        REFERENCES knowledge.semantic_entities (id, data_source_id) ON DELETE CASCADE
);

CREATE INDEX semantic_relationships_lookup
    ON knowledge.semantic_relationships (data_source_id, status);

-- ---------------------------------------------------------------------------
-- Retrieval index
-- ---------------------------------------------------------------------------

-- Embedding provenance is stored WITH the vector so incompatible embeddings can
-- never be compared. Retrieval must filter on provider, model, and dimension.
CREATE TABLE knowledge.knowledge_embeddings (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    data_source_id      uuid NOT NULL
                            REFERENCES knowledge.data_sources (id) ON DELETE CASCADE,
    document_kind       text NOT NULL,
    document_id         uuid NOT NULL,
    content             text NOT NULL,
    content_tsv         tsvector
                            GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    embedding_provider  text NOT NULL,
    embedding_model     text NOT NULL,
    embedding_dimension integer NOT NULL CHECK (embedding_dimension > 0),
    embedding           vector NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (data_source_id, document_kind, document_id,
            embedding_provider, embedding_model, embedding_dimension),
    CONSTRAINT knowledge_embeddings_dimension_matches_vector
        CHECK (vector_dims(embedding) = embedding_dimension)
);

CREATE INDEX knowledge_embeddings_scope
    ON knowledge.knowledge_embeddings
    (data_source_id, embedding_provider, embedding_model, embedding_dimension);

CREATE INDEX knowledge_embeddings_lexical
    ON knowledge.knowledge_embeddings USING gin (content_tsv);
