CREATE CONSTRAINT dq_lineage_identity IF NOT EXISTS
FOR (n:DQLineage) REQUIRE (n.version,n.id) IS UNIQUE
