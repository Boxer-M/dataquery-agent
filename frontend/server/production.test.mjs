import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { request } from 'node:http'
import { productionServer } from './production.mjs'

test('production assets preserve local proxy boundaries and hide config files', async () => {
  const root = await mkdtemp(join(tmpdir(), 'dataquery-production-'))
  await mkdir(join(root, 'assets'))
  await writeFile(join(root, 'index.html'), '<div id="root"></div>')
  await writeFile(join(root, 'assets/app.js'), 'export const production = true')
  await writeFile(join(root, '.env.api'), 'private-placeholder')
  const server = productionServer({ root })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const port = server.address().port
  const get = (path, headers = {}) => new Promise((resolve, reject) => {
    request({ host: '127.0.0.1', port, path, headers: { Host: '127.0.0.1:5173', ...headers } }, response => {
      let body = ''
      response.on('data', value => { body += value })
      response.on('end', () => resolve({ status: response.statusCode, body }))
    }).on('error', reject).end()
  })
  try {
    assert.equal((await get('/')).status, 200)
    assert.equal((await get('/assets/app.js')).body, 'export const production = true')
    for (const path of ['/.env.api', '/assets/../../.env.api', '/assets/%2e%2e/%2e%2e/.env.api']) {
      assert.equal((await get(path)).status, 404)
    }
    assert.equal((await get('/', { Host: 'attacker.invalid' })).status, 403)
    assert.equal((await get('/api/health', { 'X-DataQuery-Client': 'workspace', Origin: 'https://attacker.invalid' })).status, 403)
    assert.equal((await get('/api/arbitrary', { 'X-DataQuery-Client': 'workspace' })).status, 404)
  } finally {
    await new Promise(resolve => server.close(resolve))
    await rm(root, { recursive: true, force: true })
  }
})
