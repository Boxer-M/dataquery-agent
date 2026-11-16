export type Status = 'completed' | 'partial' | 'clarification_required' | 'failed'
export type Cell = string | number | boolean | null | Cell[] | { [key: string]: Cell }
export type Row = Record<string, Cell>
type ApiError = { code: string; message: string }

export type ChatRequest = {
  conversation_id: string
  message_id: string
  question: string
}

export type MetricDefinition = {
  metric_id?: string | null
  name?: string | null
  definition?: string | null
  definition_version?: string | null
}

export type Citation = Partial<Record<
  'document_id' | 'document_version' | 'chunk_id' | 'title' |
  'source_uri' | 'source_location' | 'source_type' | 'quote', string | null
>>

export type TraceStep = {
  trace_id?: string; plan_id?: string; task_id?: string; attempt_id?: string
  capability_trace_id?: string; capability_phase?: string; metric_id?: string
  agent?: string; tool?: string; kind?: string; status?: string; code?: string
  sources?: string[]; depends_on?: string[]; failed_dependencies?: string[]
  schedule_position?: number; elapsed_ms?: number; agent_elapsed_ms?: number
}

export type PublicTrace = {
  plan_id?: string; attempt_id?: string; trace_id?: string; previous_query_id?: string
  tasks?: TraceStep[]; capability?: TraceStep[]; tools?: TraceStep[]
}

export type Visualization = {
  version: '1'
  metric: { metric_id: string; name: string; unit: string; unit_type: 'count'; sources: string[] }
  dimension: { field: 'month' | 'bucket'; type: 'time' | 'category'; semantic: 'month' | 'channel';
    order: 'calendar_asc' | 'category_code_asc' }
  window: { start_date: string; end_date: string; end_exclusive: true }
  value_path: string[]
  row_order: number[]
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
  definitions: MetricDefinition[]
  citations: Citation[]
  clarification_choices: MetricDefinition[]
  trace?: PublicTrace
  replayed: boolean
  visualization?: Visualization
  visualization_unavailable_reason?: string
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
      if (path.startsWith('/api/queries/') && (response.status === 403 || response.status === 404)) {
        throw new RequestError('记录不存在或当前身份无权查看。', false)
      }
      const message = typeof value?.error?.message === 'string' ? value.error.message : `请求失败（HTTP ${response.status}）。`
      throw new RequestError(message, response.status >= 500)
    }
    if (!value || typeof value !== 'object') throw new RequestError('API 返回了无效响应。', true)
    return value as T
  } catch (error) {
    if (error instanceof RequestError) throw error
    if (controller.signal.aborted) throw new RequestError(
      method === 'GET' ? '读取超时，请重试。' : '请求超时，可重试原消息。', true)
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
      || !Array.isArray(value.definitions) || !Array.isArray(value.citations)
      || (value.status !== 'failed' && (value.conversation_id !== body.conversation_id
          || value.message_id !== body.message_id || !value.query_id))) {
    throw new RequestError('API 回执不完整或与当前消息不匹配。', true)
  }
  return value
}

export async function readQuery(queryId: string) {
  const value = await jsonRequest<ChatResponse>(`/api/queries/${encodeURIComponent(queryId)}`, 'GET')
  if (!['completed', 'partial', 'clarification_required', 'failed'].includes(value.status)) {
    throw new RequestError('API 返回了无效的历史状态。', false)
  }
  if (value.query_id !== queryId) throw new RequestError('历史记录与请求的查询 ID 不匹配。', false)
  return {
    ...value,
    rows: value.rows ?? [],
    definitions: value.definitions ?? [],
    citations: value.citations ?? [],
    clarification_choices: value.clarification_choices ?? [],
  }
}
