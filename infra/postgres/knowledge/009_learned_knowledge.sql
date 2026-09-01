-- Five more kinds of reusable knowledge the worker may propose, and the
-- normalized stores each one is promoted into.
--
-- Promotion writes to a real store rather than leaving the knowledge inside an
-- approved candidate row. A candidate is a record of a decision; the decision's
-- effect belongs somewhere the runtime reads, or approval means nothing.
--
-- Everything is scoped by data_source_id. A synonym learned about one database
-- must not silently change how another is understood, and a join relationship
-- cannot span two databases at all.

ALTER TYPE knowledge.candidate_type ADD VALUE IF NOT EXISTS 'FILTER';
ALTER TYPE knowledge.candidate_type ADD VALUE IF NOT EXISTS 'SYNONYM';
ALTER TYPE knowledge.candidate_type ADD VALUE IF NOT EXISTS 'ENTITY_ALIAS';
ALTER TYPE knowledge.candidate_type ADD VALUE IF NOT EXISTS 'JOIN_RULE';
ALTER TYPE knowledge.candidate_type ADD VALUE IF NOT EXISTS 'DESCRIPTION_IMPROVEMENT';

-- A reusable population, held as a bounded predicate over confirmed semantic
-- attributes rather than as SQL. "Valid posted costs" is a business idea; the
-- SQL that expresses it is not something to trust from a model.
CREATE TABLE knowledge.approved_filters (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    data_source_id          uuid NOT NULL
                                REFERENCES knowledge.data_sources (id) ON DELETE CASCADE,
    name                    text NOT NULL,
    description             text NOT NULL DEFAULT '',
    -- A small tree of {op, attribute_id, values} nodes, validated against the
    -- confirmed semantic model before it is stored.
    predicate               jsonb NOT NULL,
    status                  knowledge.approval_status NOT NULL DEFAULT 'CONFIRMED',
    source_candidate_id     uuid,
    approved_by             text,
    approved_at             timestamptz NOT NULL DEFAULT now(),
    UNIQUE (data_source_id, name)
);

-- Language that points at meaning the review has already confirmed. A synonym
-- never creates meaning of its own: it names something that exists.
CREATE TABLE knowledge.approved_synonyms (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    data_source_id          uuid NOT NULL
                                REFERENCES knowledge.data_sources (id) ON DELETE CASCADE,
    -- What it points at: a confirmed concept, a certified metric key, or a
    -- dimension. Validated at approval, not asserted by the model.
    target_kind             text NOT NULL,
    target                  text NOT NULL,
    phrases                 text[] NOT NULL,
    status                  knowledge.approval_status NOT NULL DEFAULT 'CONFIRMED',
    source_candidate_id     uuid,
    approved_by             text,
    approved_at             timestamptz NOT NULL DEFAULT now(),
    UNIQUE (data_source_id, target_kind, target)
);

-- A business naming relationship: "People Ops" is what people call this unit.
-- Deliberately separate from row identity, which the live entity lookup owns:
-- an alias must never become a way to bind an ambiguous value globally.
CREATE TABLE knowledge.approved_entity_aliases (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    data_source_id          uuid NOT NULL
                                REFERENCES knowledge.data_sources (id) ON DELETE CASCADE,
    entity_id               uuid NOT NULL
                                REFERENCES knowledge.semantic_entities (id) ON DELETE CASCADE,
    alias                   text NOT NULL,
    -- Optional: the canonical key this alias names, when the reviewer knows it.
    -- Null means the alias points at the entity, not at one row of it.
    canonical_key           text,
    status                  knowledge.approval_status NOT NULL DEFAULT 'CONFIRMED',
    source_candidate_id     uuid,
    approved_by             text,
    approved_at             timestamptz NOT NULL DEFAULT now(),
    UNIQUE (data_source_id, entity_id, alias)
);

-- A relationship the database does not declare. Both sides are confirmed
-- semantic attributes, so a join rule cannot reference a column nobody
-- reviewed, and both must belong to the same datasource.
CREATE TABLE knowledge.approved_join_rules (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    data_source_id          uuid NOT NULL
                                REFERENCES knowledge.data_sources (id) ON DELETE CASCADE,
    left_attribute_id       uuid NOT NULL
                                REFERENCES knowledge.semantic_attributes (id) ON DELETE CASCADE,
    right_attribute_id      uuid NOT NULL
                                REFERENCES knowledge.semantic_attributes (id) ON DELETE CASCADE,
    cardinality             text NOT NULL DEFAULT 'MANY_TO_ONE',
    status                  knowledge.approval_status NOT NULL DEFAULT 'CONFIRMED',
    source_candidate_id     uuid,
    approved_by             text,
    approved_at             timestamptz NOT NULL DEFAULT now(),
    UNIQUE (data_source_id, left_attribute_id, right_attribute_id)
);

-- Description changes keep their history. A confirmed description is human
-- work, and replacing it without a trail makes an improvement indistinguishable
-- from a mistake.
CREATE TABLE knowledge.semantic_description_revisions (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    data_source_id          uuid NOT NULL
                                REFERENCES knowledge.data_sources (id) ON DELETE CASCADE,
    subject_kind            text NOT NULL,
    subject_id              uuid NOT NULL,
    previous_description    text NOT NULL DEFAULT '',
    description             text NOT NULL,
    source_candidate_id     uuid,
    approved_by             text,
    approved_at             timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX semantic_description_revisions_subject
    ON knowledge.semantic_description_revisions (data_source_id, subject_id, approved_at DESC);
