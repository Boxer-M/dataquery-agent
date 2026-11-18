import { createServer } from 'node:http'
import { readFile } from 'node:fs/promises'
import { resolve, sep, extname } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createLocalApiHandler } from './local-api.mjs'

const types = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png', '.woff2': 'font/woff2' }

export function productionServer({ root = fileURLToPath(new URL('../dist', import.meta.url)),
  tokenFile = '/run/frontend/.env.api' } = {}) {
  const directory = resolve(root)
  const proxy = createLocalApiHandler(tokenFile)
  return createServer(async (req, res) => {
    if (/^\/api(?:\/|\?|$)/.test(req.url ?? '')) {
      return proxy(req, res, () => { res.writeHead(404).end() })
    }
    const origin = new Map([['127.0.0.1:5173', 'http://127.0.0.1:5173'], ['localhost:5173', 'http://localhost:5173']])
    if (req.socket.remoteAddress !== '127.0.0.1' || !origin.has(req.headers.host)
        || (req.headers.origin && req.headers.origin !== origin.get(req.headers.host))) {
      return res.writeHead(403).end()
    }
    if (!['GET', 'HEAD'].includes(req.method)) return res.writeHead(405).end()
    let path
    try { path = decodeURIComponent(new URL(req.url, 'http://127.0.0.1').pathname) }
    catch { return res.writeHead(400).end() }
    if (path !== '/' && !path.startsWith('/assets/')) return res.writeHead(404).end()
    const file = resolve(directory, path === '/' ? 'index.html' : '.' + path)
    if (!file.startsWith(directory + sep) || !types[extname(file)]) return res.writeHead(404).end()
    try {
      const body = await readFile(file)
      res.writeHead(200, { 'Content-Type': types[extname(file)], 'Cache-Control': 'no-store',
        'X-Content-Type-Options': 'nosniff' })
      res.end(req.method === 'HEAD' ? undefined : body)
    } catch { res.writeHead(404).end() }
  })
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const server = productionServer()
  server.listen(5173, '127.0.0.1', () => console.log('Production assets listening on loopback port 5173'))
  for (const signal of ['SIGTERM', 'SIGINT']) process.on(signal, () => server.close())
}
