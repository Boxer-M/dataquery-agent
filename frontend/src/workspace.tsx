import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { Alert, Button, Input, Spin } from 'antd'
import {
  ApartmentOutlined, ArrowUpOutlined, BarChartOutlined, CalendarOutlined,
  FileTextOutlined, MessageOutlined, PlusOutlined, ReloadOutlined,
} from '@ant-design/icons'
import { createConversation, health, listConversations, listMessages, readQuery, RequestError, resumeQuery, sendQuestion } from './api'
import type { ChatRequest, ChatResponse, ConversationItem } from './api'

const QueryResult = lazy(() => import('./query-result').then(module => ({ default: module.QueryResult })))
const QueryHistory = lazy(() => import('./query-history').then(module => ({ default: module.QueryHistory })))

const DocumentWorkbench = lazy(() => import('./document-workbench'))

const HITL_RUN = "dataquery.pendingClarification"
const ACTIVE_CONVERSATION = 'dataquery.activeConversation'

type Message = { request: ChatRequest; response?: ChatResponse; error?: string; retryable?: boolean; historical?: boolean }

const starterQuestions = [
  { label: '查询订单量', icon: <BarChartOutlined />, question: '2025年1月订单量是多少？' },
  { label: '比较订单与明细', icon: <ApartmentOutlined />, question: '查询2025年1月订单量和订单商品明细数，并给出正式定义' },
  { label: '理解退货口径', icon: <CalendarOutlined />, question: '商品明细退货率怎么定义？' },
]

