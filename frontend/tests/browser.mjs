import assert from 'node:assert/strict'
import { chromium } from 'playwright-core'

const origin = process.env.WORKSPACE_ORIGIN ?? 'http://127.0.0.1:5173'
const browser = await chromium.launch({
  executablePath: process.env.CHROME_BIN ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
})
try {
  const page = await browser.newPage({ viewport: { width: 1360, height: 850 } })
  const submitted = []
  const featureModules = new Set()
  const browserErrors = []
  page.on('pageerror', (error) => browserErrors.push(error.message))
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname
    if (path === '/src/query-result.tsx' || path === '/src/query-history.tsx') featureModules.add(path)
    if (request.url() === origin + '/api/chat') {
      submitted.push(request.postDataJSON())
      assert.equal(request.headers().authorization, undefined)
    }
  })
  await page.goto(origin)
  await page.getByText('API 已连接', { exact: true }).waitFor()
  assert.equal(featureModules.size, 0)
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
  await page.locator('.metric-card .metric-value').filter({ hasText: /^2312$/ }).waitFor()
  assert.ok(featureModules.has('/src/query-result.tsx'))
  assert.ok(!featureModules.has('/src/query-history.tsx'))
  if (process.env.WORKSPACE_SCREENSHOT) await page.screenshot({ path: process.env.WORKSPACE_SCREENSHOT })

  const nextResponse = page.waitForResponse(response => response.url() === origin + '/api/chat', { timeout: 100_000 })
  await submit('那2月份呢？')
  const second = await (await nextResponse).json()
  assert.equal(second.status, 'completed')
  assert.equal(second.rows[0].value, '2049')
  await page.locator('.metric-card .metric-value').filter({ hasText: /^2049$/ }).waitFor()
  assert.equal(first.conversation_id, conversation_id)
  assert.equal(second.conversation_id, conversation_id)
  assert.notEqual(first.message_id, second.message_id)

  const groupedResponse = page.waitForResponse(response => response.url() === origin + '/api/chat', { timeout: 100_000 })
  await submit('查询2025年1月按渠道统计订单量')
  const grouped = await (await groupedResponse).json()
  assert.equal(grouped.status, 'completed')
  assert.equal(grouped.rows_truncated, false)
  const expected = [['mobile', '685'], ['partner', '207'], ['phone', '83'], ['web', '1337']]
  assert.deepEqual(grouped.rows.map(row => [row.bucket, row.value]), expected)
  const table = page.locator('.message').last().getByRole('table')
  await table.waitFor()
  for (const [bucket, value] of expected) {
    const row = table.getByRole('row').filter({ has: page.getByRole('cell', { name: bucket, exact: true }) })
    await row.getByRole('cell', { name: value, exact: true }).waitFor()
  }
  if (process.env.WORKSPACE_GROUPED_SCREENSHOT) {
    await page.screenshot({ path: process.env.WORKSPACE_GROUPED_SCREENSHOT, animations: 'disabled' })
  }

  const hybridResponse = page.waitForResponse(response => response.url() === origin + '/api/chat', { timeout: 100_000 })
  await submit('查询2025年1月订单量和订单商品明细数，并给出正式定义')
  const hybrid = await (await hybridResponse).json()
  assert.equal(hybrid.status, 'completed')
  assert.deepEqual(Object.fromEntries(Object.entries(hybrid.rows[0].metrics).map(([id, cell]) => [id, cell.value])),
    { order_count: '2312', order_item_count: '5538' })
  const joint = page.locator('.message').last()
  await joint.getByRole('cell', { name: '5538', exact: true }).waitFor()
  assert.equal(hybrid.citations.length, 2)
  for (const citation of hybrid.citations) {
    const card = joint.locator('.definition').filter({ has: page.getByRole('heading', { name: citation.title, exact: true }) })
    await card.getByText(citation.quote, { exact: true }).first().waitFor()
    await card.getByText(`查看引用 · ${citation.title}`, { exact: true }).click()
    assert.ok((await card.innerText()).includes(citation.document_version))
    assert.ok((await card.innerText()).includes(citation.source_uri))
    assert.ok((await card.innerText()).includes(citation.chunk_id))
  }
  if (process.env.WORKSPACE_HYBRID_SCREENSHOT) {
    await page.setViewportSize({ width: 1360, height: 1440 })
    await joint.evaluate(node => node.scrollIntoView({ block: 'start' }))
    await page.screenshot({ path: process.env.WORKSPACE_HYBRID_SCREENSHOT })
    await page.setViewportSize({ width: 1360, height: 850 })
  }

  const historyPath = `${origin}/api/queries/${hybrid.query_id}`
  const callsBeforeHistory = submitted.length
  const historyResponse = page.waitForResponse(response => response.url() === historyPath && response.request().method() === 'GET')
  await joint.getByRole('button', { name: '查看查询历史' }).click()
  const receipt = await historyResponse
  assert.equal(receipt.request().headers().authorization, undefined)
  assert.equal(receipt.status(), 200)
  assert.ok(featureModules.has('/src/query-history.tsx'))
  const history = await receipt.json()
  for (const key of ['plan_id', 'attempt_id', 'tasks', 'capability', 'tools']) {
    assert.deepEqual(history.trace[key], hybrid.trace[key], key)
  }
  assert.deepEqual(history.citations, hybrid.citations)
  assert.deepEqual(history.trace.tasks.map(task => task.kind).sort(), ['catalog', 'catalog', 'metric', 'metric'])
  assert.deepEqual(history.trace.tasks.map(task => task.agent).sort(), ['data_analysis', 'data_analysis', 'knowledge', 'knowledge'])
  assert.deepEqual(history.trace.tasks.map(task => task.schedule_position), [1, 2, 3, 4])
  for (const event of history.trace.tools.filter(item => item.kind === 'metric')) {
    assert.ok(history.trace.capability.some(record => record.trace_id === event.capability_trace_id
      && record.task_id === event.task_id && record.attempt_id === event.attempt_id
      && record.capability_phase === 'execution_recheck'))
  }
  const drawer = page.getByRole('dialog', { name: '查询历史详情' })
  await drawer.getByText('当前回执与已保存 Trace 的可对照标识一致。').waitFor()
  await drawer.getByRole('button', { name: /SQL 指标 · order_count/ }).first().click()
  await drawer.getByText('Data Analysis Agent', { exact: true }).waitFor()
  if (process.env.WORKSPACE_HISTORY_SCREENSHOT) {
    await drawer.getByRole('button', { name: /SQL 指标 · order_count/ }).first().evaluate(
      node => node.scrollIntoView({ block: 'start' }),
    )
    await page.screenshot({ path: process.env.WORKSPACE_HISTORY_SCREENSHOT, animations: 'disabled' })
  }
  assert.equal(submitted.length, callsBeforeHistory)
  await page.keyboard.press('Escape')
  await drawer.waitFor({ state: 'hidden' })

  const recoveryId = '11111111-1111-4111-8111-111111111111'
  await page.route(historyPath, route => route.fulfill({ json: {
    query_id: hybrid.query_id, status: 'partial', trace: {
      attempt_id: 'second_attempt', previous_query_id: recoveryId, tasks: [{
        task_id: 'dependent_task', attempt_id: 'first_attempt', kind: 'catalog',
        status: 'skipped', code: 'dependency_failed', failed_dependencies: ['failed_metric'],
      }],
    },
  } }), { times: 1 })
  await page.route(`${origin}/api/queries/${recoveryId}`, route => route.fulfill({ status: 404 }), { times: 1 })
  await joint.getByRole('button', { name: '查看查询历史' }).click()
  await drawer.getByText('second_attempt、first_attempt').waitFor()
  await drawer.getByRole('button', { name: /Catalog.*skipped/ }).click()
  await drawer.getByText('dependency_failed', { exact: true }).waitFor()
  await drawer.getByText('failed_metric', { exact: true }).waitFor()
  await drawer.getByRole('button', { name: '查看上一次执行记录' }).click()
  await drawer.getByText('记录不存在或当前身份无权查看。').waitFor()
  assert.equal(submitted.length, callsBeforeHistory)
  await page.keyboard.press('Escape')
  await drawer.waitFor({ state: 'hidden' })

  const clarifyResponse = page.waitForResponse(response => response.url() === origin + '/api/chat')
  await submit('2025年1月销售额')
  const clarify = await (await clarifyResponse).json()
  assert.equal(clarify.status, 'clarification_required')
  await page.getByText('需要澄清', { exact: true }).waitFor()

  const choice = clarify.clarification_choices.find(item => item.metric_id === 'item_net_sales_amount')
  assert.ok(choice)
  const choiceIndex = await page.locator('.message').count() - 1
  const choiceMessage = page.locator('.message').nth(choiceIndex)
  if (process.env.WORKSPACE_CLARIFY_SCREENSHOT) {
    await page.screenshot({ path: process.env.WORKSPACE_CLARIFY_SCREENSHOT })
  }
  const selectionResponse = page.waitForResponse(response => response.url() === origin + '/api/chat')
  await choiceMessage.getByRole('button', { name: choice.name }).click()
  const selection = await (await selectionResponse).json()
  assert.equal(selection.status, 'completed')
  assert.equal(selection.conversation_id, conversation_id)
  assert.notEqual(selection.message_id, clarify.message_id)
  assert.equal(submitted.at(-1).question, choice.metric_id)
  assert.ok(await choiceMessage.getByRole('button', { name: choice.name }).isDisabled())

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
      query_id: crypto.randomUUID(), rows: [{ bucket: 'all', value: '9007199254740993.0100' }], row_count: 2, rows_truncated: true,
      definitions: [{ metric_id: 'test_precision', definition: '<img src=x onerror=alert(1)>' }], citations: [],
      trace: { tasks: [{ task_id: 'catalog:1', kind: 'catalog', metric_id: 'test_precision', status: 'unavailable' }] },
      clarification_choices: [], replayed: false, answer: '测试用例中的部分响应。',
    } })
  }, { times: 1 })
  await submit('测试部分完成提示')
  await page.getByText('部分完成', { exact: true }).waitFor()
  await page.getByText('结果已截断，仅显示 API 返回的部分数据。').waitFor()
  const partial = page.locator('.message').last()
  await partial.getByText('9007199254740993.0100', { exact: true }).waitFor()
  await partial.getByText('正式定义引用不可用', { exact: false }).waitFor()
  await partial.getByText('<img src=x onerror=alert(1)>', { exact: true }).waitFor()
  assert.equal(await partial.locator('img').count(), 0)
  await partial.getByText('查看原始结果').click()
  assert.ok((await partial.locator('pre').innerText()).includes('"9007199254740993.0100"'))
  await page.route('**/api/chat', route => {
    const request = route.request().postDataJSON()
    return route.fulfill({ json: { ...request, status: 'failed', error: { code: 'query_timeout', message: '请求未能完成。' },
      definitions: [], citations: [], clarification_choices: [], rows: [] } })
  }, { times: 1 })
  await submit('测试业务失败')
  await page.getByText('查询失败', { exact: true }).waitFor()
  await page.route('**/api/chat', route => route.fulfill({
    status: 504, json: { error: { message: '后端请求超时，可重试原消息。' } },
  }), { times: 1 })
  await submit('测试超时提示')
  await page.getByText('后端请求超时，可重试原消息。').waitFor()
  assert.deepEqual(browserErrors, [])
  assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0)
  console.log(JSON.stringify({
    browser: 'Chrome', origin, first: { status: first.status, value: first.rows[0].value, query_id: first.query_id },
    followup: { status: second.status, value: second.rows[0].value, query_id: second.query_id },
    hybrid: { query_id: hybrid.query_id, values: ['2312', '5538'], verified_citations: hybrid.citations.length },
    grouped: { query_id: grouped.query_id, rows: expected, visualization: 'table' },
    history: { same_trace: true, tasks: history.trace.tasks.length, capability: history.trace.capability.length, tools: history.trace.tools.length, additional_chat_requests: 0 },
    clarification: { status: clarify.status, selection: choice.metric_id, followup_status: selection.status, query_id: selection.query_id },
    retry_same_message_id: true, browser_authorization_sent: false,
    simulated: ['interrupted_network', 'partial_truncated_precision_html', 'history_skipped_recovery_missing', 'failed', 'gateway_timeout'],
  }, null, 2))
} finally {
  await browser.close()
}
