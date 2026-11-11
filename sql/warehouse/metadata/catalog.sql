-- check: relations
SELECT n.nspname || '.' || c.relname AS name, c.relkind AS kind, c.relrowsecurity AS rls,
       has_schema_privilege(n.oid, 'USAGE') AS schema_usage,
       coalesce(has_schema_privilege(to_regrole(%(metric_role)s), n.oid, 'USAGE'), false) AS metric_schema_usage
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname || '.' || c.relname = ANY(%(names)s)
ORDER BY 1;

-- check: columns
SELECT n.nspname || '.' || c.relname AS name, a.attname AS column,
       format_type(a.atttypid, a.atttypmod) AS type, NOT a.attnotnull AS nullable,
       has_column_privilege(c.oid, a.attnum, 'SELECT') AS readable,
       coalesce(has_column_privilege(to_regrole(%(metric_role)s), c.oid, a.attnum, 'SELECT'), false) AS metric_readable
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname || '.' || c.relname = ANY(%(names)s) AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY 1, a.attnum;

-- check: constraints
SELECT n.nspname || '.' || c.relname AS name, k.contype AS kind,
       pg_get_constraintdef(k.oid) AS definition, k.convalidated AS validated,
       ARRAY(SELECT a.attname::text FROM unnest(k.conkey) WITH ORDINALITY AS key(num, pos)
             JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = key.num ORDER BY key.pos) AS columns,
       tn.nspname || '.' || tc.relname AS target,
       ARRAY(SELECT a.attname::text FROM unnest(k.confkey) WITH ORDINALITY AS key(num, pos)
             JOIN pg_attribute a ON a.attrelid = tc.oid AND a.attnum = key.num ORDER BY key.pos) AS target_columns
FROM pg_constraint k
JOIN pg_class c ON c.oid = k.conrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_class tc ON tc.oid = k.confrelid
LEFT JOIN pg_namespace tn ON tn.oid = tc.relnamespace
WHERE n.nspname || '.' || c.relname = ANY(%(names)s) AND k.contype IN ('p','u','f','c','x')
ORDER BY 1, k.conname;
