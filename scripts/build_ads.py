"""Create/verify three ADS views, preserving all 22 inputs and existing metrics."""
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
from dataquery_agent.ads import models,build_ads
from dataquery_agent.dws import build_dws


def reader_access(pg,*,grant=False):
    role='sqlshed_reader'
    attrs=pg.execute('SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,rolinherit FROM pg_roles WHERE rolname=%s',(role,)).fetchone()
    if attrs is None or any(attrs):raise ValueError('Learning role missing or privileged')
    if pg.execute('SELECT count(*) FROM pg_auth_members WHERE member=%s::regrole',(role,)).fetchone()[0]:
        raise ValueError('Unexpected reader membership')
    if grant:
        with pg.transaction():
            pg.execute('GRANT USAGE ON SCHEMA ads TO sqlshed_reader')
            for model in models()['models']:
                pg.execute(sql.SQL('GRANT SELECT ON {} TO sqlshed_reader').format(sql.Identifier(*model['name'].split('.'))))
    if pg.execute("SELECT has_database_privilege(%s,current_database(),'CREATE,TEMP')",(role,)).fetchone()[0]:
        raise ValueError('Reader can create database objects')
    for schema in ['ods','dwd','dim','dws','ads','public']:
        if pg.execute('SELECT has_schema_privilege(%s,%s,\'CREATE\')',(role,schema)).fetchone()[0]:
            raise ValueError('Reader can create business objects')
    # Covers all business tables/views and column-level grants, not only ADS.
    rows=pg.execute("""SELECT n.nspname||'.'||c.relname,
        has_table_privilege(%s,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
        OR has_any_column_privilege(%s,c.oid,'INSERT,UPDATE,REFERENCES'),c.relowner=%s::regrole
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname IN ('ods','dwd','dim','dws','ads') AND c.relkind IN ('r','v','m')""",(role,role,role)).fetchall()
    if any(write or owner for _,write,owner in rows):raise ValueError('Reader can write/drop business objects')
    reads={m['name']:pg.execute("SELECT has_table_privilege(%s,%s,'SELECT')",(role,m['name'])).fetchone()[0] for m in models()['models']}
    if not all(reads.values()):raise ValueError('Missing reader SELECT')
    if attrs!=pg.execute('SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,rolinherit FROM pg_roles WHERE rolname=%s',(role,)).fetchone():
        raise ValueError('Reader role attributes changed')
    return dict(ads_select=reads,role_attributes_unchanged=True,no_memberships=True,business_write=False,business_ownership=False,create_temp=False)


def reader_tcp():
    path=ROOT/'.env.sqlshed-reader'
    if stat.S_IMODE(path.stat().st_mode)!=0o600:raise ValueError('Unsafe credentials mode')
    env=dotenv_values(path)
    with psycopg.connect(host=env['SQLSHED_HOST'],port=int(env['SQLSHED_PORT']),dbname=env['SQLSHED_DATABASE'],user=env['SQLSHED_USER'],password=env['SQLSHED_PASSWORD'],autocommit=True) as pg:
        for model in models()['models']:
            pg.execute(sql.SQL('SELECT * FROM {} LIMIT 1').format(sql.Identifier(*model['name'].split('.')))).fetchone()
        pg.execute('SET default_transaction_read_only=off')
        for statement in ['UPDATE ads.payment_anomaly_order SET order_id=order_id WHERE false',
                          'DELETE FROM ads.payment_anomaly_order WHERE false',
                          'INSERT INTO ads.payment_anomaly_order(order_id) SELECT 0 WHERE false',
                          'UPDATE dwd.order_fact SET order_id=order_id WHERE false',
                          'CREATE TABLE ads.reader_probe(id integer)',
                          'CREATE TEMP TABLE reader_probe(id integer)']:
            try:
                with pg.transaction():
                    pg.execute(statement)
                    raise ValueError('Reader write unexpectedly allowed')
            except (psycopg.errors.InsufficientPrivilege,psycopg.errors.ObjectNotInPrerequisiteState) as error:
                if isinstance(error,psycopg.errors.ObjectNotInPrerequisiteState) and not statement.startswith(('UPDATE ads.','INSERT INTO ads.','DELETE FROM ads.')):
                    raise
    return dict(tcp_select_views=3,view_dml_denied=True,base_table_zero_row_update_acl_denied=True,create_and_temp_denied=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',action='store_true');parser.add_argument('--check-only',action='store_true')
    parser.add_argument('--grant-reader-select',action='store_true')
    args=parser.parse_args()
    if args.check_only and args.grant_reader_select:raise ValueError('Read-only mode cannot grant')
    if args.plan:
        print('\n'.join(m['name'] for m in models()['models']));return
    logging.basicConfig(level=logging.INFO,format='%(message)s')
    env=dotenv_values(ROOT/'.env');config=dict(host='127.0.0.1',port=5433,user=env['POSTGRES_USER'],password=env['POSTGRES_PASSWORD'])
    before=olist_snapshot(config)
    if before['orders']!=99441:raise ValueError('Olist order baseline changed')
    with psycopg.connect(**config,dbname='sqlshed',autocommit=True) as pg:
        prerequisite=build_dws(pg,check_only=True)
        first=build_ads(pg,check_only=args.check_only)
        repeated=build_ads(pg,check_only=True)
        actions={n:r.pop('action') for n,r in first['models'].items()}
        for r in repeated['models'].values():r.pop('action')
        if first!=repeated:raise ValueError('ADS repeat changed results')
        reader=reader_access(pg,grant=args.grant_reader_select)
    reader['tcp']=reader_tcp()
    after=olist_snapshot(config)
    if before!=after:raise ValueError('Olist source changed')
    paths=['src/dataquery_agent/ads.py','scripts/build_ads.py','warehouse/ads_models.json','sql/warehouse/ads_checks.sql',
           'sql/warehouse/ground_truth/ads/models.sql','metrics/warehouse_catalog_v1.json']
    paths += [str(p.relative_to(ROOT)) for folder in ('sql/warehouse/ads','sql/warehouse/ddl/ads') for p in (ROOT/folder).glob('*.sql')]
    report=dict(status='accepted_ads',executed_at_utc=datetime.now(timezone.utc).isoformat(),
        tested_base_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        tested_artifact_sha256={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths},
        first_actions=actions,repeat_verified=True,validation=first,reader=reader,
        dws_metric_comparisons=sum(r['checks'] for r in prerequisite['metrics'].values()),
        olist_before=before,olist_after=after,limitations=['fixed exclusive cutoff','no Agent changes','26 available / 4 deferred / 8 unsupported unchanged'])
    atomic_json(ROOT/'reports/warehouse/ads_acceptance.json',report)
    print(json.dumps(dict(rows={n:r['rows'] for n,r in first['models'].items()},ads_comparisons=first['ground_truth_comparisons'],dws_comparisons=report['dws_metric_comparisons'],repeat_verified=True),ensure_ascii=False))


if __name__=='__main__':
    try:main()
    except Exception as error:
        atomic_json(ROOT/'data/staging/warehouse/ads_failure.json',dict(status='failed',error_type=type(error).__name__))
        raise
