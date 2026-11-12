CREATE TABLE IF NOT EXISTS agent_meta.query_asset_versions (
    asset_id text NOT NULL,
    implementation_version integer NOT NULL CHECK (implementation_version > 0),
    operation text NOT NULL,
    identity_hash text NOT NULL,
    definition jsonb NOT NULL,
    lifecycle_status text NOT NULL DEFAULT 'CANDIDATE'
        CHECK (lifecycle_status IN ('CANDIDATE','VALIDATED','PUBLISHED','STALE','RETIRED','REJECTED')),
    validation_evidence jsonb,
    supersedes_version integer,
    published_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (asset_id, implementation_version),
    UNIQUE (asset_id, identity_hash),
    FOREIGN KEY (asset_id, supersedes_version)
        REFERENCES agent_meta.query_asset_versions(asset_id, implementation_version),
    CHECK (supersedes_version IS NULL OR supersedes_version < implementation_version),
    CHECK (lifecycle_status NOT IN ('VALIDATED','PUBLISHED','STALE','RETIRED') OR validation_evidence IS NOT NULL),
    CHECK (lifecycle_status NOT IN ('PUBLISHED','STALE','RETIRED') OR published_at IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS query_asset_active_operation
    ON agent_meta.query_asset_versions(operation) WHERE lifecycle_status = 'PUBLISHED';

CREATE TABLE IF NOT EXISTS agent_meta.query_asset_events (
    event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    asset_id text NOT NULL,
    implementation_version integer NOT NULL,
    lifecycle_status text NOT NULL,
    actor text NOT NULL,
    evidence jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (asset_id, implementation_version)
        REFERENCES agent_meta.query_asset_versions(asset_id, implementation_version)
);
