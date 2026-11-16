import assert from 'node:assert/strict'
import { chromium } from 'playwright-core'

const origin = process.env.WORKSPACE_ORIGIN ?? 'http://127.0.0.1:5173'
const browser = await chromium.launch({
  executablePath: process.env.CHROME_BIN ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
})
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } })
  const requests = []
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('request', request => {
    if (new URL(request.url()).pathname.startsWith('/api/')) {
      requests.push({ method: request.method(), path: new URL(request.url()).pathname })
      assert.equal(request.headers().authorization, undefined)
    }
  })
  await page.addInitScript(() => {
    window.documentTestResponses = []
    const originalFetch = window.fetch
    window.fetch = async (input, options) => {
      const response = await originalFetch(input, options)
      if (options?.method === 'POST' && /^\/api\/(documents|chat)/.test(input)) {
        const body = await response.clone().json()
        window.documentTestResponses.push({ path: input, status: response.status, body })
      }
      return response
    }
  })
  await page.goto(origin)
  await page.getByText('API 已连接', { exact: true }).waitFor()
  await page.getByRole('button', { name: '文档知识库' }).click()
  const drawer = page.getByRole('dialog', { name: '文档知识库' })
  const selectFile = path => drawer.getByLabel('选择 PDF', { exact: true }).setInputFiles(path)
  const button = name => drawer.getByRole('button', { name, exact: true })
  async function captured(path, index) {
    await page.waitForFunction(({ path, index }) =>
      window.documentTestResponses.filter(item => item.path === path).length > index, { path, index }, { timeout: 100_000 })
    return page.evaluate(({ path, index }) =>
      window.documentTestResponses.filter(item => item.path === path)[index], { path, index })
  }
  const count = path => page.evaluate(path =>
    window.documentTestResponses.filter(item => item.path === path).length, path)
  async function act(path, name, expectedStatus) {
    const index = await count(path)
    await button(name).click()
    const { body, status } = await captured(path, index)
    assert.equal(status, expectedStatus, JSON.stringify(body))
    return body
  }

  const base = '/api/documents'
  await selectFile(process.env.WORKSPACE_NATIVE_PDF)
  await page.route('**/api/documents', route => route.abort('failed'), { times: 1 })
  await button('上传 PDF').click()
  await drawer.getByText(/首次上传若回执丢失，创建结果待确认/).waitFor()
  assert.equal(requests.filter(item => item.path === base && item.method === 'POST').length, 1)
  const native = await act(base, '上传 PDF', 201)
  assert.equal(native.pages[0].method, 'native_text')
  await drawer.getByText('原始提取文本', { exact: true }).waitFor()
  await selectFile(process.env.WORKSPACE_SCAN_PDF)
  const imported = await act(base, '上传 PDF', 201)
  const path = base + '/' + imported.document_id
  assert.equal(imported.state, 'unreviewed')
  await drawer.getByRole('combobox', { name: '选择页码' }).click()
  await page.getByText('第 2 页 · OCR · 提取完成', { exact: true }).last().click()
  const correction = drawer.getByRole('textbox', { name: '人工修订文本' })
  await correction.waitFor()
  const original = await correction.inputValue()
  assert.match(original, /Order count: 48/)
  await drawer.getByText('查看 OCR 行级位置与置信度').click()
  await drawer.getByText(/置信度 .*位置 \[/).first().waitFor()
  await drawer.getByText('查看 OCR 行级位置与置信度').click()
  await correction.fill(original + '\nReviewed UI appendix: confirmed by human review.')
  await drawer.getByText('文档标题', { exact: true }).locator('input').fill('Document UI synthetic PDF verification')
  await drawer.getByText('审核依据', { exact: true }).locator('textarea').fill('Compared with the approved synthetic PDF.')
  assert.ok(await button('发布到知识库').isDisabled())
  const reviewed = await act(path + '/review', '提交审核', 200)
  assert.equal(reviewed.state, 'reviewed')
  await drawer.getByText('已审核', { exact: true }).waitFor()
  await correction.waitFor()
  assert.match(await correction.inputValue(), /Reviewed UI appendix/)
  if (process.env.WORKSPACE_DOCUMENT_SCREENSHOTS) {
    await page.screenshot({ path: process.env.WORKSPACE_DOCUMENT_SCREENSHOTS + '-review.png', animations: 'disabled' })
  }

  await drawer.getByRole('checkbox').check()
  const failed = await act(path + '/publish', '发布到知识库', 503)
  assert.equal(failed.error.code, 'document_service_unavailable')
  await drawer.getByText(/当前界面保留最后一次确认的状态/).waitFor()
  assert.ok(await drawer.getByText('已审核', { exact: true }).isVisible())
  assert.equal(await drawer.getByText('已发布', { exact: true }).count(), 0)
  const published = await act(path + '/publish', '发布到知识库', 200)
  assert.equal(published.state, 'published')
  await drawer.getByRole('checkbox').check()
  const replay = await act(path + '/publish', '再次发布', 200)
  assert.equal(replay.publication.version, published.publication.version)
  assert.equal(replay.publication.indexed, false)
  await drawer.getByText(/已复用现有索引/).waitFor()
  if (process.env.WORKSPACE_DOCUMENT_SCREENSHOTS) {
    await page.screenshot({ path: process.env.WORKSPACE_DOCUMENT_SCREENSHOTS + '-published.png', animations: 'disabled' })
  }

  await button('在聊天中询问此文档').click()
  const composer = page.getByRole('textbox', { name: '输入问题' })
  assert.match(await composer.inputValue(), /Document UI synthetic PDF verification/)
  const chatCount = await count('/api/chat')
  await composer.fill('请从「Document UI synthetic PDF verification」的 PDF 扫描页逐字摘录以 Reviewed UI appendix 开头的整行。')
  await composer.press('Enter')
  const { body: answer, status } = await captured('/api/chat', chatCount)
  assert.equal(status, 200)
  const citation = answer.citations.find(item => item.document_id === published.publication.document_id)
  assert.equal(citation?.page_number, 2)
  assert.equal(citation.quote, 'Reviewed UI appendix: confirmed by human review.')
  assert.equal(citation.source_hash, imported.file_sha256)
  const card = page.locator('.message').last()
  await card.getByText(/PDF 第 2 页/).waitFor()
  await card.getByText('查看引用 · Document UI synthetic PDF verification').click()
  await card.getByText(citation.quote, { exact: true }).waitFor()
  if (process.env.WORKSPACE_DOCUMENT_SCREENSHOTS) {
    await page.screenshot({ path: process.env.WORKSPACE_DOCUMENT_SCREENSHOTS + '-citation.png', animations: 'disabled' })
  }
  await page.getByRole('button', { name: '文档知识库' }).click()
  await selectFile(process.env.WORKSPACE_NATIVE_PDF)
  const replacement = await act(path + '/versions', '替换当前 PDF', 201)
  assert.notEqual(replacement.version, imported.version)
  await drawer.getByText(/已有发布的旧版本可能仍可用于问答/).waitFor()
  assert.ok(await button('发布到知识库').isDisabled())
  const stale = await page.evaluate(async ({ path, version, review_version }) => {
    const response = await fetch(path + '/publish', {
      method: 'POST', headers: { 'X-DataQuery-Client': 'workspace', 'Content-Type': 'application/json' },
      body: JSON.stringify({ version, review_version, external_embedding_approved: true }),
    })
    return { status: response.status, code: (await response.json()).error.code }
  }, { ...reviewed, path })
  assert.deepEqual(stale, { status: 409, code: 'stale_upload_version' })
  await page.reload()
  await page.getByRole('button', { name: '文档知识库' }).click()
  await drawer.getByRole('textbox', { name: '已有文档的 Document ID' }).fill(imported.document_id)
  await button('打开已有文档').click()
  await drawer.getByText(replacement.version, { exact: true }).waitFor()
  await drawer.getByText('待审核', { exact: true }).waitFor()
  assert.ok(await button('发布到知识库').isDisabled())
  assert.deepEqual(errors, [])
  console.log(JSON.stringify({
    native: native.pages, scanned: imported.pages, review_version: reviewed.review_version,
    publication: published.publication, repeat_publication: replay.publication, citation, query_id: answer.query_id,
    failure: failed.error.code, upload_network_attempts: 1, stale, reopened_after_refresh: true, browser_authorization_sent: false,
  }))
} finally {
  await browser.close()
}
