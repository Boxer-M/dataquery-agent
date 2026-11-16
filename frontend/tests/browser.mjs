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
  let chartCoreUrl
  const browserErrors = []
  page.on('pageerror', (error) => browserErrors.push(error.message))
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname
    if (['/src/query-result.tsx', '/src/query-history.tsx', '/src/order-chart.tsx'].includes(path)) featureModules.add(path)
    if (path.includes('/echarts_core.js')) chartCoreUrl = request.url()
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
  assert.ok(!featureModules.has('/src/order-chart.tsx'))
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
  async function verifyChart(message, data, kind, screenshot) {
    const plot = message.getByRole('img', { name: /订单量.*图/ })
    await plot.locator('canvas').waitFor()
    assert.ok(chartCoreUrl)
    const rendered = await plot.evaluate(async (element, url) => {
      const { getInstanceByDom } = await import(url)
      const option = getInstanceByDom(element).getOption()
      const series = option.series[0]
      return {
        kind: series.type,
        labels: option.xAxis[0].data,
        values: series.data,
        tooltip: option.tooltip[0].formatter({ dataIndex: 0 }),
      }
    }, chartCoreUrl)
    assert.equal(rendered.kind, kind)
    const contract = data.visualization
    const expectedPoints = contract.row_order.map(i => ({
      label: data.rows[i][contract.dimension.field],
      raw: contract.value_path.reduce((value, field) => value[field], data.rows[i]),
      index: i,
    }))
    assert.deepEqual(rendered.labels, expectedPoints.map(point => point.label))
    assert.deepEqual(rendered.values.map(point => [point.name, point.raw, point.rowIndex]),
      expectedPoints.map(point => [point.label, point.raw, point.index]))
    assert.deepEqual(rendered.values.map(point => String(point.value)), expectedPoints.map(point => point.raw))
    assert.ok(rendered.tooltip.includes(contract.metric.name) && rendered.tooltip.includes(expectedPoints[0].raw))
    if (screenshot) {
      await message.locator('.agent-message').evaluate(node => node.scrollIntoView({ block: 'start' }))
      await page.screenshot({ path: screenshot, animations: 'disabled' })
    }
    return expectedPoints.map(({ label, raw }) => [label, raw])
  }

  const groupedMessage = page.locator('.message').last()
  assert.deepEqual(await verifyChart(groupedMessage, grouped, 'bar', process.env.WORKSPACE_CHANNEL_SCREENSHOT), expected)
  const beforeToggle = submitted.length
  await groupedMessage.getByRole('button', { name: '表格', exact: true }).click()
  const table = groupedMessage.getByRole('table')
  for (const [bucket, value] of expected) {
    const row = table.getByRole('row').filter({ has: page.getByRole('cell', { name: bucket, exact: true }) })
    await row.getByRole('cell', { name: value, exact: true }).waitFor()
  }
  await groupedMessage.getByRole('button', { name: '图表', exact: true }).click()
  await groupedMessage.locator('canvas').waitFor()
  assert.equal(submitted.length, beforeToggle)

  const annualResponse = page.waitForResponse(response => response.url() === origin + '/api/chat', { timeout: 100_000 })
  await submit('2025年订单量是多少？')
  assert.equal((await (await annualResponse).json()).status, 'completed')
  const monthlyResponse = page.waitForResponse(response => response.url() === origin + '/api/chat', { timeout: 100_000 })
  await submit('按月拆分')
  const monthly = await (await monthlyResponse).json()
  assert.equal(monthly.status, 'completed')
  assert.equal(monthly.rows.length, 12)
  const monthlyPoints = await verifyChart(page.locator('.message').last(), monthly, 'line', process.env.WORKSPACE_MONTH_SCREENSHOT)

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

  // Let the server commit before dropping the response to exercise real idempotency.
  let committed
  await page.route('**/api/chat', async route => {
    committed = await (await route.fetch()).json()
    await route.abort('failed')
  }, { times: 1 })
  await submit('2025年1月折扣后商品销售金额是多少？')
  const retry = page.getByRole('button', { name: '重试原消息' })
  await retry.waitFor()
  const failedMessage = submitted.at(-1)
  const retriedResponse = page.waitForResponse(response => response.url() === origin + '/api/chat')
  await retry.click()
  const retried = await (await retriedResponse).json()
  assert.deepEqual(submitted.at(-1), failedMessage)
  assert.equal(retried.message_id, failedMessage.message_id)
  assert.equal(committed.status, 'completed')
  assert.equal(retried.replayed, true)
  assert.equal(retried.query_id, committed.query_id)

  let pdfEvidence
  if (process.env.WORKSPACE_PDF_E2E) {
    const pdfResponse = page.waitForResponse(response => response.url() === origin + '/api/chat', { timeout: 100_000 })
    await submit('PDF 中带有 Scanned verification page 标题的页面，请逐字摘录该页的 Order count 那一整行。')
    const pdf = await (await pdfResponse).json()
    assert.equal(pdf.evidence_status, 'supported')
    const citation = pdf.citations.find(item => item.source_type === 'reviewed_pdf')
    assert.equal(citation.page_number, 2)
    assert.equal(citation.quote, 'Order count: 48')
    const pdfMessage = page.locator('.message').last()
    const details = pdfMessage.locator('.result-details').filter({ hasText: citation.title })
    await details.locator('summary').click()
    await details.getByText(citation.source_hash, { exact: true }).waitFor()
    await details.getByText('1–3', { exact: true }).waitFor()
    const before = submitted.length
    const pdfHistory = page.waitForResponse(response => response.url() === `${origin}/api/queries/${pdf.query_id}`)
    await pdfMessage.getByRole('button', { name: '查看查询历史' }).click()
    const saved = await (await pdfHistory).json()
    assert.deepEqual(saved.citations, pdf.citations)
    assert.deepEqual(saved.trace, pdf.trace)
    assert.equal(submitted.length, before)
    await page.keyboard.press('Escape')
    await drawer.waitFor({ state: 'hidden' })
    if (process.env.WORKSPACE_PDF_SCREENSHOT) {
      await pdfMessage.locator('.agent-message').evaluate(node => node.scrollIntoView({ block: 'start' }))
      await page.screenshot({ path: process.env.WORKSPACE_PDF_SCREENSHOT, animations: 'disabled' })
    }
    pdfEvidence = { query_id: pdf.query_id, citation, history_matches: true }
  }

  let sqlDocument
  if (process.env.WORKSPACE_SQL_DOCUMENT_E2E) {
    const question = '2025年1月订单量是多少，并引用PDF扫描页的 Order count 原文。'
    async function askJoint() {
      const response = page.waitForResponse(item => item.url() === origin + '/api/chat', { timeout: 100_000 })
      await submit(question)
      return (await response).json()
    }
    const completed = await askJoint()
    assert.equal(completed.status, 'completed', completed.error?.code)
    assert.equal(completed.rows[0].metrics.order_count.value, '2312')
    assert.equal(completed.citations[0].page_number, 2)
    assert.equal(completed.citations[0].quote, 'Order count: 48')
    assert.deepEqual(completed.trace.tasks.map(task => task.kind), ['metric', 'pdf', 'summary'])
    const completeMessage = page.locator('.message').last()
    await completeMessage.locator('.answer-text').filter({ hasText: /数据库结果.*PDF 证据/s }).waitFor()
    await completeMessage.getByText('查看引用 · Synthetic PDF verification').waitFor()

    const partial = await askJoint()
    assert.equal(partial.status, 'partial', partial.error?.code)
    assert.equal(partial.rows[0].metrics.order_count.value, '2312')
    assert.equal(partial.citations.length, 0)
    const partialMessage = page.locator('.message').last()
    await partialMessage.getByText('部分完成', { exact: true }).waitFor()
    await partialMessage.getByText(/PDF 证据不可用/).waitFor()
    await partialMessage.getByText(/联合汇总未完成/).waitFor()
    sqlDocument = {
      completed: { query_id: completed.query_id, rows: completed.rows, citations: completed.citations, trace: completed.trace },
      partial: { query_id: partial.query_id, rows: partial.rows, error: partial.error, trace: partial.trace },
    }
  }

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
    grouped: { query_id: grouped.query_id, rows: expected, visualization: 'bar' },
    monthly: { query_id: monthly.query_id, rows: monthlyPoints, visualization: 'line' },
    history: { same_trace: true, tasks: history.trace.tasks.length, capability: history.trace.capability.length, tools: history.trace.tools.length, additional_chat_requests: 0 },
    clarification: { status: clarify.status, selection: choice.metric_id, followup_status: selection.status, query_id: selection.query_id },
    retry_same_message_id: true, idempotent_replay_query_id: retried.query_id,
    pdf: pdfEvidence, sql_document: sqlDocument, browser_authorization_sent: false,
    simulated: ['interrupted_network', 'partial_truncated_precision_html', 'history_skipped_recovery_missing', 'failed', 'gateway_timeout'],
  }, null, 2))
} finally {
  await browser.close()
}
