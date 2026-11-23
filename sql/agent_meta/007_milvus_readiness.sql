-- Replica readiness only; knowledge reader remains read-only.
CREATE TABLE IF NOT EXISTS agent_meta.milvus_readiness (
    document_id text PRIMARY KEY REFERENCES agent_meta.knowledge_documents,
    document_version text NOT NULL,
    index_version text NOT NULL,
    embedding_model text NOT NULL,
    embedding_version text NOT NULL,
    expected_ids text[] NOT NULL,
    attempt_id text NOT NULL,
    state text NOT NULL CHECK (state IN ('syncing','ready','failed')),
    updated_at timestamptz NOT NULL DEFAULT now()
);
REVOKE ALL ON agent_meta.milvus_readiness FROM PUBLIC;
ALTER TABLE agent_meta.milvus_readiness ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS milvus_admin ON agent_meta.milvus_readiness;
CREATE POLICY milvus_admin ON agent_meta.milvus_readiness TO sqlshed_knowledge_admin USING (true);
DROP POLICY IF EXISTS milvus_visible ON agent_meta.milvus_readiness;
CREATE POLICY milvus_visible ON agent_meta.milvus_readiness FOR SELECT TO sqlshed_knowledge_reader
    USING (EXISTS (SELECT 1 FROM agent_meta.knowledge_documents d
        WHERE d.document_id=milvus_readiness.document_id));
GRANT SELECT ON agent_meta.milvus_readiness TO sqlshed_knowledge_reader;
GRANT SELECT, INSERT, UPDATE ON agent_meta.milvus_readiness TO sqlshed_knowledge_admin;
