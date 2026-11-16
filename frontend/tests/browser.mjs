import assert from 'node:assert/strict'
import { chromium } from 'playwright-core'

const origin = 'http://127.0.0.1:5173'
const browser = await chromium.launch({
  executablePath: process.env.CHROME_BIN ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
})
try {
  const page = await browser.newPage({ viewport: { width: 1360, height: 850 } })
  const submitted = []
  const browserErrors = []
  page.on('pageerror', (error) => browserErrors.push(error.message))
  page.on('request', (request) => {
    if (request.url() === origin + '/api/chat') {
      submitted.push(request.postDataJSON())
      assert.equal(request.headers().authorization, undefined)
    }
  })
  await page.goto(origin)
  await page.getByText('API 已连接', { exact: true }).waitFor()
  if (process.env.WORKSPACE_EMPTY_SCREENSHOT) await page.screenshot({ path: process.env.WORKSPACE_EMPTY_SCREENSHOT })
  const created = page.waitForResponse(response => response.url() === origin + '/api/conversations')
  await page.getByRole('button', { name: '新建会话' }).click()
  const { conversation_id } = await (await created).json()
  const input = page.getByRole('textbox', { name: '输入问题' })
  async function submit(question) {
    await input.fill(question)
    await input.press('Enter')
  }

  const firstResponse = page.waitForResponse(response => response.url() === origin + '/api/chat', { timeout: 100_000 })
  await submit('2025年1月订单量是多少？')
  const first = await (await firstResponse).json()
  assert.equal(first.status, 'completed', first.error?.code)
  assert.equal(first.rows[0].value, '2312')
  await page.getByRole('cell', { name: '2312', exact: true }).waitFor()
  if (process.env.WORKSPACE_SCREENSHOT) await page.screenshot({ path: process.env.WORKSPACE_SCREENSHOT })

  const nextResponse = page.waitForResponse(response => response.url() === origin + '/api/chat', { timeout: 100_000 })
  await submit('那2月份呢？')
  const second = await (await nextResponse).json()
  assert.equal(second.status, 'completed')
  assert.equal(second.rows[0].value, '2049')
  await page.getByRole('cell', { name: '2049', exact: true }).waitFor()
  assert.equal(first.conversation_id, conversation_id)
  assert.equal(second.conversation_id, conversation_id)
  assert.notEqual(first.message_id, second.message_id)

  const clarifyResponse = page.waitForResponse(response => response.url() === origin + '/api/chat')
  await submit('2025年1月销售额')
  const clarify = await (await clarifyResponse).json()
  assert.equal(clarify.status, 'clarification_required')
  await page.getByText('需要澄清', { exact: true }).waitFor()

  // Simulate only the transport failure, then let the real API handle the retry.
  await page.route('**/api/chat', route => route.abort('failed'), { times: 1 })
  await submit('折扣后商品销售金额')
  const retry = page.getByRole('button', { name: '重试原消息' })
  await retry.waitFor()
  const failedMessage = submitted.at(-1)
  const retriedResponse = page.waitForResponse(response => response.url() === origin + '/api/chat')
  await retry.click()
  const retried = await (await retriedResponse).json()
  assert.deepEqual(submitted.at(-1), failedMessage)
  assert.equal(retried.message_id, failedMessage.message_id)

  // Controlled HTTP fixtures exercise partial and timeout presentation.
  await page.route('**/api/chat', route => {
    const request = route.request().postDataJSON()
    return route.fulfill({ json: {
      status: 'partial', conversation_id: request.conversation_id, message_id: request.message_id,
      query_id: crypto.randomUUID(), rows: [], row_count: 1, rows_truncated: true,
      clarification_choices: [], replayed: false, answer: '测试用例中的部分响应。',
    } })
  }, { times: 1 })
  await submit('测试部分完成提示')
  await page.getByText('部分完成', { exact: true }).waitFor()
  await page.getByText('结果已截断，表格显示 API 返回的部分数据。').waitFor()
  await page.route('**/api/chat', route => route.fulfill({
    status: 504, json: { error: { message: '后端请求超时，可重试原消息。' } },
  }), { times: 1 })
  await submit('测试超时提示')
  await page.getByText('后端请求超时，可重试原消息。').waitFor()
  assert.deepEqual(browserErrors, [])
  assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0)
  console.log(JSON.stringify({
    browser: 'Chrome', first: { status: first.status, value: first.rows[0].value, query_id: first.query_id },
    followup: { status: second.status, value: second.rows[0].value, query_id: second.query_id },
    successful_clarification: clarify.status, retry_same_message_id: true, browser_authorization_sent: false,
    simulated: ['interrupted_network', 'partial', 'gateway_timeout'],
  }, null, 2))
} finally {
  await browser.close()
}
