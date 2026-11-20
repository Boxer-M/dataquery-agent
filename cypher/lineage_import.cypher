UNWIND $nodes AS item
MERGE (n:DQLineage {version:$version, id:item.id})
SET n.kind=item.kind, n.name=item.name
WITH count(n) AS ignored
UNWIND $edges AS edge
MATCH (a:DQLineage {version:$version,id:edge.from_id})
MATCH (b:DQLineage {version:$version,id:edge.to_id})
MERGE (a)-[r:IMPACTS {version:$version,id:edge.id}]->(b)
SET r += edge
RETURN count(r) AS relationships
