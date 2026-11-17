import { readFile } from 'node:fs/promises'
import { request } from 'node:http'

const origins = new Map([
  ['127.0.0.1:5173', 'http://127.0.0.1:5173'],
  ['localhost:5173', 'http://localhost:5173'],
])
const uuid = '[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'
const resumePath = new RegExp(`^/api/queries/${uuid}/resume$`)
const queryPath = new RegExp(`^/api/queries/${uuid}$`)
const docRoot = '/api/documents'
const pdfRoute = new RegExp(`^${docRoot}(?:/${uuid}/versions)?$`)
const reviewRoute = new RegExp(`^${docRoot}/${uuid}/review$`)
const publishRoute = new RegExp(`^${docRoot}/${uuid}/publish$`)
const previewRoute = new RegExp(`^${docRoot}/${uuid}(?:/pages/[1-9][0-9]?)?$`)
const pdfBytes = 10 * 1024 * 1024
const reviewBytes = 512 * 1024
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
  const pdf = pdfRoute.test(url)
  const review = reviewRoute.test(url)
  const publish = publishRoute.test(url)
  const allowedMethod = routes.get(url) ?? (pdf || review || publish || resumePath.test(url) ? 'POST'
    : queryPath.test(url) || previewRoute.test(url) ? 'GET' : undefined)
  if (!allowedMethod) return [404, 'proxy_route_not_found', '此接口未开放。']
  if (allowedMethod !== method) return [405, 'proxy_method_not_allowed', '请求方法不受支持。']
  if (method === 'POST' && headers.origin !== origin) {
    return [403, 'proxy_origin_required', '写入请求需要工作台的本地 Origin。']
  }
  const mediaType = headers['content-type']?.split(';')[0].trim().toLowerCase()
  if (pdf && mediaType !== 'application/pdf') {
    return [415, 'proxy_pdf_required', '请提交 PDF 请求体。']
  }
  if ((url === '/api/chat' || review || publish) && mediaType !== 'application/json') {
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
      let writingDocument = false
      server.middlewares.use(async (req, res, next) => {
        if (!/^\/api(?:\/|\?|$)/.test(req.url ?? '')) return next()
        const rejected = checkAccess(req)
        if (rejected) return error(res, ...rejected)

        const pdf = pdfRoute.test(req.url)
        const review = reviewRoute.test(req.url)
        if (pdf || review || publishRoute.test(req.url)) {
          if (writingDocument) return error(res, 409, 'document_operation_busy', '文档操作正在处理，请重试。')
          writingDocument = true
          res.once('close', () => { writingDocument = false })
        }
        const requestLimit = pdf ? pdfBytes : review ? reviewBytes : 8192
        const responseLimit = previewRoute.test(req.url) ? 8 * 1024 * 1024 : 256 * 1024
        const chunks = []
        let size = 0
        const uploadDeadline = pdf || review ? setTimeout(() => {
          if (!res.headersSent) error(res, 408, 'request_timeout', '请求体接收超时。')
          req.destroy()
        }, 15_000) : undefined
        try {
          for await (const chunk of req.iterator({ destroyOnReturn: false })) {
            size += chunk.length
            if (size > requestLimit) return error(res, 413, 'request_too_large', '请求体过大。')
            chunks.push(chunk)
          }
        } catch {
          if (!res.headersSent && !res.destroyed) return error(res, 400, 'request_incomplete', '请求未接收完整。')
          return
        } finally {
          clearTimeout(uploadDeadline)
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
          if (req.method === 'POST') headers['Content-Type'] = pdf ? 'application/pdf' : 'application/json'
        }

        const call = request(new URL(req.url, upstream), { method: req.method, headers }, (response) => {
          const parts = []
          let received = 0
          response.on('data', (part) => {
            received += part.length
            if (received > responseLimit) {
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
