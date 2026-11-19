-- No warehouse writes or additional business privileges.
CREATE TABLE IF NOT EXISTS agent_meta.preferences (
    owner_hash text PRIMARY KEY CHECK (owner_hash ~ '^[0-9a-f]{64}$'),
    channel text NOT NULL CHECK (channel IN ('web','mobile','partner','phone')),
    source text NOT NULL CHECK (source = 'explicit_chat'),
    version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
    updated_at timestamptz NOT NULL DEFAULT now()
);
REVOKE ALL ON agent_meta.preferences FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE, DELETE ON agent_meta.preferences TO sqlshed_agent_meta;
