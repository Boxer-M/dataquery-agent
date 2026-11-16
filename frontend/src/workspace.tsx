import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Input, Spin, Table, Tag } from 'antd'
import {
  ApartmentOutlined, ArrowUpOutlined, BarChartOutlined, CalendarOutlined,
  MessageOutlined, PlusOutlined, ReloadOutlined,
} from '@ant-design/icons'
import { createConversation, health, RequestError, sendQuestion } from './api'
import type { Cell, ChatRequest, ChatResponse, Row, Status } from './api'

type Message = { request: ChatRequest; response?: ChatResponse; error?: string; retryable?: boolean }

const statuses: Record<Status, { label: string; color: string }> = {
  completed: { label: '查询完成', color: 'success' },
  partial: { label: '部分完成', color: 'warning' },
  clarification_required: { label: '需要澄清', color: 'processing' },
  failed: { label: '查询失败', color: 'error' },
}

const starterQuestions = [
  { label: '查询订单量', icon: <BarChartOutlined />, question: '2025年1月订单量是多少？' },
  { label: '比较订单与明细', icon: <ApartmentOutlined />, question: '查询2025年1月订单量和订单商品明细数，并给出正式定义' },
  { label: '理解退货口径', icon: <CalendarOutlined />, question: '商品明细退货率怎么定义？' },
]

const equivalentLabels: Record<string, string> = {
  bucket: '分组', value: '数值', month: '月份', start_date: '开始日期', end_date: '结束日期',
  numerator: '分子', denominator: '分母', order_count: '订单量', order_item_count: '商品明细数',
  captured_payment_amount: '成功支付金额',
}

function flatten(row: Row): Row {
  const { metrics, ...rest } = row
  if (!metrics || typeof metrics !== 'object') return rest
  return { ...rest, ...Object.fromEntries(Object.entries(metrics).map(([key, cell]) => [
    key, cell && typeof cell === 'object' && 'value' in cell ? cell.value : cell,
  ])) }
}

function cellText(value: Cell | undefined): string {
  if (value == null) return '—'
  if (typeof value !== 'object') return String(value)
  return JSON.stringify(value)
}

function QueryResult({ result }: { result: ChatResponse }) {
  const state = statuses[result.status]
  const rows = result.rows.map(flatten)
  const keys = Array.from(new Set(rows.flatMap(Object.keys)))
  return <>
    <div className="result-heading"><span>查询结果</span><Tag color={state.color}>{state.label}</Tag></div>
    {result.answer && <p className="answer-text">{result.answer}</p>}
    {result.error && <Alert type={result.status === 'failed' ? 'error' : 'warning'} showIcon
      title={result.error.message} description={result.error.code} />}
    {result.clarification_choices.length > 0 && <ul className="choices">
      {result.clarification_choices.map((choice, index) => <li key={choice.metric_id ?? index}>
        <strong>{choice.name ?? choice.metric_id}</strong>{choice.definition && <span> · {choice.definition}</span>}
      </li>)}
    </ul>}
    {rows.length > 0 && <Table<Row>
      size="small" pagination={false} scroll={{ x: 'max-content' }}
      columns={keys.map(key => ({ key, title: equivalentLabels[key] ?? key, render: (_, row) => cellText(row[key]) }))}
      dataSource={rows} rowKey={(_, index) => String(index)}
    />}
    {result.rows_truncated && <Alert type="warning" showIcon title="结果已截断，表格显示 API 返回的部分数据。" />}
    <div className="result-meta">
      {typeof result.row_count === 'number' && <span>{result.row_count} 行</span>}
      {result.query_id && <span>查询 ID <code>{result.query_id}</code></span>}
      {result.replayed && <span>已重放原查询回执</span>}
    </div>
  </>
}

