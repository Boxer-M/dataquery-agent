/** Real Chrome + real query; injected transport failures are reported separately. */
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright-core'

const origin = process.env.WORKSPACE_ORIGIN ?? 'http://127.0.0.1:5173'
const directory = resolve(process.env.XLSX_UI_OUTPUT ?? fileURLToPath(new URL('../../artifacts/xlsx-ui', import.meta.url)))
const statsPath = process.env.XLSX_UI_STATS
assert.ok(statsPath, 'Use the instrumented local API and supply XLSX_UI_STATS')
await mkdir(directory, { recursive: true })
const stats = async () => JSON.parse(await readFile(statsPath, 'utf8'))
const browser = await chromium.launch({ executablePath: process.env.CHROME_BIN
  ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' })
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1050 }, acceptDownloads: true })
  const requests = [], errors = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('request', request => {
    if (request.url().startsWith(origin + '/api/')) {
      assert.equal(request.headers().authorization, undefined)
      requests.push({ path: new URL(request.url()).pathname, method: request.method() })
    }
  })
  await page.goto(origin)
  await page.getByText('API 已连接', { exact: true }).waitFor()
  await page.getByRole('button', { name: '新建会话' }).click()
  const answer = page.waitForResponse(response => response.url() === origin + '/api/chat', { timeout: 100_000 })
  await page.getByRole('textbox', { name: '输入问题' }).fill('2025年1月的订单量和成功支付金额是多少')
  await page.getByRole('textbox', { name: '输入问题' }).press('Enter')
  const result = await (await answer).json()
  assert.equal(result.status, 'completed', JSON.stringify(result.error))
  assert.equal(result.rows[0].metrics.order_count.value, '2312')
  assert.equal(result.rows[0].metrics.captured_payment_amount.value, '230864.57')
  const initial = await stats()
  const button = page.getByRole('button', { name: '导出 Excel', exact: true })
  await button.waitFor()
  const exportUrl = `${origin}/api/queries/${result.query_id}/artifacts/xlsx`

  await page.route(exportUrl, route => route.fulfill({ status: 409, contentType: 'application/json',
    body: JSON.stringify({ error: { code: 'query_export_unavailable', message: '快照不可用' } }) }))
  await button.click()
  await page.getByText(/请主动重新运行完整指标查询后再导出/).waitFor()
  await page.unroute(exportUrl)
  assert.equal(requests.filter(item => item.path === '/api/chat').length, 1)

  await page.route(exportUrl, route => route.fulfill({ status: 404, contentType: 'application/json',
    body: JSON.stringify({ error: { code: 'not_found', message: '没有可访问的记录' } }) }))
  await button.click()
  await page.getByText('报表或源查询不存在，或当前身份无权访问。请确认访问权限。').waitFor()
  await page.unroute(exportUrl)

  let creates = 0, downloadAttempts = 0
  await page.route(exportUrl, async route => {
    creates++
    await new Promise(resolve => setTimeout(resolve, 250))
    await route.continue()
  })
  await page.route('**/api/artifacts/*/download', route => {
    downloadAttempts++
    return downloadAttempts === 1 ? route.abort('failed') : route.continue()
  })
  const createdResponse = page.waitForResponse(response => response.url() === exportUrl && response.status() === 201)
  await button.evaluate(element => { element.click(); element.click() })
  const artifact = await (await createdResponse).json()
  await page.getByText('已保留报表 ID，再次点击只重试下载，不重新生成报表。').waitFor()
  assert.equal(creates, 1)
  await page.screenshot({ path: resolve(directory, 'download-retry.png'), fullPage: true })
  const downloaded = page.waitForEvent('download')
  await button.click()
  const file = await downloaded
  assert.equal(file.suggestedFilename(), artifact.filename)
  const path = resolve(directory, file.suggestedFilename())
  await file.saveAs(path)
  assert.equal(creates, 1)
  assert.equal(downloadAttempts, 2)
  assert.equal(requests.filter(item => item.path === '/api/chat').length, 1)
  const after = await stats()
  assert.equal(after.business_metric_sql, initial.business_metric_sql)
  assert.equal(after.real_gemini_calls, initial.real_gemini_calls)
  assert.equal(after.proposal_calls, initial.proposal_calls)
  assert.deepEqual(errors, [])
  await page.getByText('已提交浏览器下载。').waitFor()
  await page.screenshot({ path: resolve(directory, 'export-success.png'), fullPage: true })
  await writeFile(resolve(directory, 'browser.json'), JSON.stringify({
    mode: 'real Chrome + fixed model proposal + PostgreSQL; fault injections labelled below',
    query_id: result.query_id, artifact, downloaded_file: path,
    initial_query: initial, after_export: after,
    export_business_metric_sql: after.business_metric_sql - initial.business_metric_sql,
    export_gemini_calls: after.real_gemini_calls - initial.real_gemini_calls,
    fault_injection: { snapshot_409: true, access_404: true, download_network_failure_then_retry: true },
    actual_artifact_creates: creates, download_attempts: downloadAttempts,
    rapid_double_click_deduplicated: true, browser_authorization_header_absent: true,
  }, null, 2) + '\n')
  console.log(JSON.stringify({ query_id: result.query_id, artifact_id: artifact.artifact_id, file: path,
    initial, export_sql: 0, export_gemini: 0 }))
} finally {
  await browser.close()
}
