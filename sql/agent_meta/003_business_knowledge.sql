CREATE SCHEMA IF NOT EXISTS rag_vector;
REVOKE ALL ON SCHEMA rag_vector FROM PUBLIC;
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA rag_vector;
CREATE TABLE IF NOT EXISTS agent_meta.knowledge_documents (
    document_id text PRIMARY KEY,
    owner_scope text NOT NULL,
    source_type text NOT NULL CHECK (source_type IN ('catalog','technical','business_rule','synthetic_fixture')),
    source_uri text NOT NULL,
    active_version text,
    active_index text,
    deleted_at timestamptz,
    UNIQUE (owner_scope, source_type, source_uri),
    CHECK ((active_version IS NULL) = (active_index IS NULL))
);
CREATE TABLE IF NOT EXISTS agent_meta.knowledge_versions (
    document_id text REFERENCES agent_meta.knowledge_documents,
    document_version text,
    title text NOT NULL,
    content_hash text NOT NULL,
    topic text,
    authority_level smallint NOT NULL CHECK (authority_level BETWEEN 1 AND 4),
    effective_date date,
    expires_on date,
    imported_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (document_id, document_version),
    CHECK (expires_on IS NULL OR (effective_date IS NOT NULL AND expires_on > effective_date))
);
CREATE TABLE IF NOT EXISTS agent_meta.knowledge_chunks (
    chunk_id text PRIMARY KEY,
    document_id text NOT NULL,
    document_version text NOT NULL,
    index_version text NOT NULL,
    chunk_index integer NOT NULL CHECK (chunk_index >= 0),
    chunk_text text NOT NULL,
    heading_path text[] NOT NULL,
    source_location text NOT NULL,
    content_hash text NOT NULL,
    embedding_model text NOT NULL,
    embedding_version text NOT NULL,
    embedding rag_vector.vector(768) NOT NULL,
    last_indexed_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (document_id, document_version)
        REFERENCES agent_meta.knowledge_versions(document_id, document_version),
    UNIQUE (document_id, document_version, index_version, chunk_index)
);

ALTER TABLE agent_meta.knowledge_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_meta.knowledge_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_meta.knowledge_chunks ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS knowledge_admin ON agent_meta.knowledge_documents;
CREATE POLICY knowledge_admin ON agent_meta.knowledge_documents TO sqlshed_knowledge_admin USING (true);
DROP POLICY IF EXISTS knowledge_admin ON agent_meta.knowledge_versions;
CREATE POLICY knowledge_admin ON agent_meta.knowledge_versions TO sqlshed_knowledge_admin USING (true);
DROP POLICY IF EXISTS knowledge_admin ON agent_meta.knowledge_chunks;
CREATE POLICY knowledge_admin ON agent_meta.knowledge_chunks TO sqlshed_knowledge_admin USING (true);

DROP POLICY IF EXISTS knowledge_visible ON agent_meta.knowledge_documents;
CREATE POLICY knowledge_visible ON agent_meta.knowledge_documents FOR SELECT TO sqlshed_knowledge_reader
    USING (deleted_at IS NULL AND active_version IS NOT NULL
       AND (owner_scope = '' OR owner_scope = current_setting('agent.owner_scope', true))
       AND (source_type <> 'synthetic_fixture' OR
            (owner_scope <> '' AND current_setting('agent.synthetic', true) = 'on')));
DROP POLICY IF EXISTS knowledge_visible ON agent_meta.knowledge_versions;
CREATE POLICY knowledge_visible ON agent_meta.knowledge_versions FOR SELECT TO sqlshed_knowledge_reader
    USING (EXISTS (SELECT 1 FROM agent_meta.knowledge_documents d
        WHERE d.document_id = knowledge_versions.document_id AND d.active_version = knowledge_versions.document_version));
DROP POLICY IF EXISTS knowledge_visible ON agent_meta.knowledge_chunks;
CREATE POLICY knowledge_visible ON agent_meta.knowledge_chunks FOR SELECT TO sqlshed_knowledge_reader
    USING (EXISTS (SELECT 1 FROM agent_meta.knowledge_documents d
        WHERE d.document_id = knowledge_chunks.document_id AND d.active_version = knowledge_chunks.document_version
          AND d.active_index = knowledge_chunks.index_version));
