import { readFile } from 'node:fs/promises'
import { request } from 'node:http'

const origins = new Map([
  ['127.0.0.1:5173', 'http://127.0.0.1:5173'],
  ['localhost:5173', 'http://localhost:5173'],
])
const queryPath = /^\/api\/queries\/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i
const upstream = 'http://127.0.0.1:8000'
const routes = new Map([
  ['/api/health', 'GET'],
  ['/api/conversations', 'POST'],
  ['/api/chat', 'POST'],
])

function error(response, status, code, message) {
  response.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
  response.end(JSON.stringify({ error: { code, message } }))
}

export function checkAccess(req) {
  const { headers, socket, url, method } = req
  const origin = origins.get(headers.host)
  if (socket.remoteAddress !== '127.0.0.1' || !origin
      || (headers.origin && headers.origin !== origin)
      || (headers['sec-fetch-site'] && headers['sec-fetch-site'] !== 'same-origin')
      || headers['x-dataquery-client'] !== 'workspace') {
    return [403, 'proxy_access_denied', '只允许本地工作台发起同源请求。']
  }
  const allowedMethod = routes.get(url) ?? (queryPath.test(url) ? 'GET' : undefined)
  if (!allowedMethod) return [404, 'proxy_route_not_found', '此接口未开放。']
  if (allowedMethod !== method) return [405, 'proxy_method_not_allowed', '请求方法不受支持。']
  if (method === 'POST' && headers.origin !== origin) {
    return [403, 'proxy_origin_required', '写入请求需要工作台的本地 Origin。']
  }
  if (url === '/api/chat' && headers['content-type']?.split(';')[0] !== 'application/json') {
    return [415, 'proxy_json_required', '请提交 JSON 请求。']
  }
}

export function localApi(tokenFile) {
  return {
    name: 'dataquery-local-api',
    configureServer(server) {
      if (server.config.server.host !== '127.0.0.1' || server.config.server.port !== 5173
          || !server.config.server.strictPort) {
        throw new Error('DataQuery workspace requires 127.0.0.1:5173 with strictPort.')
      }
      server.middlewares.use(async (req, res, next) => {
        if (!/^\/api(?:\/|\?|$)/.test(req.url ?? '')) return next()
        const rejected = checkAccess(req)
        if (rejected) return error(res, ...rejected)

        const chunks = []
        let size = 0
        try {
          for await (const chunk of req.iterator({ destroyOnReturn: false })) {
            size += chunk.length
            if (size > 8192) return error(res, 413, 'request_too_large', '请求体过大。')
            chunks.push(chunk)
          }
        } catch {
          return error(res, 400, 'request_incomplete', '请求未接收完整。')
        }

        const headers = { Accept: 'application/json' }
        if (req.url !== '/api/health') {
          let token
          try {
            const config = await readFile(tokenFile, 'utf8')
            token = /^DATAQUERY_HTTP_TOKEN=([a-fA-F0-9]{64})\s*$/m.exec(config)?.[1]
          } catch {
            // Keep configuration details out of the browser response.
          }
          if (!token) return error(res, 503, 'proxy_not_configured', '本地代理的访问配置尚未就绪。')
          headers.Authorization = `Bearer ${token}`
          if (req.method === 'POST') headers['Content-Type'] = 'application/json'
        }

        const call = request(new URL(req.url, upstream), { method: req.method, headers }, (response) => {
          const parts = []
          let received = 0
          response.on('data', (part) => {
            received += part.length
            if (received > 256 * 1024) {
              if (!res.headersSent) error(res, 502, 'upstream_response_too_large', '后端响应超出上限。')
              response.destroy()
              return
            }
            parts.push(part)
          })
          response.on('error', () => {
            if (!res.headersSent) error(res, 502, 'upstream_unavailable', '后端连接已中断。')
          })
          response.on('end', () => {
            if (res.writableEnded) return
            if (response.headers['content-type']?.split(';')[0] !== 'application/json') {
              return error(res, 502, 'invalid_upstream_response', '后端返回了无效响应。')
            }
            res.writeHead(response.statusCode ?? 502, {
              'Content-Type': 'application/json',
              'Cache-Control': 'no-store',
            })
            res.end(Buffer.concat(parts))
          })
        })
        const deadline = setTimeout(() => {
          if (!res.headersSent) error(res, 504, 'upstream_timeout', '后端请求超时，可重试原消息。')
          call.destroy()
        }, 90_000)
        res.once('close', () => {
          clearTimeout(deadline)
          if (!res.writableEnded) call.destroy()
        })
        call.on('error', () => {
          if (!res.headersSent && !res.destroyed) error(res, 502, 'upstream_unavailable', '无法连接本地 API。')
        })
        call.end(Buffer.concat(chunks))
      })
    },
  }
}
