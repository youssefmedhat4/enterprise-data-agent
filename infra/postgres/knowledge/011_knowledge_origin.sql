-- Explicit lineage from a reviewed candidate to the authoritative knowledge
-- object it created. Candidates remain review history; runtime continues to
-- read only the promoted stores.

ALTER TABLE knowledge.knowledge_candidates
    ADD COLUMN promoted_to_type text,
    ADD COLUMN promoted_to_id uuid,
    ADD CONSTRAINT knowledge_candidates_promotion_complete CHECK (
        (promoted_to_type IS NULL AND promoted_to_id IS NULL)
        OR (promoted_to_type IS NOT NULL AND promoted_to_id IS NOT NULL)
    ),
    ADD CONSTRAINT knowledge_candidates_identity_scope
        UNIQUE (id, data_source_id);

ALTER TABLE knowledge.approved_query_examples
    ADD COLUMN source_candidate_id uuid,
    ADD COLUMN approved_by text,
    ADD CONSTRAINT approved_query_examples_candidate_same_datasource
        FOREIGN KEY (source_candidate_id, data_source_id)
        REFERENCES knowledge.knowledge_candidates (id, data_source_id)
        ON DELETE RESTRICT;

ALTER TABLE knowledge.metric_definitions
    ADD COLUMN source_candidate_id uuid,
    ADD CONSTRAINT metric_definitions_candidate_same_datasource
        FOREIGN KEY (source_candidate_id, data_source_id)
        REFERENCES knowledge.knowledge_candidates (id, data_source_id)
        ON DELETE RESTRICT;

-- Existing learned stores already retained candidate IDs. Tighten those links
-- so a row can never claim an origin in another datasource.
ALTER TABLE knowledge.business_instructions
    DROP CONSTRAINT IF EXISTS business_instructions_source_candidate_id_fkey,
    ADD CONSTRAINT business_instructions_candidate_same_datasource
        FOREIGN KEY (source_candidate_id, data_source_id)
        REFERENCES knowledge.knowledge_candidates (id, data_source_id)
        ON DELETE RESTRICT;

ALTER TABLE knowledge.approved_filters
    ADD CONSTRAINT approved_filters_candidate_same_datasource
        FOREIGN KEY (source_candidate_id, data_source_id)
        REFERENCES knowledge.knowledge_candidates (id, data_source_id)
        ON DELETE RESTRICT;

ALTER TABLE knowledge.approved_synonyms
    ADD CONSTRAINT approved_synonyms_candidate_same_datasource
        FOREIGN KEY (source_candidate_id, data_source_id)
        REFERENCES knowledge.knowledge_candidates (id, data_source_id)
        ON DELETE RESTRICT;

ALTER TABLE knowledge.approved_entity_aliases
    ADD CONSTRAINT approved_entity_aliases_candidate_same_datasource
        FOREIGN KEY (source_candidate_id, data_source_id)
        REFERENCES knowledge.knowledge_candidates (id, data_source_id)
        ON DELETE RESTRICT;

ALTER TABLE knowledge.approved_join_rules
    ADD CONSTRAINT approved_join_rules_candidate_same_datasource
        FOREIGN KEY (source_candidate_id, data_source_id)
        REFERENCES knowledge.knowledge_candidates (id, data_source_id)
        ON DELETE RESTRICT;

ALTER TABLE knowledge.semantic_description_revisions
    ADD CONSTRAINT semantic_description_revisions_candidate_same_datasource
        FOREIGN KEY (source_candidate_id, data_source_id)
        REFERENCES knowledge.knowledge_candidates (id, data_source_id)
        ON DELETE RESTRICT;

-- Query examples already carried their source cluster. This is an exact-ID
-- backfill for previously promoted examples, not a name or SQL comparison.
UPDATE knowledge.approved_query_examples AS example
SET source_candidate_id = candidate.id,
    approved_by = candidate.reviewed_by
FROM knowledge.knowledge_candidates AS candidate
WHERE example.source_candidate_id IS NULL
  AND candidate.data_source_id = example.data_source_id
  AND candidate.cluster_id = example.source_cluster_id
  AND candidate.candidate_type = 'QUERY_EXAMPLE'
  AND candidate.status = 'APPROVED';

-- Backfill the inverse destination for stores which already retained the
-- candidate ID. Future promotions write this directly in application code.
UPDATE knowledge.knowledge_candidates AS candidate
SET promoted_to_type = promoted.kind,
    promoted_to_id = promoted.id
FROM (
    SELECT source_candidate_id, data_source_id, 'QUERY_EXAMPLE'::text AS kind, id
      FROM knowledge.approved_query_examples WHERE source_candidate_id IS NOT NULL
    UNION ALL
    SELECT source_candidate_id, data_source_id, 'BUSINESS_RULE', id
      FROM knowledge.business_instructions WHERE source_candidate_id IS NOT NULL
    UNION ALL
    SELECT source_candidate_id, data_source_id, 'FILTER', id
      FROM knowledge.approved_filters WHERE source_candidate_id IS NOT NULL
    UNION ALL
    SELECT source_candidate_id, data_source_id, 'SYNONYM', id
      FROM knowledge.approved_synonyms WHERE source_candidate_id IS NOT NULL
    UNION ALL
    SELECT source_candidate_id, data_source_id, 'ENTITY_ALIAS', id
      FROM knowledge.approved_entity_aliases WHERE source_candidate_id IS NOT NULL
    UNION ALL
    SELECT source_candidate_id, data_source_id, 'JOIN_RULE', id
      FROM knowledge.approved_join_rules WHERE source_candidate_id IS NOT NULL
    UNION ALL
    SELECT source_candidate_id, data_source_id, 'DESCRIPTION_IMPROVEMENT', id
      FROM knowledge.semantic_description_revisions WHERE source_candidate_id IS NOT NULL
) AS promoted
WHERE candidate.id = promoted.source_candidate_id
  AND candidate.data_source_id = promoted.data_source_id;

-- Metric candidates did not previously retain an ID link on the destination.
-- The stable metric key in their structured payload provides a one-time
-- backfill; no candidate display text is compared.
UPDATE knowledge.metric_definitions AS metric
SET source_candidate_id = candidate.id
FROM knowledge.knowledge_candidates AS candidate
WHERE metric.source_candidate_id IS NULL
  AND candidate.data_source_id = metric.data_source_id
  AND candidate.candidate_type = 'METRIC'
  AND candidate.status = 'APPROVED'
  AND candidate.proposal_payload ->> 'metric_key' = metric.metric_key;

UPDATE knowledge.knowledge_candidates AS candidate
SET promoted_to_type = 'METRIC',
    promoted_to_id = metric.id
FROM knowledge.metric_definitions AS metric
WHERE metric.source_candidate_id = candidate.id
  AND metric.data_source_id = candidate.data_source_id;
