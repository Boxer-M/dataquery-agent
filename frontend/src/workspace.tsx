import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Input, Spin } from 'antd'
import {
  ApartmentOutlined, ArrowUpOutlined, BarChartOutlined, CalendarOutlined,
  MessageOutlined, PlusOutlined, ReloadOutlined,
} from '@ant-design/icons'
import { createConversation, health, RequestError, sendQuestion } from './api'
import type { ChatRequest, ChatResponse } from './api'
import { QueryResult } from './query-result'
import { QueryHistory } from './query-history'

type Message = { request: ChatRequest; response?: ChatResponse; error?: string; retryable?: boolean }

const starterQuestions = [
  { label: '查询订单量', icon: <BarChartOutlined />, question: '2025年1月订单量是多少？' },
  { label: '比较订单与明细', icon: <ApartmentOutlined />, question: '查询2025年1月订单量和订单商品明细数，并给出正式定义' },
  { label: '理解退货口径', icon: <CalendarOutlined />, question: '商品明细退货率怎么定义？' },
]

export function App() {
  const [connection, setConnection] = useState<'checking' | 'online' | 'offline'>('checking')
  const [conversation, setConversation] = useState<string>()
  const [messages, setMessages] = useState<Message[]>([])
  const [historyId, setHistoryId] = useState<string>()
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

  function submit(input = draft) {
    const question = input.trim()
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
      setHistoryId(undefined)
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
      <div className="session-history" aria-label="当前页面查询历史">
        {messages.some(message => message.response?.query_id) && <div className="sidebar-label">当前页面查询</div>}
        {messages.filter(message => message.response?.query_id).map(message =>
          <button type="button" key={message.request.message_id}
            onClick={() => setHistoryId(message.response?.query_id)}>{message.request.question}</button>)}
      </div>
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
        <div className="messages">{messages.map((message, index) => <article className="message" key={message.request.message_id}>
          <div className="user-message"><span>你</span><p>{message.request.question}</p></div>
          <div className="agent-message">
            {message.response ? <QueryResult result={message.response}
              busy={busy || messages.slice(index + 1).some(item => item.response && item.response.status !== 'failed')}
              onClarify={submit} />
              : message.error ? <Alert type="error" showIcon title={message.error} />
              : <div className="loading"><Spin size="small" /> 正在查询</div>}
            {message.response?.query_id && <Button size="small"
              onClick={() => setHistoryId(message.response?.query_id)}>查看查询历史</Button>}
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
    {historyId && <QueryHistory key={historyId} queryId={historyId}
      current={messages.find(message => message.response?.query_id === historyId)?.response}
      onOpen={setHistoryId} onClose={() => setHistoryId(undefined)} />}
  </div>
}
