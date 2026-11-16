import { test } from 'node:test'
import assert from 'node:assert/strict'
import { checkAccess } from './local-api.mjs'

const request = {
  url: '/api/chat', method: 'POST', socket: { remoteAddress: '127.0.0.1' },
  headers: { host: '127.0.0.1:5173', origin: 'http://127.0.0.1:5173',
    'sec-fetch-site': 'same-origin', 'content-type': 'application/json', 'x-dataquery-client': 'workspace' },
}
const withHeaders = headers => ({ ...request, headers: { ...request.headers, ...headers } })

test('allows only the same-origin routes and intended methods', () => {
  assert.equal(checkAccess(request), undefined)
  assert.equal(checkAccess({ ...request, method: 'GET', url: '/api/health' }), undefined)
  assert.equal(checkAccess({ ...request, url: '/api/queries/123' })[0], 404)
  assert.equal(checkAccess({ ...request, url: '/api/chat?url=https://example.invalid' })[0], 404)
  assert.equal(checkAccess({ ...request, method: 'OPTIONS' })[0], 405)
})

test('blocks forged hosts, origins, cross-site and non-loopback clients', () => {
  for (const headers of [
    { host: 'example.invalid' }, { origin: 'https://example.invalid' },
    { 'sec-fetch-site': 'cross-site' }, { 'x-dataquery-client': undefined }, { origin: undefined },
  ]) {
    assert.equal(checkAccess(withHeaders(headers))[0], 403)
  }
  assert.equal(checkAccess({ ...request, socket: { remoteAddress: '192.0.2.1' } })[0], 403)
  assert.equal(checkAccess(withHeaders({ 'content-type': 'text/plain' }))[0], 415)
})
