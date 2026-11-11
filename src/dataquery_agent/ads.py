"""Three ordinary application views; immutable source snapshot, no replacement DDL."""
import hashlib
import json
import logging
import re
from datetime import datetime

from psycopg import sql
from .metrics import ROOT, read_queries
from .dwd import build_transaction, relation_identifier
from .dws import all_inputs, models as dws_models, projection

MANIFEST=ROOT/'warehouse/ads_models.json'
LOGGER=logging.getLogger(__name__)


def models():
    manifest=json.loads(MANIFEST.read_text())
    if manifest['as_of']!=dws_models()['as_of']:
        raise ValueError('ADS and accepted DWS observation cutoffs differ')
    seen=set()
    for model in manifest['models']:
        name=model['name']
        path=(ROOT/model['sql']).resolve()
        if not re.fullmatch(r'ads\.[a-z_]+',name) or name in seen or not path.is_relative_to(ROOT) or not path.is_file():
            raise ValueError('Invalid ADS model')
        if not set(model['keys'])<={c['name'] for c in model['columns']}:
            raise ValueError('Missing ADS grain columns')
        seen.add(name)
    if len(seen)!=3:raise ValueError('Expected three ADS application views')
    return manifest


def inputs(pg):
    result=all_inputs(pg)
    for model in dws_models()['models']:
        digest=hashlib.sha256()
        with pg.cursor() as cur:
            cur.execute(sql.SQL('SELECT * FROM {} ORDER BY {}').format(relation_identifier(model['name']),
                sql.SQL(',').join(map(sql.Identifier,model['keys']))))
            while rows:=cur.fetchmany(2000):
                for row in rows:digest.update((json.dumps(row,default=str,separators=(',',':'))+'\n').encode())
        result[model['name']]=digest.hexdigest()
    return result


def definition_hash(pg,name):
    definition=pg.execute('SELECT pg_get_viewdef(%s::regclass,true)',(name,)).fetchone()[0]
    return hashlib.sha256(definition.encode()).hexdigest()


def create_view(pg,model,query):
    """No OR REPLACE: existing or incompatible objects are validated, never rewritten."""
    name=model['name'];exists=pg.execute('SELECT to_regclass(%s)',(name,)).fetchone()[0]
    if not exists:
        pg.execute('CREATE SCHEMA IF NOT EXISTS ads')
        ddl=sql.SQL((ROOT/model['ddl']).read_text()).format(projection=sql.SQL(query))
        pg.execute(ddl)
        stamp=dict(sql_sha256=hashlib.sha256(query.encode()).hexdigest(),definition_sha256=definition_hash(pg,name))
        pg.execute(sql.SQL('COMMENT ON VIEW {} IS {}').format(relation_identifier(name),sql.Literal(json.dumps(stamp,sort_keys=True))))
    return 'verified_existing' if exists else 'created'


