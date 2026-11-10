-- check: columns
SELECT a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull
FROM pg_attribute a WHERE a.attrelid=%s::regclass
AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum;

-- check: constraints
SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid=%s::regclass ORDER BY 1;

-- check: kind
SELECT relkind,relrowsecurity FROM pg_class WHERE oid=%s::regclass;

-- check: differences
WITH expected AS ({projection})
SELECT count(*) FROM (
 (SELECT * FROM expected EXCEPT ALL SELECT * FROM {target})
 UNION ALL
 (SELECT * FROM {target} EXCEPT ALL SELECT * FROM expected)
) differences;

-- check: summary
SELECT count(*),count(DISTINCT {key}),count(*) FILTER(WHERE {key} IS NULL) FROM {target};

-- check: flags
SELECT count(*) FROM {target} WHERE {flag};

-- check: indexes
SELECT c.relname,pg_get_indexdef(i.indexrelid),i.indisvalid
FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid WHERE i.indrelid=%s::regclass;
