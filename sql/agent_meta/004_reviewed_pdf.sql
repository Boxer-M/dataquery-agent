ALTER TABLE agent_meta.knowledge_documents DROP CONSTRAINT IF EXISTS knowledge_documents_source_type_check;
ALTER TABLE agent_meta.knowledge_documents ADD CONSTRAINT knowledge_documents_source_type_check
    CHECK (source_type IN ('catalog','technical','business_rule','synthetic_fixture','reviewed_pdf')
       AND (source_type <> 'reviewed_pdf' OR owner_scope <> ''));
ALTER TABLE agent_meta.knowledge_versions ADD COLUMN IF NOT EXISTS pdf_review jsonb;
