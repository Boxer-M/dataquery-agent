"""Build six DWS models, validate metrics, and optionally grant validated SELECT access."""
import argparse
import hashlib
import json
import logging
import stat
import subprocess
from datetime import datetime,timezone

import psycopg
from psycopg import sql
from dotenv import dotenv_values
from build_warehouse import atomic_json,olist_snapshot
from dataquery_agent.metrics import ROOT
from dataquery_agent.dws import build_dws,models


def reader_check(pg,grant=False):
    role='sqlshed_reader'
    attrs=pg.execute('SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,rolinherit FROM pg_roles WHERE rolname=%s',(role,)).fetchone()
    if attrs is None:
        if grant:raise ValueError('Learning role absent; create separately, never implicitly')
        return {'present':False}
    if any(attrs):raise ValueError('Learning role unexpectedly privileged')
    if grant:
        with pg.transaction():
            pg.execute(sql.SQL('GRANT USAGE ON SCHEMA dws TO {}').format(sql.Identifier(role)))
            for model in models()['models']:
                pg.execute(sql.SQL('GRANT SELECT ON TABLE {} TO {}').format(sql.Identifier(*model['name'].split('.')),sql.Identifier(role)))
    if pg.execute('SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,rolinherit FROM pg_roles WHERE rolname=%s',(role,)).fetchone()!=attrs:
        raise ValueError('Learning role attributes changed')
    if pg.execute('SELECT count(*) FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=%s)',(role,)).fetchone()[0]:
        raise ValueError('Learning role membership unexpectedly expanded')
    if pg.execute("SELECT has_database_privilege(%s,current_database(),'CREATE,TEMPORARY')",(role,)).fetchone()[0]:
        raise ValueError('Learning role can create objects')
    if pg.execute("SELECT has_schema_privilege(%s,'dws','CREATE')",(role,)).fetchone()[0]:
        raise ValueError('Learning role can create DWS objects')
    access={}
    for model in models()['models']:
        name=model['name']
        read,write=pg.execute("SELECT has_table_privilege(%s,%s,'SELECT'),has_table_privilege(%s,%s,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') OR has_any_column_privilege(%s,%s,'INSERT,UPDATE,REFERENCES')",(role,name)*3).fetchone()
        if write or (grant and not read):raise ValueError('Invalid DWS reader table permissions')
        access[name]=bool(read)
    return dict(present=True,role_attributes_unchanged=True,memberships=0,dws_select=access,dws_write=False,create_temp=False)


def verify_reader_tcp():
    path=ROOT/'.env.sqlshed-reader'
    if stat.S_IMODE(path.stat().st_mode)!=0o600:raise ValueError('Unsafe reader credential file mode')
    env=dotenv_values(path)
    with psycopg.connect(host=env['SQLSHED_HOST'],port=int(env['SQLSHED_PORT']),dbname=env['SQLSHED_DATABASE'],user=env['SQLSHED_USER'],password=env['SQLSHED_PASSWORD'],autocommit=True) as pg:
        for model in models()['models']:
            pg.execute(sql.SQL('SELECT 1 FROM {} LIMIT 1').format(sql.Identifier(*model['name'].split('.')))).fetchone()
        pg.execute('SET default_transaction_read_only=off')
        for query in ['UPDATE dws.order_day SET order_count=order_count WHERE false',
                      "INSERT INTO dws.order_day(order_date,channel,status,order_count) SELECT current_date,'probe','probe',0 WHERE false",
                      'DELETE FROM dws.order_day WHERE false']:
            try:
                with pg.transaction():
                    pg.execute(query)
                    raise ValueError('Learning role write probe unexpectedly allowed')
            except psycopg.errors.InsufficientPrivilege:
                pass
    return dict(tcp_select_tables=6,zero_row_writes_denied=True,password_printed=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',action='store_true')
    parser.add_argument('--check-only',action='store_true')
    parser.add_argument('--grant-reader-select',action='store_true',help='Grant only current six tables after acceptance')
    args=parser.parse_args()
    if args.check_only and args.grant_reader_select:raise ValueError('Read-only validation cannot grant privileges')
    logging.basicConfig(level=logging.INFO,format='%(message)s')
    if args.plan:
        print('\n'.join(m['name'] for m in models()['models']));return
    env=dotenv_values(ROOT/'.env');config=dict(host='127.0.0.1',port=5433,user=env['POSTGRES_USER'],password=env['POSTGRES_PASSWORD'])
    before=olist_snapshot(config)
    with psycopg.connect(**config,dbname='sqlshed',autocommit=True) as pg:
        first=build_dws(pg,check_only=args.check_only)
        repeated=build_dws(pg,check_only=True)
        actions={n:r.pop('action') for n,r in first['models'].items()}
        for r in repeated['models'].values():
            if r.pop('action')!='verified_existing':raise ValueError('Unexpected repeat write')
        if first!=repeated:raise ValueError('Repeat DWS validation changed results')
        reader=reader_check(pg,grant=args.grant_reader_select)
    after=olist_snapshot(config)
    if before!=after:raise ValueError('Olist orders changed')
    if args.grant_reader_select:reader['tcp']=verify_reader_tcp()
    paths=['src/dataquery_agent/dws.py','src/dataquery_agent/dwd.py','src/dataquery_agent/warehouse.py',
           'scripts/build_dws.py','scripts/build_warehouse.py','warehouse/dws_models.json','warehouse/dws_metrics.json',
           'metrics/ecommerce_v1.json','sql/metrics/ecommerce_v1.sql']
    paths += [str(p.relative_to(ROOT)) for folder in ['sql/warehouse/ddl/dws','sql/warehouse/transform/dws','sql/warehouse/dws_metrics','sql/warehouse/ground_truth/dws'] for p in (ROOT/folder).glob('*.sql')]
    paths+=['sql/warehouse/quality/dws.sql','sql/warehouse/quality/relation.sql']
    report=dict(status='accepted_dws_only',executed_at_utc=datetime.now(timezone.utc).isoformat(),
       tested_base_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
       tested_artifact_sha256={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in sorted(paths)},
       first_actions=actions,repeat_verified=True,validation=first,olist_before=before,olist_after=after,reader=reader,
       limitations=['day-aligned windows only','fixed cohort cutoff','no ADS or Agent change','4 extensions deferred; 8 unsupported unchanged'])
    atomic_json(ROOT/'reports/warehouse/dws_acceptance.json',report)
    print(json.dumps(dict(rows={n:r['rows'] for n,r in first['models'].items()},extensions_verified=len(first['available_extensions']),repeat_verified=True,report='reports/warehouse/dws_acceptance.json'),ensure_ascii=False))


if __name__=='__main__':
    try:main()
    except Exception as error:
        atomic_json(ROOT/'data/staging/warehouse/dws_failure.json',dict(status='failed',type=type(error).__name__))
        raise
