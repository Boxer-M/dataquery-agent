CREATE SCHEMA agent_meta;
REVOKE ALL ON SCHEMA agent_meta FROM PUBLIC;
CREATE TABLE agent_meta.conversations (
    conversation_id uuid PRIMARY KEY,
    owner_hash text NOT NULL,
    state_version bigint NOT NULL DEFAULT 0,
    active_query_context jsonb,
    pending_clarification jsonb,
    last_message_id uuid,
    last_query_id uuid,
    in_flight uuid,
    lease_until timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((in_flight IS NULL) = (lease_until IS NULL))
);
CREATE TABLE agent_meta.messages (
    conversation_id uuid NOT NULL REFERENCES agent_meta.conversations ON DELETE CASCADE,
    message_id uuid NOT NULL,
    claim_id uuid NOT NULL,
    digest text NOT NULL,
    status text NOT NULL CHECK (status IN ('running','completed','failed','abandoned')),
    response jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (conversation_id, message_id)
);
CREATE TABLE agent_meta.query_history (
    query_id uuid PRIMARY KEY,
    conversation_id uuid NOT NULL,
    message_id uuid NOT NULL,
    trace_id text,
    record jsonb NOT NULL,
    started_at timestamptz NOT NULL,
    completed_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, message_id),
    FOREIGN KEY (conversation_id, message_id)
        REFERENCES agent_meta.messages(conversation_id, message_id) ON DELETE CASCADE
);
CREATE INDEX ON agent_meta.conversations(owner_hash, updated_at);
CREATE INDEX ON agent_meta.query_history(conversation_id, completed_at DESC);