def validate_view(pg,model,query,reference,as_of):
    checks=read_queries(ROOT/'sql/warehouse/quality/relation.sql');name=model['name']
    if pg.execute(checks['kind'],(name,)).fetchone()!=('v',False):
        raise ValueError('ADS target is not an ordinary view: '+name)
    columns=[(n,t) for n,t,_ in pg.execute(checks['columns'],(name,))]
    if columns!=[(c['name'],c['type']) for c in model['columns']]:raise ValueError('ADS columns mismatch: '+name)
    if pg.execute('SELECT reloptions FROM pg_class WHERE oid=%s::regclass',(name,)).fetchone()[0]!=['security_barrier=true']:
        raise ValueError('ADS view options conflict: '+name)
    expected=dict(sql_sha256=hashlib.sha256(query.encode()).hexdigest(),definition_sha256=definition_hash(pg,name))
    stamp=pg.execute('SELECT obj_description(%s::regclass)',(name,)).fetchone()[0]
    if stamp!=json.dumps(expected,sort_keys=True):raise ValueError('ADS definition conflict; refusing replacement: '+name)
    for ref in (query,reference):
        difference=sql.SQL(checks['differences']).format(projection=sql.SQL(ref.strip().rstrip(';')),target=relation_identifier(name))
        if pg.execute(difference,{'as_of':as_of} if '%(as_of)' in ref else None).fetchone()[0]:
            raise ValueError('Independent ADS/source full-row mismatch: '+name)
    keys=sql.SQL(',').join(map(sql.Identifier,model['keys']))
    nulls=sql.SQL(' OR ').join(sql.SQL('{} IS NULL').format(sql.Identifier(k)) for k in model['keys'])
    rows,unique,missing=pg.execute(sql.SQL('SELECT count(*),count(DISTINCT ROW({})),count(*) FILTER(WHERE {}) FROM {}').format(keys,nulls,relation_identifier(name))).fetchone()
    if rows!=unique or missing:raise ValueError('ADS grain mismatch: '+name)
    return dict(rows=rows,unique_keys=unique,null_keys=missing,independent_ods_differences=0,definition_sha256=expected['definition_sha256'])


def verify_windows(pg,as_of):
    """Aggregate to requested window first; never average stored row rates."""
    source=read_queries(ROOT/'sql/warehouse/ground_truth/ads/models.sql')
    target={m['name'].split('.')[1]:'SELECT * FROM '+m['name'] for m in models()['models']}
    windows=[('full','2023-01-01','2026-01-01'),('january_2025','2025-01-01','2025-02-01'),
             ('boundary_day','2025-01-31','2025-02-01'),('empty','2022-01-01','2022-02-01')]
    summaries=read_queries(ROOT/'sql/warehouse/ads_checks.sql')
    results={}
    channel=pg.execute('SELECT min(channel_code) FROM dim.channel').fetchone()[0]
    for window,start,end in windows:
        for name in source:
            for scoped in (False,True):
                params=dict(start=datetime.fromisoformat(start),end=datetime.fromisoformat(end),as_of=as_of,channel=channel if scoped else None)
                query=sql.SQL(summaries[name])
                left=pg.execute(query.format(records=sql.SQL(source[name].strip().rstrip(';'))),params).fetchall()
                cur=pg.execute(query.format(records=sql.SQL(target[name])),params);right=cur.fetchall()
                if left!=right:raise ValueError('ADS window Ground Truth mismatch: '+window+'/'+name)
                results[window+'/'+name+('/channel' if scoped else '')]=dict(zip([c.name for c in cur.description],right[0]))
    return results


def build_ads(pg,*,check_only=False):
    manifest=models();as_of=datetime.fromisoformat(manifest['as_of'])
    def operation():
        if check_only:pg.execute('SET TRANSACTION READ ONLY')
        for model in manifest['models']:
            for name in model['depends_on']:
                if not pg.execute('SELECT to_regclass(%s)',(name,)).fetchone()[0]:raise ValueError('Missing ADS dependency: '+name)
        original=inputs(pg);result={}
        references=read_queries(ROOT/'sql/warehouse/ground_truth/ads/models.sql')
        for model in manifest['models']:
            query=projection(pg,(ROOT/model['sql']).read_text(),as_of).strip().rstrip(';')
            action='verified_existing' if check_only else create_view(pg,model,query)
            result[model['name']]=validate_view(pg,model,query,references[model['name'].split('.')[1]],as_of)
            result[model['name']]['action']=action
            LOGGER.info('ADS: %s %s, %s rows',model['name'],action,result[model['name']]['rows'])
        windows=verify_windows(pg,as_of)
        if original!=inputs(pg):raise ValueError('Protected source content changed')
        return dict(models=result,as_of=as_of,input_fingerprints=original,input_unchanged=True,
                    windows=windows,ground_truth_comparisons=len(windows),metric_catalog_unchanged=True)
    return build_transaction(pg,operation)
