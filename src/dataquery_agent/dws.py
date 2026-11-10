"""Six DWS tables only; exact snapshot summaries and independently checked metrics."""
import hashlib
import json
import logging
from datetime import time
from decimal import Decimal

from psycopg import sql
from .dwd import build_transaction, materialize_relation, validate_relation, relation_identifier
from .metrics import ROOT, read_queries, window_parameters
from .warehouse import BASELINE, load_models, content_fingerprint, source_fingerprints, validate_sources, validate_metrics, query_rows

MANIFEST=ROOT/'warehouse/dws_models.json'
REGISTRY=ROOT/'warehouse/dws_metrics.json'
LOGGER=logging.getLogger(__name__)
DEFERRED=('payment_attempted_order_count','multiple_payment_attempt_order_count',
          'payment_item_amount_mismatch_order_count','first_observed_ordering_customer_count')
GROUP_COLUMNS=dict(channel='channel',category='category_id',product='product_id',method='method',
                   status='status',customer='customer_id',country='country',loyalty='loyalty_tier')


def models():
    dependencies=[m['name'] for m in load_models()['models']]
    return load_models(MANIFEST,allowed_schemas=('dws',),external_dependencies=dependencies)


def projection(pg, template, as_of):
    return sql.SQL(template).format(as_of=sql.Literal(as_of)).as_string(pg)


def parameters(start,end,as_of,*,channel=None,category=None):
    params=window_parameters(start,end,as_of)
    if any(params[n].time()!=time.min for n in ('start','end')):
        raise ValueError('DWS requires day-aligned windows; use a controlled DWD query for partial days')
    params.update(channel=channel,category=category)
    return params


def metric_query(pg, template, group=None):
    if group is not None and group not in GROUP_COLUMNS:
        raise ValueError('Unsupported grouping dimension')
    bucket=sql.SQL('{}::text').format(sql.Identifier(GROUP_COLUMNS[group])) if group else sql.Literal('all')
    return sql.SQL(template.strip().rstrip(';')).format(bucket=bucket,
        output_bucket=sql.Identifier('bucket') if group else sql.Literal('all'),
        group_by=sql.SQL('GROUP BY 1') if group else sql.SQL(''))


def canonical(value):
    if isinstance(value,(int,Decimal)) and not isinstance(value,bool):
        return format(Decimal(value).normalize(),'f')
    return str(value) if value is not None else None


def numeric_equal(actual,expected):
    if actual is None or expected is None:
        return actual is expected
    return Decimal(str(actual))==Decimal(str(expected))


def compare_metric(pg,source,target,params,group=None):
    left=metric_query(pg,source,group);right=metric_query(pg,target,group)
    order=sql.SQL('SELECT * FROM ({}) checked ORDER BY 1 NULLS FIRST')
    lcur=pg.execute(order.format(left),params);columns=[c.name for c in lcur.description];expected=lcur.fetchall()
    rcur=pg.execute(order.format(right),params);actual=rcur.fetchall()
    if columns != [c.name for c in rcur.description] or actual!=expected:
        raise ValueError('Independent ODS / DWS metric mismatch')
    digest=hashlib.sha256(json.dumps([[canonical(v) for v in row] for row in expected],ensure_ascii=False).encode()).hexdigest()
    return dict(groups=len(expected),sha256=digest),[dict(zip(columns,row)) for row in actual]


def verify_metric_suite(pg,as_of,baseline):
    source=read_queries(ROOT/'sql/warehouse/dws_metrics/ods.sql')
    target=read_queries(ROOT/'sql/warehouse/dws_metrics/dws.sql')
    standard=read_queries(ROOT/'sql/metrics/ecommerce_v1.sql')
    registry=json.loads(REGISTRY.read_text())['metrics']
    if set(source)!=set(target) or set(source)!={m['metric_id'] for m in registry}:
        raise ValueError('Metric SQL/registry identifiers differ')
    category=pg.execute('SELECT min(category_id) FROM dim.product').fetchone()[0]
    channel=pg.execute('SELECT min(channel_code) FROM dim.channel').fetchone()[0]
    windows=[('full','2023-01-01','2026-01-01'),('january_2025','2025-01-01','2025-02-01'),
             ('boundary_day','2025-01-31','2025-02-01'),('empty','2022-01-01','2022-02-01')]
    evidence={m['metric_id']:dict(checks=0,windows={},dimensions={}) for m in registry}
    for window,start,end in windows:
        params=parameters(start,end,as_of.isoformat())
        LOGGER.info('DWS: validating %s, 26 metrics plus dimension cases',window)
        for spec in registry:
            mid=spec['metric_id'];record=evidence[mid]
            try:
                proof,values=compare_metric(pg,source[mid],target[mid],params)
                record['checks']+=1;record['windows'][window]=values
                if mid in standard:
                    ref=pg.execute(standard[mid],params).fetchone()
                    row=values[0]
                    if tuple(row[k] for k in row if k!='bucket')!=ref:
                        raise ValueError('Original approved SQL differs')
                    if baseline and window in ('full','january_2025'):
                        if not numeric_equal(row['value'],baseline['windows'][window]['values'][mid]):
                            raise ValueError('Approved original baseline differs')
                if window in ('full','january_2025'):
                    for group in spec['groups']:
                        proof,_=compare_metric(pg,source[mid],target[mid],params,group)
                        record['checks']+=1;record['dimensions'][window+'/'+group]=proof
                    variants=[dict(channel=channel)]
                    if spec['family'] in ('items','returns','cohort'): variants.append(dict(category=category))
                    for variant in variants:
                        scoped=dict(params,**variant)
                        proof,_=compare_metric(pg,source[mid],target[mid],scoped)
                        record['checks']+=1;record['dimensions'][window+'/'+next(iter(variant))+'_filter']=proof
            except Exception as error:
                raise ValueError(f'Metric validation failed: {window}/{mid}: {type(error).__name__}') from error
    return evidence


