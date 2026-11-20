"""Reviewed, hash-locked SQL evidence for one source field and three metrics."""
from hashlib import sha256
import json

from sqlglot import exp, parse_one

from .metrics import ROOT, read_queries
from .metadata_service import manifest_models

FIELD = 'ods.order_items.discount_amount'
METRICS = {'item_net_sales_amount':'item_net_sales_amount',
           'item_discount_amount':'item_discount_amount', 'order_item_count':'order_item_count'}
MANIFEST = ROOT/'warehouse/lineage_discount_v1.json'


class LineageError(ValueError):
    pass


def reviewed_paths(data, field, types):
    """Expected coverage for detecting incomplete graph snapshots, never a query fallback."""
    kinds={node['id']:node['kind'] for node in data['nodes']}
    def walk(path,kind,evidence):
        if kinds.get(path[-1])=='metric':
            yield {'metric_id':path[-1],'impact_type':kind,'path':path,'evidence':evidence}
        if len(path)<6:
            for edge in data['edges']:
                if edge['from_id']==path[-1] and edge['impact_type']==kind and edge['to_id'] not in path:
                    yield from walk(path+[edge['to_id']],kind,evidence+[edge])
    return [row for kind in types for row in walk([field],kind,[])]


def bundle():
    review=json.loads(MANIFEST.read_text())
    for name,digest in review['reviewed_inputs'].items():
        if sha256((ROOT/name).read_bytes()).hexdigest()!=digest:
            raise LineageError('lineage_source_changed')
    version=review['version']+':'+sha256(MANIFEST.read_bytes()).hexdigest()[:16]
    models=manifest_models()
    dwd_name,dws_name='dwd.order_item_fact','dws.product_sales_day'
    paths={name:models[name]['transform'] for name in (dwd_name,dws_name)}
    sqls={name:(ROOT/path).read_text() for name,path in paths.items()}
    trees={name:parse_one(sql,read='postgres') for name,sql in sqls.items()}
    nodes={FIELD:{'id':FIELD,'kind':'field'}}
    edges=[]
    def node(name,kind='field'):
        nodes[name]={'id':name,'kind':kind}
    def edge(start,end,impact,path,expression,needle=None):
        raw=(ROOT/path).read_text()
        lines=raw.splitlines()
        start_line=next(i+1 for i,line in enumerate(lines) if needle in line) if needle else 1
        end_line=start_line if needle else len(lines)
        if needle and needle.startswith('-- metric:'):
            end_line=next((i for i in range(start_line,len(lines)) if lines[i].startswith('-- metric:')),len(lines))
        evidence={'from_id':start,'to_id':end,'impact_type':impact,'source_file':path,
                  'line_start':start_line,'line_end':end_line,
                  'expression':expression,'file_sha256':review['reviewed_inputs'][path],
                  'source_commit':review['source_commit'],'source_version':version}
        evidence['id']=sha256(f'{start}|{end}|{impact}'.encode()).hexdigest()
        edges.append(evidence)
    reachable={FIELD}
    for name,columns in ((dwd_name,('net_item_amount','discount_amount')),
                         (dws_name,tuple(METRICS.values()))):
        tree=trees[name]
        aliases={table.alias_or_name:f'{table.db}.{table.name}' for table in tree.find_all(exp.Table)}
        outputs={e.alias_or_name:e for e in tree.selects}
        for column in columns:
            if column not in {c['name'] for c in models[name]['columns']}:
                raise LineageError('lineage_model_mismatch')
            target=f'{name}.{column}'
            node(target)
            expression=outputs[column]
            for ref in expression.find_all(exp.Column):
                source=f'{aliases.get(ref.table)}.{ref.name}'
                if source in reachable:
                    edge(source,target,'VALUE',paths[name],expression.sql(dialect='postgres'),column)
                    reachable.add(target)
    templates=read_queries(ROOT/'sql/warehouse/dws_metrics/dws.sql')
    catalog={m['metric_id']:m for m in json.loads((ROOT/'metrics/warehouse_catalog_v1.json').read_text())['metrics']}
    for mid,column in METRICS.items():
        template=templates[mid]
        tree=parse_one(template.replace('{bucket}',"'all'").replace('{group_by}',''),read='postgres')
        expression=next(e for e in tree.selects if e.alias_or_name=='value')
        if {c.name for c in expression.find_all(exp.Column)}!={column} or dws_name not in {
                f'{t.db}.{t.name}' for t in tree.find_all(exp.Table)}:
            raise LineageError('lineage_metric_mismatch')
        node(mid,'metric')
        nodes[mid]['name']=catalog[mid]['name_zh']
        edge(f'{dws_name}.{column}',mid,'VALUE','sql/warehouse/dws_metrics/dws.sql',
             expression.sql(dialect='postgres'),f'-- metric: {mid}')
    # A failed whole-relation SELECT blocks rebuilding every downstream output;
    # it does not imply that every output's value depends on the source discount.
    for name in (dwd_name,dws_name):
        node('build:'+name,'task')
    edge(FIELD,'build:'+dwd_name,'DELETE',paths[dwd_name],sqls[dwd_name])
    if dwd_name not in models[dws_name]['depends_on']:
        raise LineageError('lineage_dependency_mismatch')
    edge('build:'+dwd_name,'build:'+dws_name,'DELETE',paths[dws_name],sqls[dws_name])
    for mid in METRICS:
        edge('build:'+dws_name,mid,'DELETE','sql/warehouse/dws_metrics/dws.sql',templates[mid],f'-- metric: {mid}')
    return {'version':version,'nodes':list(nodes.values()),'edges':edges,'coverage':list(METRICS)}
