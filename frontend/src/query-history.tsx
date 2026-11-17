import { useEffect, useState } from 'react'
import { Alert, Button, Collapse, Drawer, Spin, Tag, Timeline } from 'antd'
import { readQuery } from './api'
import type { ChatResponse, PublicTrace, TraceStep } from './api'
import { QueryResult } from './query-result'

const missing = '未提供'
const phases: Record<string, string> = { preflight: '预检', execution_recheck: '执行前复核' }
const agents: Record<string, string> = { data_analysis: 'Data Analysis Agent', knowledge: 'Knowledge Agent' }

function stepTitle(step: TraceStep) {
  if (step.kind === 'metric') return 'SQL 指标'
  if (step.kind === 'catalog' || step.tool === 'catalog_exact') return 'Catalog'
  if (step.tool?.startsWith('pgvector-')) return `RAG · ${step.tool}`
  return step.tool || step.kind || '事件'
}

function stepDetails(step: TraceStep) {
  const list = (values?: string[]) => values ? values.join('、') || '无' : missing
  const fields = [
    ['Plan ID', step.plan_id], ['Task ID', step.task_id], ['Attempt ID', step.attempt_id],
    ['Trace ID', step.trace_id], ['Capability Trace ID', step.capability_trace_id],
    ['专业 Agent', step.agent && (agents[step.agent] ?? step.agent)],
    ['工具', step.tool], ['执行顺序', step.schedule_position],
    ['任务或工具耗时', step.elapsed_ms != null ? `${step.elapsed_ms} ms` : undefined],
    ['专业 Agent 耗时', step.agent_elapsed_ms != null ? `${step.agent_elapsed_ms} ms` : undefined],
    ['依赖任务', list(step.depends_on)], ['失败依赖', list(step.failed_dependencies)],
    ['状态原因', step.code], ['数据来源', step.sources?.join('、') || missing],
  ]
  return <dl className="trace-details">{fields.map(([label, value]) =>
    <div key={label}><dt>{label}</dt><dd>{value ?? missing}</dd></div>)}</dl>
}

function StepList({ steps, timeline = false }: { steps?: TraceStep[]; timeline?: boolean }) {
  if (!steps?.length) return <p>{missing}</p>
  const ordered = timeline ? [...steps].sort((a, b) =>
    (a.schedule_position ?? Infinity) - (b.schedule_position ?? Infinity)) : steps
  const items = ordered.map((step, index) => ({
    key: index,
    label: <>{step.capability_phase ? phases[step.capability_phase] ?? step.capability_phase : stepTitle(step)}
      {step.metric_id && ` · ${step.metric_id}`} <Tag>{step.status ?? missing}</Tag></>,
    children: stepDetails(step),
  }))
  return timeline
    ? <Timeline items={items.map(item => ({ key: item.key, content: <Collapse size="small" items={[item]} /> }))} />
    : <Collapse size="small" items={items} />
}

function compareTrace(current?: PublicTrace, saved?: PublicTrace) {
  if (!current || !saved) return '未提供可对照的当前与已保存 Trace。'
  let compared = 0
  let omitted = false
  let mismatch = false
  function check(a?: string | null, b?: string | null) {
    if (a == null && b == null) return
    if (a == null || b == null) omitted = true
    else {
      compared++
      if (a !== b) mismatch = true
    }
  }
  check(current.plan_id, saved.plan_id)
  check(current.attempt_id, saved.attempt_id)
  for (const section of ['tasks', 'capability', 'tools'] as const) {
    const identity = (step: TraceStep) => section === 'tasks' ? step.task_id : step.trace_id ?? step.task_id
    const live = current[section] ?? []
    const stored = saved[section] ?? []
    const ids = new Set([...live, ...stored].map(identity).filter(Boolean))
    const completeIds = live.length > 0 && stored.length > 0 && [...live, ...stored].every(identity)
    for (const id of ids) {
      const a = live.find(step => identity(step) === id)
      const b = stored.find(step => identity(step) === id)
      check(a && identity(a), b && identity(b))
      if (completeIds && (!a || !b)) mismatch = true
      if (a && b) {
        for (const key of ['plan_id', 'task_id', 'attempt_id', 'trace_id', 'capability_trace_id', 'capability_phase'] as const) {
          check(a[key], b[key])
        }
      }
    }
  }
  if (mismatch) return '当前回执与已保存 Trace 的关联字段存在差异。'
  if (!compared) return '未提供可对照的 Trace 标识。'
  return omitted ? '双方均提供的 Trace 标识一致。部分标识仅在一方提供。' : '当前回执与已保存 Trace 的可对照标识一致。'
}

export function QueryHistory({ queryId, current, onClose, onOpen }: {
  queryId: string; current?: ChatResponse; onClose: () => void; onOpen: (id: string) => void
}) {
  const [saved, setSaved] = useState<ChatResponse>()
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    let active = true
    void readQuery(queryId).then(
      value => { if (active) setSaved(value) },
      failure => { if (active) setError(failure instanceof Error ? failure.message : '读取历史失败。') },
    )
    return () => { active = false }
  }, [queryId, retry])

  const trace = saved?.trace
  const previousQueryId = trace?.previous_query_id
  const attempts = [...new Set([trace?.attempt_id, ...(trace?.tasks ?? []).map(task => task.attempt_id)].filter(Boolean))]
  return <Drawer open onClose={onClose} title="查询历史详情" size="large">
    <p className="history-id">查询 ID <code>{queryId}</code></p>
    <p>{current ? '当前页面有原始回执。' : '当前页面未保存该查询的原始回执。'}</p>
    {error ? <Alert type="error" showIcon title={error}
      action={<Button onClick={() => { setError(''); setRetry(value => value + 1) }}>重新读取</Button>} />
      : !saved ? <Spin description="正在读取已保存记录"><div className="history-loading" /></Spin>
      : <div className="history-summary">
        <h3>后端已保存的查询摘要</h3>
        <QueryResult result={saved} busy onClarify={() => {}} />
        {saved.hitl && <>
          <h3>人工澄清</h3>
          <ol>{saved.hitl.events.map((event, index) => <li key={index}>
            {({ paused: '等待澄清', resuming: '收到答复，继续原运行', completed: '恢复完成',
               failed: '恢复失败', rejected: '请求被拒绝' } as Record<string, string>)[event.status] ?? event.status}
            {event.metric_id && ` · ${event.metric_id}`}{event.code && ` · ${event.code}`}
          </li>)}</ol>
        </>}
        <h3>Agent 执行 Trace</h3>
        <p>{compareTrace(current?.trace, trace)}</p>
        <dl className="trace-details">
          <div><dt>Plan ID</dt><dd>{trace?.plan_id ?? missing}</dd></div>
          <div><dt>Trace ID</dt><dd>{trace?.trace_id ?? missing}</dd></div>
          <div><dt>Attempt ID</dt><dd>{attempts.join('、') || missing}</dd></div>
        </dl>
        {previousQueryId && <Button onClick={() => onOpen(previousQueryId)}>
          查看上一次执行记录
        </Button>}
        <h4>任务时间线</h4>
        <StepList timeline steps={trace?.tasks} />
        <h4>Capability 校验</h4>
        <StepList steps={trace?.capability} />
        <h4>工具事件</h4>
        <StepList steps={trace?.tools} />
      </div>}
  </Drawer>
}