def all_inputs(pg):
    result=source_fingerprints(pg)
    for model in load_models()['models']:
        result[model['name']]=content_fingerprint(pg,model['name'],model['key'])
    return result


def build_dws(pg,*,baseline_path=BASELINE,check_only=False):
    manifest=models()
    as_of=window_parameters('2023-01-01','2026-01-01',manifest['as_of'])['as_of']
    baseline=json.loads(baseline_path.read_text()) if baseline_path else None

    def operation():
        if check_only:pg.execute('SET TRANSACTION READ ONLY')
        source=validate_sources(pg,baseline)
        for model in load_models()['models']:
            if not pg.execute('SELECT to_regclass(%s)',(model['name'],)).fetchone()[0]:
                raise ValueError('Missing A dependency: '+model['name'])
            validate_relation(pg,model,(ROOT/model['transform']).read_text())
        validate_metrics(pg,baseline)
        checks=read_queries(ROOT/'sql/warehouse/quality/dws.sql')
        end=pg.execute(checks['source_end']).fetchone()[0]
        if end is None or as_of<end:
            raise ValueError('Observation cutoff must include the full order-day cohort')
        original=all_inputs(pg);ads_before=pg.execute(checks['protected_objects']).fetchall()
        output={}
        reference=read_queries(ROOT/'sql/warehouse/ground_truth/dws/models.sql')
        diff=read_queries(ROOT/'sql/warehouse/quality/relation.sql')['differences']
        for model in manifest['models']:
            query=projection(pg,(ROOT/model['transform']).read_text(),as_of)
            if check_only:
                result=validate_relation(pg,model,query);result['action']='verified_existing'
            else:result=materialize_relation(pg,model,query,(ROOT/model['ddl']).read_text())
            independent=reference[model['name'].split('.')[1]].strip().rstrip(';')
            difference=sql.SQL(diff).format(projection=sql.SQL(independent),target=relation_identifier(model['name']))
            mismatches=pg.execute(difference,{'as_of':as_of} if '%(as_of)' in independent else None).fetchone()[0]
            if mismatches:raise ValueError('Independent source grain/measure mismatch: '+model['name'])
            result['independent_ods_differences']=mismatches;output[model['name']]=result
            LOGGER.info('DWS: %s %s, %s rows',model['name'],result['action'],result['rows'])
        if pg.execute(checks['relations']).fetchone()[0] or pg.execute(checks['cohort_bounds'],{'as_of':as_of}).fetchone()[0]:
            raise ValueError('DWS relationship or cohort bounds failed')
        verified=verify_metric_suite(pg,as_of,baseline)
        if original!=all_inputs(pg) or ads_before!=pg.execute(checks['protected_objects']).fetchall():
            raise ValueError('ODS/DWD/DIM or ADS objects changed')
        return dict(model_version=manifest['model_version'],metric_version='1.0.0',as_of=as_of,
            models=output,input_fingerprints=original,input_unchanged=True,ads_objects_unchanged=True,
            source_snapshot_id=hashlib.sha256(json.dumps(original,sort_keys=True).encode()).hexdigest(),
            source=source,totals=query_rows(pg,checks['totals'])[0],metrics=verified,
            available_extensions=[m for m in verified if m not in read_queries(ROOT/'sql/metrics/ecommerce_v1.sql')],
            deferred_extensions=list(DEFERRED))
    return build_transaction(pg,operation)