export function App() {
  const [connection, setConnection] = useState<'checking' | 'online' | 'offline'>('checking')
  const [conversation, setConversation] = useState<string>()
  const [messages, setMessages] = useState<Message[]>([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const locked = useRef(false)
  const end = useRef<HTMLDivElement>(null)

  async function refreshHealth() {
    setConnection('checking')
    try { setConnection(await health() ? 'online' : 'offline') }
    catch { setConnection('offline') }
  }

  useEffect(() => { void refreshHealth() }, [])
  useEffect(() => { end.current?.scrollIntoView({ block: 'nearest' }) }, [messages, busy])

  async function run(operation: () => Promise<void>) {
    if (locked.current) return
    locked.current = true
    setBusy(true)
    setNotice('')
    try { await operation() }
    catch (error) { setNotice(error instanceof Error ? error.message : '请求失败。') }
    finally {
      locked.current = false
      setBusy(false)
    }
  }

  function update(id: string, changes: Partial<Message>) {
    setMessages(items => items.map(item => item.request.message_id === id ? { ...item, ...changes } : item))
  }

  async function execute(request: ChatRequest) {
    update(request.message_id, { error: undefined, retryable: false, response: undefined })
    try {
      const response = await sendQuestion(request)
      update(request.message_id, { response, retryable: response.error?.code === 'conversation_busy' })
      setConnection('online')
    } catch (error) {
      update(request.message_id, {
        error: error instanceof Error ? error.message : '请求失败。',
        retryable: error instanceof RequestError && error.retryable,
      })
    }
  }

  function submit() {
    const question = draft.trim()
    if (!question || question.length > 500) return
    void run(async () => {
      const active = conversation ?? await createConversation()
      setConversation(active)
      const request = { conversation_id: active, message_id: crypto.randomUUID(), question }
      setMessages(items => [...items, { request }])
      setDraft('')
      await execute(request)
    })
  }

  function restart() {
    void run(async () => {
      const active = await createConversation()
      setConversation(active)
      setMessages([])
      setDraft('')
      setConnection('online')
    })
  }

  return <div className="workspace">
    <aside className="sidebar" aria-label="会话">
      <div className="brand"><span className="mark"><BarChartOutlined /></span><div><b>DataQuery</b><span>分析工作台</span></div></div>
      <Button className="new-session" icon={<PlusOutlined />} onClick={restart} disabled={busy}>新建会话</Button>
      <div className="sidebar-label">当前会话</div>
      <div className="session"><MessageOutlined /><div><strong>{conversation ? '数据问答' : '尚未开始'}</strong>
        {conversation && <small title={conversation}>{conversation}</small>}
      </div></div>
      <div className="sidebar-footer"><span className={`status-dot ${connection}`} />
        {{ checking: '正在连接 API', online: 'API 已连接', offline: 'API 未连接' }[connection]}
        <button type="button" aria-label="重新检查 API 连接" onClick={() => void refreshHealth()}><ReloadOutlined /></button>
      </div>
    </aside>
    <main className="main">
      <header className="topbar"><span>工作台</span><span className="topbar-divider">/</span><strong>数据问答</strong></header>
      <div className={`thread ${messages.length ? 'has-messages' : ''}`} aria-live="polite">
        {!messages.length && <div className="empty-state">
          <div className="empty-icon"><BarChartOutlined /></div>
          <h1>数据问答</h1>
          <p>查询电商指标、对比业务数据，或了解指标口径。</p>
          <div className="starters">{starterQuestions.map(item => <button key={item.label} type="button"
            onClick={() => setDraft(item.question)} disabled={busy}>
            <span className="starter-icon">{item.icon}</span><span>{item.label}</span><ArrowUpOutlined className="starter-arrow" />
          </button>)}</div>
        </div>}
        <div className="messages">{messages.map(message => <article className="message" key={message.request.message_id}>
          <div className="user-message"><span>你</span><p>{message.request.question}</p></div>
          <div className="agent-message">
            {message.response ? <QueryResult result={message.response} />
              : message.error ? <Alert type="error" showIcon title={message.error} />
              : <div className="loading"><Spin size="small" /> 正在查询</div>}
            {message.retryable && <Button icon={<ReloadOutlined />} disabled={busy}
              onClick={() => void run(() => execute(message.request))}>重试原消息</Button>}
          </div>
        </article>)}</div>
        <div ref={end} />
      </div>
      <div className="composer-area">
        {notice && <Alert className="notice" showIcon type="error" title={notice} />}
        <form className="composer" onSubmit={event => { event.preventDefault(); submit() }}>
          <Input.TextArea aria-label="输入问题" value={draft} maxLength={500} autoSize={{ minRows: 1, maxRows: 5 }}
            onChange={event => setDraft(event.target.value)} placeholder="输入你想查询的问题"
            onKeyDown={event => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault()
                submit()
              }
            }} />
          <div className="composer-footer"><span>Enter 发送 · Shift + Enter 换行</span>
            <Button type="primary" htmlType="submit" loading={busy} disabled={busy || !draft.trim()}
              icon={<ArrowUpOutlined />}>发送</Button>
          </div>
        </form>
      </div>
    </main>
  </div>
}
