# DataQuery Agent 演示脚本

按[本地安装手册](LOCAL_RELEASE.md)启动工作台。SQL Shed 是公开合成测试数据。PDF 使用仓库内的授权合成样例，示例标题和修订仅用于演示。

## 1. 指标、澄清、追问及 ECharts

1. 输入「2025年1月订单量是多少？」，结果为 **2312**。
2. 在同一会话输入「2月呢？」，复用订单量口径，结果为 **2049**。
3. 输入「2025年1月销售额」触发口径澄清，选「折扣后商品销售金额」再查询。指标卡片的元数据可查看对应口径。
4. 输入「2025年订单量是多少？」后输入「按月拆分」，检查 12 个点。或者直接输入「查询2025年1月按渠道统计订单量」，核对 mobile **685**、partner **207**、phone **83**、web **1337**。
5. 切换图表和表格，对照相同查询 ID。打开「查看查询历史」查看 Capability 和工具事件。

来源为 `dws.order_day`。发布验收对时间和渠道聚合使用独立的 `ods.orders` 查询。[图表契约](CHART_DATA_CONTRACT_V1.md)

## 2. PDF 上传、OCR 修订和知识引用

1. 打开文档知识库，上传 [合成 PDF](../../output/pdf/document-extraction-sample.pdf)。第 1 页是原生文本，第 2 页采用 OCR，第 3 页为空白。
2. 选择第 2 页，核对原始提取的 `Order count: 48`。在人工修订栏保留原文并添加一行 `Reviewed UI appendix: confirmed by human review.`
3. 文档标题填 `Document UI synthetic PDF verification`，审核依据填「比对授权合成原件」，提交审核。勾选生成向量授权后发布。记录暂存 Document ID 和知识版本。
4. 点击「在聊天中询问此文档」，将草稿改为「请从『Document UI synthetic PDF verification』的 PDF 扫描页逐字摘录以 Reviewed UI appendix 开头的整行。」并发送。
5. 应看到第 **2** 页及整行原文。打开引用检查审核文本行号、版本和原件 SHA-256。发布验收使用该文件的真实提取内容与索引记录对照。
6. 刷新页面后，回到文档知识库，粘贴之前记录的暂存 Document ID 重新打开。通过发布后的回执可区分导入版本、审核版本及知识版本。

知识来源是审核后的当前 Owner 私有 PDF。报告里的修订句属于合成样本，展示了 OCR 批注如何成为带来源的检索证据。[文档 API](DOCUMENT_API_V1.md)

## 3. SQL 与 PDF 联合分析及 Trace

1. 保留演示 2 的已发布扫描样例，提交「2025年1月订单量是多少，并引用PDF扫描页的 Order count 原文。」
2. 核对数据库部分的 **2312** 与 PDF 第 **2** 页的 `Order count: 48`。前者来自 `dws.order_day` 并经 ODS 对账，后者来自审核后的 PDF。
3. 查看 Query History，确认指标、PDF 和汇总三个 DAG 节点。查看 SQL 的 Capability 关联和 PDF 引用的版本、来源 Hash。
4. 可选地执行下面的撤下操作后重发。新回答应保留 **2312**，将 PDF 标记为不可用，并在 Trace 中记录汇总跳过。既有 History 记录显示当次已执行的结果和证据。

撤下自己的测试文档时，使用发布回执 `publication.document_id`（知识文档 ID）而不是上传的 UUID。首次运行前确认正在操作对应的测试文档。撤下的原知识 ID 不可重新激活，需要重新导入和发布。

```sh
PYTHONPATH=src .venv/bin/python -m dataquery_agent.rag.cli delete '<知识文档 ID>'
```

## 自动复现

下面是本次使用的浏览器与 23 场景评测。保持 Vite 在 5173 运行，先停止本机现有的 8000 服务。浏览器评测会在环回地址启动独立 API，使用临时暂存目录，并撤下提交的合成文档。第二个命令可独立运行，使用专用 ODS 审计账号。

```sh
WORKSPACE_DOCUMENT_UI_E2E=1 WORKSPACE_SQL_DOCUMENT_E2E=1 \
WORKSPACE_DOCUMENT_SCREENSHOTS=/tmp/dataquery-release \
PYTHONPATH=src .venv/bin/python tests/evaluate_integrated_browser.py
PYTHONPATH=src .venv/bin/python tests/evaluate_integrated_e2e.py
```
