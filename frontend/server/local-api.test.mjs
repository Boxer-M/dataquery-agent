import { test } from 'node:test'
import assert from 'node:assert/strict'
import { checkAccess } from './local-api.mjs'

const request = {
  url: '/api/chat', method: 'POST', socket: { remoteAddress: '127.0.0.1' },
  headers: { host: '127.0.0.1:5173', origin: 'http://127.0.0.1:5173',
    'sec-fetch-site': 'same-origin', 'content-type': 'application/json', 'x-dataquery-client': 'workspace' },
}
const withHeaders = headers => ({ ...request, headers: { ...request.headers, ...headers } })

test('only approved artifact export/download methods and local origins are accepted', () => {
  const id = '2f074dc9-2d98-4824-a765-09fc65ead286'
  const exportUrl = `/api/queries/${id}/artifacts/xlsx`
  const downloadUrl = `/api/artifacts/${id}/download`
  assert.equal(checkAccess({ ...request, url: exportUrl }), undefined)
  assert.equal(checkAccess({ ...request, method: 'GET', url: downloadUrl }), undefined)
  assert.equal(checkAccess({ ...request, method: 'GET', url: exportUrl })[0], 405)
  assert.equal(checkAccess({ ...request, url: downloadUrl })[0], 405)
  for (const url of [downloadUrl + '?owner=foreign', exportUrl + '/extra', '/api/artifacts/unknown/download']) {
    assert.equal(checkAccess({ ...request, url })[0], 404)
  }
  assert.equal(checkAccess({ ...withHeaders({ 'content-type': 'text/plain' }), url: exportUrl })[0], 415)
  assert.equal(checkAccess({ ...withHeaders({ origin: 'https://example.invalid' }), url: exportUrl })[0], 403)
  assert.equal(checkAccess({ ...withHeaders({ host: 'example.invalid' }), method: 'GET', url: downloadUrl })[0], 403)
  assert.equal(checkAccess({ ...withHeaders({ host: 'localhost:5173', origin: 'http://localhost:5173' }), url: exportUrl }), undefined)
})

test('allows only the same-origin routes and intended methods', () => {
  assert.equal(checkAccess(request), undefined)
  assert.equal(checkAccess({ ...request, method: 'GET', url: '/api/health' }), undefined)
  for (const url of ['/api/conversations', '/api/conversations?offset=20',
    '/api/conversations/2f074dc9-2d98-4824-a765-09fc65ead286/messages?offset=0']) {
    assert.equal(checkAccess({ ...request, method: 'GET', url }), undefined)
  }
  for (const url of ['/api/conversations?owner=foreign', '/api/conversations?offset=-1',
    '/api/conversations?offset=10000', '/api/conversations/unknown/messages']) {
    assert.equal(checkAccess({ ...request, method: 'GET', url })[0], 404)
  }
  const url = '/api/queries/2f074dc9-2d98-4824-a765-09fc65ead286'
  assert.equal(checkAccess({ ...request, method: 'GET', url }), undefined)
  assert.equal(checkAccess({ ...request, url })[0], 405)
  assert.equal(checkAccess({ ...request, url: url + '/resume' }), undefined)
  assert.equal(checkAccess({ ...request, method: 'GET', url: url + '/resume' })[0], 405)
  assert.equal(checkAccess({ ...request, url: url + '/resume?metric=order_count' })[0], 404)
  for (const path of ['/api/queries/123', url + '?test=1', url + '/extra', '/api/queries/']) {
    assert.equal(checkAccess({ ...request, method: 'GET', url: path })[0], 404)
  }
  assert.equal(checkAccess(withHeaders({ host: 'localhost:5173', origin: 'http://localhost:5173' })), undefined)
  assert.equal(checkAccess({ ...request, url: '/api/chat?url=https://example.invalid' })[0], 404)
  assert.equal(checkAccess({ ...request, method: 'OPTIONS' })[0], 405)
})

test('blocks forged hosts, origins, cross-site and non-loopback clients', () => {
  for (const headers of [
    { host: 'example.invalid' }, { host: 'localhost:5173' }, { origin: 'http://localhost:5173' }, { origin: 'https://example.invalid' },
    { 'sec-fetch-site': 'cross-site' }, { 'x-dataquery-client': undefined }, { origin: undefined },
  ]) {
    assert.equal(checkAccess(withHeaders(headers))[0], 403)
  }
  assert.equal(checkAccess({ ...request, socket: { remoteAddress: '192.0.2.1' } })[0], 403)
  assert.equal(checkAccess(withHeaders({ 'content-type': 'text/plain' }))[0], 415)
})

test('allows bounded PDF import and JSON review routes for the local workspace', () => {
  const base = '/api/documents'
  const document = base + '/2f074dc9-2d98-4824-a765-09fc65ead286'
  const pdf = withHeaders({ 'content-type': 'application/pdf' })
  assert.equal(checkAccess({ ...pdf, url: base }), undefined)
  assert.equal(checkAccess({ ...pdf, url: document + '/versions' }), undefined)
  assert.equal(checkAccess({ ...request, url: base })[0], 415)
  assert.equal(checkAccess({ ...request, method: 'GET', url: document + '/pages/2' }), undefined)
  assert.equal(checkAccess({ ...request, url: document + '/review' }), undefined)
  assert.equal(checkAccess({ ...request, url: document + '/publish' }), undefined)
  for (const path of ['/%2E%2E/%2Eenv.api', '/pages/01', '?owner=foreign', '/receipt']) {
    assert.equal(checkAccess({ ...request, method: 'GET', url: document + path })[0], 404)
  }
  const foreign = withHeaders({ origin: 'https://example.invalid', 'content-type': 'application/pdf' })
  assert.equal(checkAccess({ ...foreign, url: base })[0], 403)
})
