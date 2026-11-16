import type { ChatResponse, Visualization } from './api'

export type OrderChartData = {
  kind: 'line' | 'bar'
  metric: Visualization['metric']
  window: Visualization['window']
  points: { label: string; raw: string; value: number; rowIndex: number }[]
}

function validDate(value: unknown): value is string {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const parsed = new Date(value)
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value
}

export function orderChartData(result: ChatResponse): OrderChartData | undefined {
  const { visualization: chart, rows } = result
  if (result.status !== 'completed' || result.rows_truncated !== false || result.visualization_unavailable_reason
      || !Array.isArray(rows) || rows.length < 2 || (result.row_count != null && result.row_count !== rows.length)
      || chart?.version !== '1') return

  const { metric, dimension, window, value_path: path, row_order: order } = chart
  if (metric?.metric_id !== 'order_count' || metric.unit_type !== 'count'
      || typeof metric.name !== 'string' || !metric.name.trim() || typeof metric.unit !== 'string' || !metric.unit.trim()
      || !Array.isArray(metric.sources) || !metric.sources.length
      || metric.sources.some(source => typeof source !== 'string' || !source.trim())
      || window?.end_exclusive !== true || !validDate(window.start_date) || !validDate(window.end_date)
      || window.start_date >= window.end_date) return

  const monthly = dimension?.type === 'time' && dimension.semantic === 'month'
    && dimension.field === 'month' && dimension.order === 'calendar_asc'
  const channel = dimension?.type === 'category' && dimension.semantic === 'channel'
    && dimension.field === 'bucket' && dimension.order === 'category_code_asc'
  if (!monthly && !channel) return

  const flat = Array.isArray(path) && path.length === 1 && path[0] === 'value'
  const nested = Array.isArray(path) && path.length === 3
    && path[0] === 'metrics' && path[1] === metric.metric_id && path[2] === 'value'
  if ((!flat && !nested) || !Array.isArray(order) || order.length !== rows.length
      || new Set(order).size !== rows.length) return

  const points: OrderChartData['points'] = []
  for (const index of order) {
    if (!Number.isInteger(index) || index < 0 || index >= rows.length) return
    const label = rows[index]?.[dimension.field]
    const labelFormat = monthly ? /^\d{4}-(0[1-9]|1[0-2])$/ : /^[a-z][a-z0-9_-]{0,31}$/
    if (typeof label !== 'string' || !labelFormat.test(label)
        || (points.length > 0 && label <= points[points.length - 1].label)) return
    if (monthly && (label < window.start_date.slice(0, 7) || label + '-01' >= window.end_date)) return
    let cell: unknown = rows[index]
    for (const key of path) {
      if (!cell || typeof cell !== 'object' || Array.isArray(cell) || !Object.hasOwn(cell, key)) return
      cell = (cell as Record<string, unknown>)[key]
    }
    // The v1 unit is an order count. Only exact integers enter the value axis.
    if (typeof cell !== 'string' || !/^[0-9]+$/.test(cell)) return
    const value = Number(cell)
    if (!Number.isSafeInteger(value)) return
    points.push({ label, raw: cell, value, rowIndex: index })
  }
  return { kind: monthly ? 'line' : 'bar', metric, window, points }
}
