export type Status = 'completed' | 'partial' | 'clarification_required' | 'failed'
export type Cell = string | number | boolean | null | { [key: string]: Cell }
export type Row = Record<string, Cell>
type ApiError = { code: string; message: string }

export type ChatRequest = {
  conversation_id: string
  message_id: string
  question: string
}

export type ChatResponse = {
  status: Status
  conversation_id?: string
  message_id?: string
  query_id?: string
  answer?: string
  error?: ApiError
  rows: Row[]
  row_count?: number
  rows_truncated: boolean
  clarification_choices: { metric_id?: string; name?: string; definition?: string }[]
  replayed: boolean
}

export class RequestError extends Error {
  constructor(message: string, readonly retryable: boolean) {
    super(message)
  }
}

async function jsonRequest<T>(path: string, method: 'GET' | 'POST', body?: ChatRequest): Promise<T> {
  const controller = new AbortController()
  const deadline = window.setTimeout(() => controller.abort(), path === '/api/health' ? 4_000 : 95_000)
  try {
    const response = await fetch(path, {
      method,
      mode: 'same-origin',
      cache: 'no-store',
      headers: { 'X-DataQuery-Client': 'workspace', ...(body ? { 'Content-Type': 'application/json' } : {}) },
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    })
    const value = await response.json().catch(() => null)
    if (!response.ok) {
      const message = typeof value?.error?.message === 'string' ? value.error.message : `请求失败（HTTP ${response.status}）。`
      throw new RequestError(message, response.status >= 500)
    }
    if (!value || typeof value !== 'object') throw new RequestError('API 返回了无效响应。', true)
    return value as T
  } catch (error) {
    if (error instanceof RequestError) throw error
    if (controller.signal.aborted) throw new RequestError('请求超时，可重试原消息。', true)
    throw new RequestError('网络连接中断，请确认本地 API 正在运行。', true)
  } finally {
    window.clearTimeout(deadline)
  }
}

export async function health() {
  return (await jsonRequest<{ status: string }>('/api/health', 'GET')).status === 'ok'
}

export async function createConversation() {
  const value = await jsonRequest<{ conversation_id: string }>('/api/conversations', 'POST')
  if (typeof value.conversation_id !== 'string' || !value.conversation_id) {
    throw new RequestError('API 未返回会话 ID。', false)
  }
  return value.conversation_id
}

export async function sendQuestion(body: ChatRequest) {
  const value = await jsonRequest<ChatResponse>('/api/chat', 'POST', body)
  if (!['completed', 'partial', 'clarification_required', 'failed'].includes(value.status)
      || !Array.isArray(value.rows) || !Array.isArray(value.clarification_choices)
      || (value.status !== 'failed' && (value.conversation_id !== body.conversation_id
          || value.message_id !== body.message_id || !value.query_id))) {
    throw new RequestError('API 回执不完整或与当前消息不匹配。', true)
  }
  return value
}
