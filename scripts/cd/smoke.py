"""Exercise production assets, real API routing and real metric SQL without an LLM."""
import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from dotenv import dotenv_values
from config import bind_config
from dataquery_agent.metric_query_service import MetricQueryService
from dataquery_agent.conversation_manager import local_manager

bind_config()
BASE = 'http://127.0.0.1:5173'
HEADERS = {'X-DataQuery-Client': 'workspace', 'Origin': BASE, 'Content-Type': 'application/json'}


def request(path, body=None, headers=None):
    req = Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                  headers=HEADERS | (headers or {}))
    with urlopen(req, timeout=20) as response:
        return response.read(), response.headers


html, _ = request('/')
assert b'<div id="root">' in html
asset = re.search(rb'src="(/assets/[^" ]+\.js)"', html)
assert asset, 'No production JavaScript entry'
javascript, _ = request(asset[1].decode())
assert len(javascript) > 100
for path, headers, status in [('/api/health', {'Origin': 'https://invalid.example'}, 403),
                              ('/api/health', {'Host': 'invalid.example'}, 403),
                              ('/.env.api', {}, 404), ('/api/not-approved', {}, 404)]:
    try:
        request(path, headers=headers)
    except HTTPError as error:
        assert error.code == status
    else:
        raise AssertionError('Proxy boundary failed')
assert json.loads(request('/api/health')[0]) == {'status': 'ok'}
cid = json.loads(request('/api/conversations', {})[0])['conversation_id']
answer = json.loads(request('/api/chat', {'conversation_id': cid, 'message_id': str(uuid4()),
                                         'question': 'order_count怎么定义？'})[0])
assert answer['status'] == 'completed' and answer['citations']
manager, owner = local_manager()
stored = manager.get_query(owner, answer['query_id'])
assert stored['prompt_versions'] == []
assert stored['knowledge']['query_embedding_called'] is False
history = json.loads(request('/api/queries/' + answer['query_id'])[0])
assert history['trace'] == answer['trace']
service = MetricQueryService(dotenv_values('/app/.env.metric-query')['METRIC_QUERY_DSN'])
metric = service.query({'metric_id': 'order_count', 'start_date': '2025-01-01', 'end_date': '2025-02-01'})
assert metric['status'] == 'ok' and metric['results'][0]['value'] == '2312'
token = dotenv_values('/app/.env.api')['DATAQUERY_HTTP_TOKEN'].encode()
assert token not in html and token not in javascript
Path('artifacts/cd-smoke.json').write_text(json.dumps({
    'version': 1, 'source_commit': os.environ['RELEASE_COMMIT'],
    'images': {role: os.environ[role.upper() + '_IMAGE'] for role in ('backend', 'frontend')},
    'frontend_assets': True, 'api_health': True, 'catalog_citations': len(answer['citations']),
    'history_trace_equal': True, 'metric_service_order_count': '2312', 'external_model_calls': 0,
    'proxy_boundaries': True, 'token_absent_from_browser_assets': True,
}, indent=2) + '\n')
print('Production frontend, real API Catalog request, metric SQL and proxy boundaries passed')
