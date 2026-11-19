import { useMemo, useRef, useState } from 'react'
import { Alert, Button } from 'antd'
import { createXlsxArtifact, downloadXlsxArtifact, RequestError, type ChatResponse } from './api'
import { exportControl, exportSession, type ExportStage } from './xlsx-export'

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

export function ExportExcel({ queryId, eligibility }: { queryId: string; eligibility?: ChatResponse['xlsx_export'] }) {
  const session = useMemo(() => exportSession(queryId, {
    create: createXlsxArtifact, download: downloadXlsxArtifact, save,
  }), [queryId])
  const [stage, setStage] = useState<ExportStage>('idle')
  const [error, setError] = useState('')
  const pending = useRef(false)
  const busy = stage === 'creating' || stage === 'downloading'
  const control = exportControl(eligibility)
  async function start() {
    if (pending.current || control.disabled) return
    pending.current = true
    setError('')
    try {
      await session.run(setStage)
    } catch (error) {
      setError(error instanceof RequestError && error.status === 409
        ? error.message
        : error instanceof RequestError && [401, 403, 404].includes(error.status ?? 0)
          ? '报表或源查询不存在，或当前身份无权访问。请确认访问权限。'
          : error instanceof Error ? error.message : '导出失败，请重试。')
    } finally {
      pending.current = false
    }
  }
  return <div className="excel-export">
    <Button aria-label="导出 Excel" loading={busy} disabled={busy || control.disabled} onClick={start}>导出 Excel</Button>
    {control.disabled && <Alert type="info" title={control.message} />}
    <span role="status">{stage === 'creating' ? '正在生成报表…' : stage === 'downloading' ? '正在下载…'
      : stage === 'completed' ? '已提交浏览器下载。' : ''}</span>
    {error && <Alert type="warning" showIcon title={error}
      description={session.hasArtifact() ? '已保留报表 ID，再次点击只重试下载，不重新生成报表。' : undefined} />}
  </div>
}
