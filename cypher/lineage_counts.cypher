MATCH (n:DQLineage {version:$version})
OPTIONAL MATCH (n)-[r:IMPACTS {version:$version}]->()
RETURN count(DISTINCT n) AS nodes,count(r) AS relationships
