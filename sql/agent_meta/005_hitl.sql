CREATE SCHEMA IF NOT EXISTS agent_checkpoint AUTHORIZATION sqlshed_agent_meta;
REVOKE ALL ON SCHEMA agent_checkpoint FROM PUBLIC;
GRANT UPDATE (record, completed_at) ON agent_meta.query_history TO sqlshed_agent_meta;
