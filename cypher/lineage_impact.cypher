MATCH p=(f:DQLineage {version:$version,id:$field})-[:IMPACTS*1..5]->(m:DQLineage {kind:'metric',version:$version})
WHERE all(n IN nodes(p) WHERE n.version=$version)
  AND all(r IN relationships(p) WHERE r.version=$version
      AND r.impact_type IN $types AND r.impact_type=relationships(p)[0].impact_type)
RETURN m.id AS metric_id, relationships(p)[0].impact_type AS impact_type,
       [n IN nodes(p) | n.id] AS path,
       [r IN relationships(p) | properties(r)] AS evidence
ORDER BY metric_id,impact_type
LIMIT 20
