import { useRef, useState } from 'react'
import { Alert, Button, Checkbox, Drawer, Input, Select, Tag } from 'antd'
import { UploadOutlined } from '@ant-design/icons'
import { publishPdf, readDocument, readDocumentPage, reviewPdf, uploadPdf } from './document-api'
import type { DocumentPage, DocumentReceipt } from './document-api'
import './document-workbench.css'

const states = { unreviewed: '待审核', reviewed: '已审核', published: '已发布' }
const pageStates = { ok: '提取完成', no_text: '空白页', partial: '部分提取', error: '提取失败' }
const method = (value: string) => value === 'ocr' ? 'OCR' : value === 'native_text' ? '原生文本' : value

export default function DocumentWorkbench({ open, onClose, onAsk }: {
  open: boolean; onClose: () => void; onAsk: (title: string) => void
}) {
  const [selection, setSelection] = useState<File>()
  const [lookup, setLookup] = useState('')
  const [fileLabel, setFileLabel] = useState<{ name: string; size: number }>()
  const [document, setDocument] = useState<DocumentReceipt>()
  const [page, setPage] = useState<DocumentPage>()
  const [pageNumber, setPageNumber] = useState<number>()
  const [corrections, setCorrections] = useState<Record<number, string>>({})
  const [title, setTitle] = useState('')
  const [basis, setBasis] = useState('')
  const [dirty, setDirty] = useState(false)
  const [consent, setConsent] = useState(false)
  const [superseded, setSuperseded] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const lock = useRef(false)

  async function run(label: string, operation: () => Promise<void>, failureHint = '') {
    if (lock.current) return
    lock.current = true
    setBusy(label)
    setError('')
    try { await operation() }
    catch (reason) {
      setError((reason instanceof Error ? reason.message : '请求失败。') + failureHint)
    } finally {
      lock.current = false
      setBusy('')
    }
  }

  async function loadPage(receipt: DocumentReceipt, number: number) {
    setPageNumber(number)
    setPage(undefined)
    const result = await readDocumentPage(receipt.document_id, number)
    if (result.version !== receipt.version || result.review_version !== receipt.review_version) {
      throw new Error('服务端文档版本已变更，请刷新文档状态。')
    }
    if (result.page?.page !== number) throw new Error('API 未返回所选页面。')
    setPage(result.page)
  }

  function adopt(receipt: DocumentReceipt, defaultTitle = '') {
    setDocument(receipt)
    setTitle(receipt.title ?? defaultTitle)
    setBasis(receipt.review_basis ?? '')
    setCorrections({})
    setDirty(false)
    setConsent(false)
  }

  function transfer(replace: boolean) {
    if (!selection || (replace && !document)) return
    const file = selection
    if (!/\.pdf$/i.test(file.name) || (file.type && file.type !== 'application/pdf') || !file.size || file.size > 10 * 1024 * 1024) {
      setError('请选择非空 PDF 文件，大小不得超过 10 MiB。')
      return
    }
    void run('正在上传并提取', async () => {
      const receipt = await uploadPdf(file, replace ? document?.document_id : undefined)
      setFileLabel({ name: file.name, size: file.size })
      setSelection(undefined)
      setSuperseded(replace && receipt.state !== 'published')
      adopt(receipt, file.name.replace(/\.pdf$/i, '').slice(0, 150))
      if (receipt.pages.length) await loadPage(receipt, receipt.pages[0].page)
    }, ' 如果已知 Document ID，可先打开已有文档核对。首次上传若回执丢失，创建结果待确认。')
  }

  function review() {
    if (!document || !title.trim() || !basis.trim()) return
    void run('正在提交审核', async () => {
      const reviewedText = { ...corrections }
      if (document.review_version) {
        for (const row of document.pages) {
          if (row.status !== 'ok' || row.page in reviewedText) continue
          const previous = await readDocumentPage(document.document_id, row.page)
          if (previous.version !== document.version || previous.review_version !== document.review_version) {
            throw new Error('服务端文档版本已变更，请刷新文档状态。')
          }
          if (previous.page?.reviewed_text && previous.page.reviewed_text !== previous.page.text) {
            reviewedText[row.page] = previous.page.reviewed_text
          }
        }
      }
      const approved = await reviewPdf(document, { title: title.trim(), basis: basis.trim(), corrections: reviewedText })
      adopt(approved)
      if (pageNumber) await loadPage(approved, pageNumber)
    })
  }

  function publish() {
    if (!document || !document.review_version || document.state === 'unreviewed' || dirty || !consent) return
    void run('正在生成索引', async () => {
      const receipt = await publishPdf(document)
      setDocument(receipt)
      setSuperseded(false)
      setConsent(false)
    }, ' 当前界面保留最后一次确认的状态。若索引回执丢失，可刷新状态或重试同一审核版本。')
  }

  function reopen() {
    const id = lookup.trim().toLowerCase()
    if (!/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(id)) {
      setError('请输入完整的 Document ID。')
      return
    }
    void run('正在打开文档', async () => {
      const receipt = await readDocument(id)
      setSelection(undefined)
      setFileLabel(undefined)
      setSuperseded(false)
      adopt(receipt)
      if (receipt.pages.length) await loadPage(receipt, receipt.pages[0].page)
    })
  }

  function refresh() {
    if (!document) return
    void run('正在刷新', async () => {
      const receipt = await readDocument(document.document_id)
      if (receipt.version !== document.version) {
        setFileLabel(undefined)
        setSuperseded(receipt.state !== 'published')
      }
      if (dirty && receipt.version === document.version && receipt.review_version === document.review_version) {
        setDocument(receipt)
      } else {
        adopt(receipt, receipt.version === document.version ? title : '')
      }
      if (receipt.pages.length) await loadPage(receipt, pageNumber && receipt.pages.some(row => row.page === pageNumber)
        ? pageNumber : receipt.pages[0].page)
    })
  }

  return <Drawer title="文档知识库" size="min(1020px, 100vw)" open={open} onClose={onClose}
    extra={document && <Button onClick={refresh} disabled={!!busy}>刷新文档状态</Button>}>
    <div className="document-workbench" aria-busy={!!busy}>
      <section className="document-card">
        <div className="document-actions">
          <label className="document-file"><UploadOutlined /> 选择 PDF
            <input aria-label="选择 PDF" type="file" accept=".pdf,application/pdf" disabled={!!busy}
              onChange={event => { setSelection(event.target.files?.[0]); setError(''); event.target.value = '' }} />
          </label>
          {selection && <span>{selection.name} · {(selection.size / 1024).toFixed(1)} KiB</span>}
          <Button type="primary" onClick={() => transfer(false)} disabled={!selection || !!busy}>上传 PDF</Button>
          {document && <Button onClick={() => transfer(true)} disabled={!selection || !!busy}>替换当前 PDF</Button>}
        </div>
        <small>仅接受 PDF，最多 10 MiB、40 页。上传后可逐页核对提取文本。</small>
        <div className="document-actions">
          <Input aria-label="已有文档的 Document ID" placeholder="已有文档的 Document ID" value={lookup}
            maxLength={36} disabled={!!busy} style={{ width: 'min(370px, 100%)' }}
            onChange={event => setLookup(event.target.value)} onPressEnter={reopen} />
          <Button onClick={reopen} disabled={!lookup.trim() || !!busy}>打开已有文档</Button>
        </div>
      </section>
      {busy && <div className="loading" role="status">{busy}…</div>}
      {error && <Alert type="error" showIcon title={error} />}
      {superseded && <Alert type="info" showIcon title="已有发布的旧版本可能仍可用于问答，直到当前版本成功发布。" />}
      {document && <>
        <section className="document-card document-meta" aria-label="当前导入版本">
          <div><strong>{fileLabel?.name ?? document.title ?? 'PDF 文档'}</strong> <Tag color={document.state === 'published' ? 'green' : 'blue'}>{states[document.state]}</Tag></div>
          <span>{fileLabel && `${(fileLabel.size / 1024).toFixed(1)} KiB · `}{document.pages.length} 页</span>
          <span>Document ID <code>{document.document_id}</code></span>
          <span>当前导入版本 <code>{document.version}</code></span>
          {document.publication && <span>已发布知识版本 <code>{document.publication.version}</code>
            {' · '}{document.publication.indexed ? '已建立索引' : '已复用现有索引'}</span>}
        </section>
        <section className="document-card">
          <div className="document-actions">
            <Select aria-label="选择页码" value={pageNumber} disabled={!!busy}
              options={document.pages.map(row => ({ value: row.page, label: `第 ${row.page} 页 · ${method(row.method)} · ${pageStates[row.status]}` }))}
              onChange={number => void run('正在读取页面', () => loadPage(document, number))} />
            {page && <Tag color={page.status === 'error' ? 'red' : page.status === 'ok' ? 'green' : 'default'}>{pageStates[page.status]}</Tag>}
            {page?.error && <code>{page.error}</code>}
          </div>
          {page && <>
            <div className="document-columns">
              <div><h3>原始提取文本</h3><pre>{page.text || (page.status === 'no_text' ? '本页未提取到文本。' : '本页没有可用的提取文本。')}</pre></div>
              <div><h3>人工修订文本</h3>
                {page.status === 'ok' && page.text.trim() ? <Input.TextArea aria-label="人工修订文本" rows={6} maxLength={100000}
                  disabled={!!busy} value={corrections[page.page] ?? page.reviewed_text ?? page.text}
                  onChange={event => {
                    setCorrections(values => ({ ...values, [page.page]: event.target.value }))
                    setDirty(true)
                    setConsent(false)
                  }} /> : <p>本页不支持文本修订。</p>}
              </div>
            </div>
            {!!page.blocks?.length && <details className="result-details"><summary>查看 OCR 行级位置与置信度</summary>
              <ol className="document-blocks">{page.blocks.map((block, index) => <li key={index}>
                <strong>{block.text}</strong>
                <small>{typeof block.confidence === 'number' && `置信度 ${(block.confidence * 100).toFixed(1)}% `}
                  {block.bounds && `位置 [${block.bounds.join(', ')}]（归一化裁剪框）`}</small>
              </li>)}</ol>
            </details>}
          </>}
        </section>
        <section className="document-card">
          <div className="document-columns">
            <label>文档标题<Input value={title} maxLength={150} disabled={!!busy}
              onChange={event => { setTitle(event.target.value); setDirty(true); setConsent(false) }} /></label>
            <label>审核依据<Input.TextArea value={basis} maxLength={1000} rows={2} disabled={!!busy}
              onChange={event => { setBasis(event.target.value); setDirty(true); setConsent(false) }} /></label>
          </div>
          <div className="document-actions">
            <Button onClick={review} disabled={!!busy || !title.trim() || !basis.trim()
              || !document.pages.some(row => row.status === 'ok')
              || document.pages.some(row => row.status === 'error' || row.status === 'partial')}>提交审核</Button>
            {document.review_version && <small>审核版本 <code>{document.review_version}</code></small>}
          </div>
          {dirty && document.state !== 'unreviewed' && <small>当前修改待重新审核。</small>}
          <Checkbox checked={consent} disabled={!!busy || dirty || !document.review_version}
            onChange={event => setConsent(event.target.checked)}>授权将已审核标题和文本发送给 Gemini 生成向量</Checkbox>
          <div className="document-actions">
            <Button type="primary" onClick={publish} loading={busy === '正在生成索引'} disabled={!!busy || dirty || !consent
              || !document.review_version || document.state === 'unreviewed'}>{document.state === 'published' ? '再次发布' : '发布到知识库'}</Button>
            {document.state === 'published' && document.title && <Button onClick={() => onAsk(document.title!)}
              disabled={!!busy}>在聊天中询问此文档</Button>}
          </div>
        </section>
      </>}
    </div>
  </Drawer>
}
