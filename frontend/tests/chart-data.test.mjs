import assert from 'node:assert/strict'
import { test } from 'node:test'
import { orderChartData } from '../src/chart-data.ts'

const metadata = {
  version: '1',
  metric: { metric_id: 'order_count', name: '订单量', unit: '笔', unit_type: 'count', sources: ['dws.order_day'] },
  dimension: { field: 'month', type: 'time', semantic: 'month', order: 'calendar_asc' },
  window: { start_date: '2025-01-01', end_date: '2025-03-01', end_exclusive: true },
  row_order: [1, 0], value_path: ['value'],
}
const result = {
  status: 'completed', rows_truncated: false, row_count: 2, visualization: metadata,
  rows: [{ month: '2025-02', value: '12' }, { month: '2025-01', value: '0009' }],
}

test('reads contract ordering and preserves raw strings and rows', () => {
  const original = structuredClone(result)
  assert.deepEqual(orderChartData(result)?.points, [
    { label: '2025-01', value: 9, raw: '0009', rowIndex: 1 },
    { label: '2025-02', value: 12, raw: '12', rowIndex: 0 },
  ])
  assert.deepEqual(result, original)
  const grouped = {
    ...result,
    visualization: {
      ...metadata, value_path: ['metrics', 'order_count', 'value'],
      dimension: { field: 'bucket', type: 'category', semantic: 'channel', order: 'category_code_asc' },
    },
    rows: ['web', 'mobile'].map(bucket => ({ bucket, metrics: { order_count: { value: '10' } } })),
  }
  assert.equal(orderChartData(grouped)?.kind, 'bar')
  assert.deepEqual(orderChartData(grouped)?.points.map(point => point.label), ['mobile', 'web'])
})

test('rejects incomplete, unknown, imprecise or inconsistent contracts', () => {
  for (const patch of [
    { version: '2' }, { row_order: [1, 1] }, { row_order: [0] }, { row_order: [1, 2] },
    { row_order: [0, 1] }, { row_order: [1, '0'] }, { value_path: ['constructor', 'value'] },
    { window: { ...metadata.window, start_date: '2025-02-30' } }, { metric: undefined },
  ]) assert.equal(orderChartData({ ...result, visualization: { ...metadata, ...patch } }), undefined, JSON.stringify(patch))
  for (const patch of [
    { visualization: undefined }, { visualization_unavailable_reason: 'summary_only' }, { rows_truncated: true },
    { rows: [result.rows[0]] }, { row_count: 3 },
    ...['9007199254740993', '2.00000000000000001', null, 123].map(value => ({
      rows: [result.rows[0], { ...result.rows[1], value }],
    })),
  ]) assert.equal(orderChartData({ ...result, ...patch }), undefined, JSON.stringify(patch))
})
