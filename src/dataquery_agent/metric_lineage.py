"""Fixed Cypher metadata tool. No user-provided statements or warehouse writes."""
from base64 import b64encode
import json
import re
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
from uuid import uuid4

from dotenv import dotenv_values

from .lineage_evidence import FIELD, LineageError, bundle, reviewed_paths
from .metrics import ROOT


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Neo4jLocal:
    def __init__(self, url, auth):
        if url!='http://127.0.0.1:7474' or not isinstance(auth,str) or not auth.startswith('neo4j/'):
            raise LineageError('lineage_not_configured')
        self.url=url+'/db/neo4j/query/v2'
        self.auth='Basic '+b64encode(auth.replace('/',':',1).encode()).decode()

    def query(self, name, parameters, *, write=False):
        if name not in {'impact','counts','import','constraint'} or (name in {'import','constraint'})!=write:
            raise LineageError('lineage_query_denied')
        statement=(ROOT/f'cypher/lineage_{name}.cypher').read_text()
        payload=json.dumps({'statement':statement,'parameters':parameters,
                            'accessMode':'Write' if write else 'Read'}).encode()
        req=Request(self.url,data=payload,headers={'Authorization':self.auth,'Content-Type':'application/json'})
        try:
            with build_opener(ProxyHandler({}),NoRedirect()).open(req,timeout=8) as response:
                raw=response.read(1024*1024+1)
            if len(raw)>1024*1024:
                raise LineageError('lineage_response_limit')
            result=json.loads(raw)
            if result.get('errors'):
                raise LineageError('lineage_query_failed')
            data=result['data']
            return [dict(zip(data['fields'],row)) for row in data['values']]
        except (HTTPError,URLError,TimeoutError,OSError,ValueError,KeyError) as error:
            if isinstance(error,LineageError):
                raise
            raise LineageError('lineage_unavailable') from None


class LineageTool:
    def __init__(self, client):
        self.client=client

    @classmethod
    def local(cls):
        cfg=dotenv_values(ROOT/'.env.neo4j')
        return cls(Neo4jLocal(cfg.get('NEO4J_HTTP_URL'),cfg.get('NEO4J_AUTH')))

    def import_graph(self):
        data=bundle()
        self.client.query('constraint',{},write=True)
        self.client.query('import',data,write=True)
        return dict(self.client.query('counts',{'version':data['version']})[0],version=data['version'])

    def lookup(self, field, *, impact='both'):
        if not isinstance(field,str) or not re.fullmatch(r'[a-z_]+\.[a-z_]+\.[a-z_]+',field) or impact not in {'both','value','delete'}:
            return {'status':'unsupported','code':'invalid_lineage_request'}
        if field!=FIELD:
            return {'status':'unsupported','code':'field_not_indexed'}
        data=bundle()
        types=['VALUE','DELETE'] if impact=='both' else [impact.upper()]
        rows=self.client.query('impact',{'field':field,'version':data['version'],
            'types':types})
        if not rows:
            return {'status':'unsupported','code':'lineage_index_missing' if field==FIELD else 'field_not_indexed'}
        known={e['id']:e for e in data['edges']}
        for row in rows:
            path=row['path']
            if path[0]!=field or path[-1]!=row['metric_id'] or len(path)!=len(row['evidence'])+1:
                raise LineageError('lineage_evidence_invalid')
            for start,end,evidence in zip(path,path[1:],row['evidence']):
                expected=known.get(evidence.get('id'))
                if (not expected or any(evidence.get(k)!=v for k,v in expected.items())
                        or expected['from_id']!=start or expected['to_id']!=end
                        or expected['impact_type']!=row['impact_type']):
                    raise LineageError('lineage_evidence_invalid')
        key=lambda row:(row['metric_id'],row['impact_type'],tuple(row['path']))
        if len(rows)!=len({key(row) for row in rows}) or {key(row) for row in rows}!={
                key(row) for row in reviewed_paths(data,field,types)}:
            raise LineageError('lineage_index_incomplete')
        return {'status':'ok','field':field,'version':data['version'],'coverage':data['coverage'],'impacts':rows}


def route_lineage(question, capability, trace):
    if re.search(r'PDF|文档|扫描',question,re.I):
        return None  # An explicit document request keeps the existing knowledge path.
    if not re.search(r'影响|血缘',question) or not re.search(r'字段|[a-z_]+\.[a-z_]+\.[a-z_]+',question,re.I):
        return None
    trace['routing']={'rule':'field_impact'}
    fields=re.findall(r'(?<![\w.])[a-z_]+\.[a-z_]+\.[a-z_]+(?![\w.])',question,re.I)
    field=fields[0].lower() if len(fields)==1 else FIELD if not fields and '折扣字段' in question else None
    if field is None:
        return {'status':'clarify','code':'lineage_field_required','message':'请指定一个完整字段标识。'}
    if capability is None:
        return {'status':'unsupported','code':'lineage_metadata_unavailable','message':'尚未配置真实字段元数据。'}
    snapshot=capability.registry.snapshot
    relation=snapshot.get(field.rsplit('.',1)[0])
    if not relation or field.rsplit('.',1)[1] not in relation['columns']:
        return {'status':'unsupported','code':'field_not_found','message':'当前真实元数据中没有该字段。'}
    if not relation.get('schema_usage') or not relation['columns'][field.rsplit('.',1)[1]].get('readable'):
        return {'status':'unsupported','code':'lineage_access_denied','message':'当前身份无权查看该字段元数据。'}
    kind='both'
    if re.search(r'删除|移除',question) and not re.search(r'值|修改|变更',question):
        kind='delete'
    elif re.search(r'值|数值',question) and not re.search(r'删除|移除',question):
        kind='value'
    began=monotonic()
    try:
        result=LineageTool.local().lookup(field,impact=kind)
    except (LineageError,OSError) as error:
        result={'status':'error','code':str(error) if isinstance(error,LineageError) else 'lineage_source_unavailable'}
    event={'trace_id':uuid4().hex,'kind':'lineage','tool':'neo4j_field_impact','status':result['status'],
           'request':{'field':field,'impact':kind},'elapsed_ms':round((monotonic()-began)*1000)}
    trace['tools'].append(event)
    trace['routing']={'rule':'field_impact','lineage':result}
    if result['status']!='ok':
        return dict(result,message='字段未在当前审核图谱中登记，或 Neo4j/版本证据不可用；未执行指标查询。')
    sections=['本轮已核验范围：净额、折扣金额、订单商品明细数；不是全目录影响集合。']
    for label,typ in (('字段值变化','VALUE'),('字段删除（重建任务依赖）','DELETE')):
        if kind=='both' or kind.upper()==typ:
            matches=[r for r in result['impacts'] if r['impact_type']==typ]
            sections.append(label+'：'+('、'.join(r['metric_id'] for r in matches) or '无已核验影响')+'。')
            for row in matches:
                sections.append(' → '.join(row['path'])+'；证据：'+'；'.join(
                    f"{e['source_file']}:{e['line_start']}-{e['line_end']}"+
                    (f" {e['expression']}" if typ=='VALUE' else '') for e in row['evidence']))
    sections.append('删除影响指当前 SQL 的后续加工/重建会失败，不代表已物化的历史查询立刻失效。')
    return dict(result,path='lineage',answer_zh='\n'.join(sections))
