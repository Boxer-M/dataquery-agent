import { lazy, Suspense, useMemo, useState } from 'react'
import { Alert, Button, Table, Tag } from 'antd'
import type { Cell, ChatResponse, Citation, Row, Status } from './api'
import { orderChartData } from './chart-data'

const OrderChart = lazy(() => import('./order-chart').catch(() => ({
  default: () => <Alert showIcon type="warning" title="图表加载失败，可切换查看表格。" />,
})))

const statuses: Record<Status, { label: string; color: string }> = {
  completed: { label: '查询完成', color: 'success' },
  partial: { label: '部分完成', color: 'warning' },
  clarification_required: { label: '需要澄清', color: 'processing' },
  failed: { label: '查询失败', color: 'error' },
}

const labels: Record<string, string> = {
  bucket: '分组', value: '数值', month: '月份', start_date: '开始日期', end_date: '结束日期（不含）',
  numerator: '分子', denominator: '分母', unit: '单位', change: '变化量', change_pct: '变化比例',
  baseline: '基准期', current: '比较期',
}

function isRecord(value: Cell | undefined): value is Row {
  return value != null && typeof value === 'object' && !Array.isArray(value)
}

function cellText(value: Cell | undefined): string {
  if (value == null) return '—'
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}

function citationMetric(citation: Citation) {
  if (citation.source_type !== 'catalog' || !citation.source_location) return undefined
  return /^#\/metrics\[metric_id=([A-Za-z0-9_]+)\]$/.exec(citation.source_location)?.[1]
}

