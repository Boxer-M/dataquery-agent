# SQL 与 PDF 联合分析 v1

例如“2025年1月订单量是多少，并引用PDF扫描页的 Order count 原文”。完成后，数据库结果展示当月订单量，PDF 证据单独展示原文及来源。统计数值只来自受控指标服务。文档中的数字仅用于展示证据。汇总采用确定性模板，不从业务规则推断实际经营原因。

识别范围为前半句请求一个正式指标及一个窗口，后半句明确引用、结合或参考 PDF 主题。现有 Business Planner 让 Gemini 提案同时包含指标字段和原文 PDF 短语。Validator 核对指标、时间、维度、筛选和原文短语，再构建三个 DAG 节点。缺少指标、时间或 PDF 主题时，按提示在完整问题中补充信息。正式指标定义可通过独立的 Catalog 路径查询。

- **指标**。预检通过后，Data Analysis Agent 还需重新校验 Capability。SQL 工具事件绑定实际执行复核的 Trace ID。
- **PDF**。Knowledge Agent 按当前 Owner 的权限检索已审核、已发布的活跃文档。现有 RAG 校验模型选择的原文，联合任务在接收该回答后再查询当前版本、Hash 和权限。校验失效的证据从回答与引用中移除。
- **汇总**。依赖两个任务成功。任一任务失败时汇总标为 `skipped`，API 返回仍可验证的结果和失败原因。两项均失败时返回 `failed`。Query History 保存子任务、Attempt、工具、来源和引用。Catalog 恢复仍使用自己的恢复凭证。

## 复现

启动 Vite 本地工作台。浏览器评测运行器会使用回环端口 8000，先结束占用该端口的本地 API 实例。评测自动审核发布授权合成样例，并在完成后撤下。数据对账使用本地独立 ODS 查询。

```sh
PYTHONPATH=src .venv/bin/python tests/evaluate_integrated_e2e.py
WORKSPACE_SQL_DOCUMENT_E2E=1 PYTHONPATH=src .venv/bin/python tests/evaluate_integrated_browser.py
PYTHONPATH=src:tests DOCUMENT_RAG_POSTGRES_TESTS=1 .venv/bin/python -m unittest test_pdf_knowledge -v
```

## 真实 Gemini 与 PostgreSQL

原有 23 条综合场景全部复跑，从 22/23 提升为 23/23。此前返回 `sql_pdf_combination_unsupported` 的唯一失败场景现可完成联合查询。SQL 与独立 ODS 对账为 10/10，Catalog 引用 4/4，PDF 引用 3/3，History 与实时 Trace 23/23 一致。

新场景实际进行一次计划生成、一次答案摘录和一次查询 Embedding。Gemini 提议正式指标 `order_count`、原文时间 `2025年1月` 和原文 PDF 短语。事实结果 **2312** 与 ODS 一致。PDF 第 **2** 页审核文本第 **1–3** 行包含 `Order count: 48`，原件 SHA-256 与本地样例一致。三个 DAG 节点均成功。报告保留 Plan ID、Task ID、Attempt ID、Capability ID、原件 Hash 以及脱敏的模型输出。

整轮评测记录 14 次 Gemini 生成调用、7258 Token、7 次查询 Embedding。P50 为 2.34 秒，P95 为 4.93 秒。Embedding 响应未返回 Token 数。

[23 条真实复跑报告](../../reports/warehouse/sql_document_hybrid_v1.json)

## 部分成功与浏览器

Chrome 通过本地 FastAPI 完成联合查询，页面分别显示数据库结果和 PDF 证据。撤下同一个 PDF 后再次提交，真实查询仍返回 2312，PDF 节点返回 `pdf_evidence_insufficient`，汇总节点记录 `skipped / dependency_failed`，页面标为“部分完成”。

| 场景 | 指标 | PDF | 汇总 |
| --- | --- | --- | --- |
| 当前 Owner 拥有有效 PDF | 成功，2312 | 成功，第 2 页原文 | 完成 |
| PDF 撤下或另一个 Owner | 成功，2312 | 不可用 | 跳过 |
| 注入 SQL 超时，PDF 仍可用 | 无数值 | 保留第 2 页原文 | 跳过 |
| 注入 SQL 超时且 PDF 不可用 | 无数值 | 不可用 | 跳过，整体失败 |
| 联合返回前二次校验失效 | 成功，2312 | 撤销引文及摘录 | 跳过 |

浏览器完整、撤下与部分成功使用真实 Gemini 和 PostgreSQL。SQL 超时及返回前校验失效使用固定模型提案和故障注入。独立的真实 PostgreSQL 回归验证了旧版本切换、未审核内容、权限隔离和伪造引用。上表的故障注入不计入模型评测成绩。

[真实 Chrome 与部分成功 Trace](../../reports/warehouse/sql_document_hybrid_browser_v1.json)

Python 全量回归在启用 PostgreSQL 和 OCR 后通过 194 项，跳过 1 项可选 Gemini 测试。真实 Gemini 的结果见独立报告。前端 4 项测试、TypeScript 检查和生产构建均通过。

本次变更跨 Planner、DAG、RAG 和前端同一条调用链。专项用例和两份实测 Trace 随该里程碑提交。
