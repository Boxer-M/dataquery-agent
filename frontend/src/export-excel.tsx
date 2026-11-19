import { useMemo, useRef, useState } from 'react'
import { Alert, Button } from 'antd'
import { createXlsxArtifact, downloadXlsxArtifact, RequestError } from './api'
import { exportSession, type ExportStage } from './xlsx-export'

function save(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function ExportExcel({ queryId }: { queryId: string }) {
  const session = useMemo(() => exportSession(queryId, {
    create: createXlsxArtifact, download: downloadXlsxArtifact, save,
  }), [queryId])
  const [stage, setStage] = useState<ExportStage>('idle')
  const [error, setError] = useState('')
  const pending = useRef(false)
  const busy = stage === 'creating' || stage === 'downloading'
  async function start() {
    if (pending.current) return
    pending.current = true
    setError('')
    try {
      await session.run(setStage)
    } catch (error) {
      setError(error instanceof RequestError && error.status === 409
        ? '该查询缺少可导出快照或不符合格式。请主动重新运行完整指标查询后再导出；此操作不会自动重跑查询。'
        : error instanceof RequestError && [401, 403, 404].includes(error.status ?? 0)
          ? '报表或源查询不存在，或当前身份无权访问。请确认访问权限。'
          : error instanceof Error ? error.message : '导出失败，请重试。')
    } finally {
      pending.current = false
    }
  }
  return <div className="excel-export">
    <Button aria-label="导出 Excel" loading={busy} disabled={busy} onClick={start}>导出 Excel</Button>
    <span role="status">{stage === 'creating' ? '正在生成报表…' : stage === 'downloading' ? '正在下载…'
      : stage === 'completed' ? '已提交浏览器下载。' : ''}</span>
    {error && <Alert type="warning" showIcon title={error}
      description={session.hasArtifact() ? '已保留报表 ID，再次点击只重试下载，不重新生成报表。' : undefined} />}
  </div>
}
