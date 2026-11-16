import { useEffect, useRef } from 'react'
import { init, use } from 'echarts/core'
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { OrderChartData } from './chart-data'

use([BarChart, LineChart, GridComponent, TooltipComponent, CanvasRenderer])

export default function OrderChart({ data }: { data: OrderChartData }) {
  const container = useRef<HTMLDivElement>(null)
  const axisName = data.kind === 'line' ? '月份' : '渠道'

  useEffect(() => {
    if (!container.current) return
    const chart = init(container.current)
    chart.setOption({
      animation: false,
      color: ['#107a69'],
      grid: { left: 62, right: 34, top: 38, bottom: 56 },
      xAxis: {
        type: 'category', name: axisName, nameLocation: 'middle', nameGap: 27,
        boundaryGap: data.kind === 'bar', data: data.points.map(point => point.label),
      },
      yAxis: { type: 'value', name: `${data.metric.name}（${data.metric.unit}）`, minInterval: 1 },
      tooltip: {
        trigger: 'item', renderMode: 'richText',
        formatter: (item: { dataIndex: number }) => {
          const point = data.points[item.dataIndex]
          return `${axisName} ${point.label}\n${data.metric.name} ${point.raw} ${data.metric.unit}`
        },
      },
      series: [{
        name: data.metric.name, type: data.kind,
        data: data.points.map(point => ({ name: point.label, value: point.value,
          raw: point.raw, rowIndex: point.rowIndex })),
      }],
    })
    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(container.current)
    return () => { observer.disconnect(); chart.dispose() }
  }, [data, axisName])

  return <figure className="order-chart">
    <figcaption>
      <strong>{data.metric.name} · {axisName}</strong>
      <span>{data.window.start_date} 至 {data.window.end_date}（不含结束日）</span>
      <span>来源 {data.metric.sources.join('、')}</span>
    </figcaption>
    <div className="order-chart-plot" ref={container} role="img" aria-label={`${data.metric.name}${axisName}图`} />
  </figure>
}