export function App() {
  const [connection, setConnection] = useState<'checking' | 'online' | 'offline'>('checking')
  const [conversation, setConversation] = useState<string>()
  const [sessions, setSessions] = useState<ConversationItem[]>([])
  const [moreSessions, setMoreSessions] = useState(false)
  const [moreMessages, setMoreMessages] = useState(false)
  const [messageOffset, setMessageOffset] = useState(0)
  const cached = useRef(new Map<string, Message[]>())
  const [messages, setMessages] = useState<Message[]>([])
  const [historyId, setHistoryId] = useState<string>()
  const [restored, setRestored] = useState<ChatResponse>()
  const [draft, setDraft] = useState('')
  const [documents, setDocuments] = useState<'open' | 'closed'>()
  const questionInput = useRef<React.ComponentRef<typeof Input.TextArea>>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const locked = useRef(false)
  const end = useRef<HTMLDivElement>(null)

  async function refreshHealth() {
    setConnection('checking')
    try { setConnection(await health() ? 'online' : 'offline') }
    catch { setConnection('offline') }
  }

  useEffect(() => {
    void refreshHealth()
    void run(async () => {
      await refreshSessions()
      const pending = sessionStorage.getItem(HITL_RUN)
      const paused = pending ? await readQuery(pending) : undefined
      const id = paused?.conversation_id ?? sessionStorage.getItem(ACTIVE_CONVERSATION)
      if (id) {
        try {
          const loaded = await openConversation(id)
          if (paused && !loaded.some(item => item.response?.query_id === paused.query_id)) setRestored(paused)
        }
        catch (error) { sessionStorage.removeItem(ACTIVE_CONVERSATION); throw error }
      }
    })
  }, [])
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

  async function refreshSessions(offset = 0) {
    const page = await listConversations(offset)
    setSessions(items => offset ? [...items, ...page.conversations] : page.conversations)
    setMoreSessions(page.has_more)
  }

  function remember() {
    if (conversation) cached.current.set(conversation, messages)
  }

  async function savedMessages(id: string, offset = 0) {
    const page = await listMessages(id, offset)
    const saved = await Promise.all(page.messages.map(async item => ({
      request: { conversation_id: id, message_id: item.message_id, question: item.question },
      response: await readQuery(item.query_id), historical: true,
    })))
    return { messages: saved, more: page.has_more, offset: offset + saved.length }
  }

  async function openConversation(id: string) {
    remember()
    const saved = await savedMessages(id) // Always recheck access, even for a previously opened conversation.
    setConversation(id)
    sessionStorage.setItem(ACTIVE_CONVERSATION, id) // Only the opaque ID, never message text or credentials.
    setMessages([...saved.messages, ...(cached.current.get(id) ?? []).filter(item =>
      !item.response && !saved.messages.some(next => next.request.message_id === item.request.message_id))])
    setMoreMessages(saved.more)
    setMessageOffset(saved.offset)
    setRestored(undefined)
    setHistoryId(undefined)
    setDraft('')
    return saved.messages
  }

  async function loadMoreMessages() {
    if (!conversation) return
    const saved = await savedMessages(conversation, messageOffset)
    setMessages(items => [...items.filter(item => item.historical),
      ...saved.messages.filter(next => !items.some(item => item.request.message_id === next.request.message_id)),
      ...items.filter(item => !item.historical)])
    setMoreMessages(saved.more)
    setMessageOffset(saved.offset)
  }

  async function execute(request: ChatRequest) {
    update(request.message_id, { error: undefined, retryable: false, response: undefined })
    try {
      const response = await sendQuestion(request)
      if (response.hitl?.status === 'paused' && response.query_id) {
        sessionStorage.setItem(HITL_RUN, response.query_id)
      }
      update(request.message_id, { response, retryable: response.error?.code === 'conversation_busy' })
      setConnection('online')
      await refreshSessions().catch(() => setNotice('查询已保存，但会话列表刷新失败，请稍后重试。'))
    } catch (error) {
      update(request.message_id, {
        error: error instanceof Error ? error.message : '请求失败。',
        retryable: error instanceof RequestError && error.retryable,
      })
    }
  }

  function clarify(result: ChatResponse, metricId: string, messageId?: string) {
    if (result.hitl?.status !== 'paused' || !result.query_id) {
      submit(metricId)
      return
    }
    const runId = result.query_id
    void run(async () => {
      const next = await resumeQuery(runId, metricId)
      if (messageId) update(messageId, { response: next, retryable: false })
      else setRestored(next)
      if (sessionStorage.getItem(HITL_RUN) === runId) sessionStorage.removeItem(HITL_RUN)
    })
  }

  function submit(input = draft) {
    const question = input.trim()
    if (!question || question.length > 500) return
    void run(async () => {
      const active = conversation ?? await createConversation()
      setConversation(active)
      sessionStorage.setItem(ACTIVE_CONVERSATION, active)
      const request = { conversation_id: active, message_id: crypto.randomUUID(), question }
      setMessages(items => [...items, { request }])
      setDraft('')
      await execute(request)
    })
  }

  function restart() {
    void run(async () => {
      const active = await createConversation()
      remember()
      setConversation(active)
      sessionStorage.setItem(ACTIVE_CONVERSATION, active)
      setMessages([])
      setMoreMessages(false)
      setMessageOffset(0)
      setRestored(undefined)
      sessionStorage.removeItem(HITL_RUN)
      setHistoryId(undefined)
      setDraft('')
      setConnection('online')
      await refreshSessions()
    })
  }

  return <div className="workspace">
    <aside className="sidebar" aria-label="会话">
      <div className="brand"><span className="mark"><BarChartOutlined /></span><div><b>DataQuery</b><span>分析工作台</span></div></div>
      <Button className="new-session" icon={<PlusOutlined />} onClick={restart} disabled={busy}>新建会话</Button>
      <Button className="document-entry" icon={<FileTextOutlined />} onClick={() => setDocuments('open')}>文档知识库</Button>
      <div className="session-history" aria-label="已保存会话">
        <div className="sidebar-label">已保存会话（30天保留策略）</div>
        {sessions.map(item => <button type="button" key={item.conversation_id} disabled={busy}
          aria-current={conversation === item.conversation_id ? 'true' : undefined}
          title={item.conversation_id} onClick={() => void run(async () => { await openConversation(item.conversation_id) })}>{item.title}</button>)}
        {moreSessions && <button disabled={busy} onClick={() => void run(() => refreshSessions(sessions.length))}>加载更多会话</button>}
        <button disabled={busy} onClick={() => void run(() => refreshSessions())}>刷新会话列表</button>
      </div>
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
        {messages.some(item => item.historical) && <Alert type="info" title="已保存会话：只读取历史，不重新查询。旧记录可能仅保留摘要。" />}
        {moreMessages && <Button disabled={busy} onClick={() => void run(loadMoreMessages)}>加载更多已保存消息</Button>}
        {!messages.length && !restored && <div className="empty-state">
          <div className="empty-icon"><BarChartOutlined /></div>
          <h1>数据问答</h1>
          <p>查询电商指标、对比业务数据，或了解指标口径。</p>
          <div className="starters">{starterQuestions.map(item => <button key={item.label} type="button"
            onClick={() => setDraft(item.question)} disabled={busy}>
            <span className="starter-icon">{item.icon}</span><span>{item.label}</span><ArrowUpOutlined className="starter-arrow" />
          </button>)}</div>
        </div>}
        {restored && <div className="messages"><article className="message"><div className="agent-message">
          <Suspense fallback={<Spin size="small" />}>
            <QueryResult result={restored} busy={busy} onClarify={choice => clarify(restored, choice)} />
          </Suspense>
          {restored.query_id && <Button size="small" onClick={() => setHistoryId(restored.query_id)}>查看查询历史</Button>}
        </div></article></div>}
        <div className="messages">{messages.map((message, index) => <article className="message" key={message.request.message_id}>
          <div className="user-message"><span>你</span><p>{message.request.question}</p></div>
          <div className="agent-message">
            {message.response ? <Suspense fallback={<div className="loading"><Spin size="small" /> 正在加载查询结果</div>}>
              <QueryResult result={message.response}
                busy={busy || messages.slice(index + 1).some(item => item.response && item.response.status !== 'failed')}
                onClarify={choice => clarify(message.response!, choice, message.request.message_id)} />
            </Suspense>
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
          <Input.TextArea ref={questionInput} aria-label="输入问题" value={draft} maxLength={500} autoSize={{ minRows: 1, maxRows: 5 }}
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
    {documents && <Suspense fallback={<div className="history-chunk-loading" role="status">正在加载文档工作台</div>}>
      <DocumentWorkbench open={documents === 'open'} onClose={() => setDocuments('closed')}
        onAsk={title => {
          setDraft(`请根据「${title}」这份 PDF，说明其中的内容并引用原文。`)
          setDocuments('closed')
          window.setTimeout(() => questionInput.current?.focus(), 300)
        }} />
    </Suspense>}
    {historyId && <Suspense fallback={<div className="history-chunk-loading" role="status">
      <Spin size="small" /> 正在加载查询历史
    </div>}>
      <QueryHistory key={historyId} queryId={historyId}
        current={messages.find(message => message.response?.query_id === historyId)?.response}
        onOpen={setHistoryId} onClose={() => setHistoryId(undefined)} />
    </Suspense>}
  </div>
}