function CitationDetails({ citation }: { citation: Citation }) {
  const fields = [
    ['来源', citation.source_uri], ['位置', citation.source_location], ['类型', citation.source_type],
    ['版本', citation.document_version], ['文档 ID', citation.document_id], ['片段 ID', citation.chunk_id],
    ['页码', citation.page_number],
    ['审核文本行', citation.line_start && citation.line_end ? `${citation.line_start}–${citation.line_end}` : null],
    ['提取方式', citation.extraction_method], ['原件 SHA-256', citation.source_hash],
  ]
  return <details className="result-details">
    <summary>{citation.title ? `查看引用 · ${citation.title}` : '查看引用'}</summary>
    {citation.quote && <blockquote>{citation.quote}</blockquote>}
    <dl>{fields.filter(([, value]) => Boolean(value)).map(([label, value]) =>
      <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
  </details>
}

export function QueryResult({ result, busy, onClarify }: {
  result: ChatResponse; busy: boolean; onClarify: (text: string) => void
}) {
  const [tableSelected, setTableSelected] = useState(false)
  const chart = useMemo(() => orderChartData(result), [result])
  const showChart = chart && !tableSelected
  const state = statuses[result.status]
  const citations = result.citations.filter(item => item.document_id && item.chunk_id)
  const metricNames = new Map(result.definitions.flatMap(item => item.metric_id && item.name
    ? [[item.metric_id, item.name] as const] : []))
  for (const item of citations) {
    const id = citationMetric(item)
    if (id && item.title && !metricNames.has(id)) metricNames.set(id, item.title)
  }
  const rows = result.rows.map(({ metrics, ...row }) => {
    const values: Row = Object.create(null)
    if (isRecord(metrics)) {
      for (const [id, value] of Object.entries(metrics)) {
        values[id] = isRecord(value) && Object.hasOwn(value, 'value') ? value.value : value
      }
    }
    return { ...row, ...values }
  })
  const keys = [...new Set(rows.flatMap(Object.keys))]
  const scalar = rows.length === 1 && Object.hasOwn(rows[0], 'value') && rows[0].bucket === 'all'
    && keys.every(key => ['bucket', 'value', 'unit', 'month', 'start_date', 'end_date', 'numerator', 'denominator'].includes(key))
    && result.definitions.length === 1 ? rows[0] : undefined
  const unavailable = result.trace?.tasks?.filter(task => ['unavailable', 'failed', 'error', 'skipped', 'partial'].includes(task.status ?? '')) ?? []
  const otherCitations = citations.filter(item => !result.definitions.some(definition =>
    definition.metric_id && citationMetric(item) === definition.metric_id))

  return <>
    <div className="result-heading"><span>查询结果</span><Tag color={state.color}>{state.label}</Tag></div>
    {result.answer && <p className="answer-text">{result.answer}</p>}
    {result.error && <Alert type={result.status === 'failed' ? 'error' : 'warning'} showIcon
      title={result.error.message} description={result.error.code} />}
    {result.status === 'partial' && <Alert type="warning" showIcon
      title={rows.length ? '下方为已返回的结果，部分信息不可用。' : '部分信息不可用。'}
      description={unavailable.length > 0 && <ul>{unavailable.map((task, index) => {
        const id = task.metric_id
        return <li key={task.task_id ?? index}>{id ? metricNames.get(id) ?? id : '子任务'} · {
          task.kind === 'catalog' ? '正式定义引用不可用'
            : task.kind === 'pdf' ? (task.status === 'partial' ? 'PDF 证据不完整' : 'PDF 证据不可用')
              : task.kind === 'summary' ? '联合汇总未完成' : '任务未完成'
        }{task.code && `（${task.code}）`}</li>
      })}</ul>} />}
    {result.status === 'clarification_required' && <div className="choices">
      {result.clarification_choices.map((choice, index) => {
        const text = choice.metric_id || choice.name
        return text && <div key={index}>
          <Button disabled={busy} onClick={() => onClarify(text)}>{choice.name || choice.metric_id}</Button>
          {choice.definition && <span>{choice.definition}</span>}
        </div>
      })}
      <small>也可以在输入框中补充完整问题。</small>
    </div>}
    {chart && <div className="chart-view-switch" aria-label="结果视图">
      <Button size="small" aria-label="图表" aria-pressed={!tableSelected} onClick={() => setTableSelected(false)}>图表</Button>
      <Button size="small" aria-label="表格" aria-pressed={tableSelected} onClick={() => setTableSelected(true)}>表格</Button>
    </div>}
    {!chart && result.visualization && <small>图表数据未通过格式或精度校验，已保留原始结果。</small>}
    {showChart ? <Suspense fallback={<p role="status">正在加载图表</p>}>
      <OrderChart data={chart} />
    </Suspense> : scalar ? <div className="metric-card">
      <h3>{result.definitions[0].name || metricNames.get(result.definitions[0].metric_id ?? '')
        || result.definitions[0].metric_id || '指标数值'}</h3>
      <strong className="metric-value">{cellText(scalar.value)}</strong>
      {typeof scalar.unit === 'string' && <span>{scalar.unit}</span>}
      <div className="result-meta">{['month', 'start_date', 'end_date', 'numerator', 'denominator']
        .filter(key => scalar[key] != null).map(key => <span key={key}>{labels[key]} {cellText(scalar[key])}</span>)}</div>
    </div> : rows.length > 0 && <Table<Row>
      size="small" pagination={false} scroll={{ x: 'max-content' }}
      columns={keys.map(key => ({ key, title: metricNames.get(key) ?? (Object.hasOwn(labels, key) ? labels[key] : key),
        render: (_, row) => cellText(Object.hasOwn(row, key) ? row[key] : undefined) }))}
      dataSource={rows} rowKey={(_, index) => String(index)}
    />}
    {result.rows_truncated && <Alert type="warning" showIcon title="结果已截断，仅显示 API 返回的部分数据。" />}
    {(result.definitions.length > 0 || citations.length > 0) && <section className="metric-definitions">
      <h3>{result.definitions.length ? '指标定义与引用' : '来源引用'}</h3>
      {result.definitions.map((definition, index) => {
        const matching = citations.filter(item => definition.metric_id && citationMetric(item) === definition.metric_id)
        return <div className="definition" key={index}>
          <h4>{definition.name || metricNames.get(definition.metric_id ?? '') || definition.metric_id || '指标定义'}</h4>
          {definition.definition && <p>{definition.definition}</p>}
          {definition.definition_version && <small>定义版本 {definition.definition_version}</small>}
          {matching.length
            ? matching.map((item, position) => <CitationDetails key={position} citation={item} />)
            : <small className="unavailable">未返回该指标的有效引用</small>}
        </div>
      })}
      {otherCitations.map((item, index) => <CitationDetails key={index} citation={item} />)}
    </section>}
    {(rows.length > 0 || result.row_count === 0 || result.rows_truncated) && <details className="result-details">
      <summary>查看原始结果</summary><pre>{JSON.stringify(result.rows, null, 2)}</pre>
    </details>}
    <div className="result-meta">
      {typeof result.row_count === 'number' && <span>{result.row_count} 行</span>}
      {result.query_id && <span>查询 ID <code>{result.query_id}</code></span>}
      {result.replayed && <span>已重放原查询回执</span>}
    </div>
  </>
}
