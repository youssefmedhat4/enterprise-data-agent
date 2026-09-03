-- Persistent conversation transcripts.
--
-- Three storage concerns stay separate on purpose:
--
--   question_clusters / question_memory  -- what shapes of question recur, so
--                                           the system can learn from them
--   checkpoint database                  -- LangGraph's analytical state for
--                                           follow-ups
--   the two tables below                 -- what the user and the assistant
--                                           actually said
--
-- Only the last of these is a product transcript. Question memory deliberately
-- holds no answers, no SQL and no rows, and this migration does not change
-- that: it adds a place for the transcript rather than widening the learning
-- store into one.
--
-- A conversation names the LangGraph thread it continues, so reopening it
-- restores both the visible history and the analytical context that answers a
-- follow-up.

CREATE TABLE knowledge.conversations (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- The authenticated subject. Ownership is checked before any message,
    -- result preview or provenance reference is returned.
    owner_subject_id    text NOT NULL,
    data_source_id      uuid NOT NULL
        REFERENCES knowledge.data_sources (id) ON DELETE CASCADE,
    -- The LangGraph checkpoint thread this conversation continues. Stored
    -- rather than derived, so the relationship survives any change to how
    -- thread keys are minted.
    thread_id           text NOT NULL UNIQUE,
    title               text NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    archived_at         timestamptz,
    -- Lets a message carry a composite foreign key, which is what makes a
    -- Legacy ERP transcript unable to hold a Company Analytics turn.
    CONSTRAINT conversations_identity_scope UNIQUE (id, data_source_id)
);

-- The sidebar reads one owner's most recent conversations.
CREATE INDEX conversations_by_owner
    ON knowledge.conversations (owner_subject_id, updated_at DESC)
    WHERE archived_at IS NULL;

CREATE TABLE knowledge.conversation_messages (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id     uuid NOT NULL,
    -- Repeated from the conversation so the composite foreign key below can
    -- enforce that a turn never crosses datasources.
    data_source_id      uuid NOT NULL,
    role                text NOT NULL CHECK (role IN ('user', 'assistant')),
    -- Ordering within the conversation. Explicit rather than by timestamp:
    -- a question and its answer are written in the same transaction and would
    -- otherwise be indistinguishable in order.
    sequence            integer NOT NULL,
    -- What was said. A user turn carries the question; an assistant turn
    -- carries the answer text it displayed.
    content             text NOT NULL,
    -- Bounded UI payload for an assistant turn: columns, a capped row preview,
    -- chart spec, provenance and the trace the answer was rendered from.
    -- Never a full result set, and never a prompt, a credential or hidden
    -- model reasoning.
    payload             jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- The analytics request this turn came from, so an answer can be tied back
    -- to its run without duplicating what the run recorded.
    request_id          text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT conversation_messages_same_datasource
        FOREIGN KEY (conversation_id, data_source_id)
        REFERENCES knowledge.conversations (id, data_source_id)
        ON DELETE CASCADE,
    CONSTRAINT conversation_messages_ordered UNIQUE (conversation_id, sequence)
);

CREATE INDEX conversation_messages_by_conversation
    ON knowledge.conversation_messages (conversation_id, sequence);
