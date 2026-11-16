# 综合端到端验收 v1

## 调用链与复现

综合评测复用 13 条 Golden 用例和独立 ODS 校验器，并增加 10 条跨模块用例。测试输入包括自然语言问题、会话前置状态和 Owner 专属的合成文档。输出逐项记录业务状态、模型尝试、工具与 Capability 关联、引用、独立对账和读回的历史一致性。浏览器运行器在回环地址启动实际 FastAPI，使用本地 Vite 工作台、Chrome 和真实数据库。建议依次执行数据库评测与回归。Catalog 恢复使用数据库快照判定结果可复用性，并行测试写入会使快照变化。

```sh
PYTHONPATH=src .venv/bin/python tests/evaluate_integrated_e2e.py
# 停止已有的本地 API 实例，保持 Vite 工作台运行
PYTHONPATH=src WORKSPACE_PDF_SCREENSHOT=/tmp/integrated-pdf.png \
  .venv/bin/python tests/evaluate_integrated_browser.py
.venv/bin/python -c 'import json; print(json.dumps(json.load(open("reports/warehouse/agent_e2e_evaluation_v1.json"))["specialists"]))' \
  > /tmp/integrated-historical-specialists.json
PYTHONPATH=src .venv/bin/python tests/evaluate_agent_e2e.py \
  --rescore /tmp/integrated-historical-specialists.json
```

模型请求使用已有授权的 Catalog 上下文及项目内合成文本和 PDF 片段。ODS 明细与对账结果留在本地。用于评测的文档通过现有发布与撤下流程，采用独立 Owner。报告中的模型输出已脱敏，保留查询、任务、Attempt 和工具关联标识。

## 真实 Gemini 与 PostgreSQL

| 检查 | 结果 |
| --- | --- |
| 完整链路 | 22/23。原有 Golden 13/13，新增 9/10 |
| 涉及生成模型的场景 | 12/12，通过 13 次实际调用 |
| 适用样本的模型意图、实体提案 | 各 6/6；合法经营计划 2/2 |
| SQL 独立 ODS 对账与来源门控 | 各 9/9 |
| 图表契约 | 2/2，月度 12 行、渠道 4 行 |
| 正式 Catalog、PDF 引用 | 分别 4/4、2/2；PDF 原生文本页和 OCR 页均与当前审核 Chunk 核对 |
| 权限与拒绝检查 | 8/8；包括跨 Owner、撤下 PDF 和当前不支持的联合请求 |
| 当前响应与持久化 History | 23/23 |
| 生成模型 Token | 输入 4915，输出 931，合计 5846 |
| Embedding | 查询 6 次；测试文档索引另计 2 批。API 未返回 Embedding Token |
| 延迟 P50 / P95 | 全部场景 2.192 / 6.669 秒；调用生成模型的场景 3.315 / 7.119 秒 |

SQL 与 PDF 联合分析是唯一未完成场景。首轮曾只完成 SQL，遗漏用户要求的 PDF 证据。现在对于已识别的明确组合，系统在执行前返回 `sql_pdf_combination_unsupported`，并提示拆分问题。普通 PDF、Catalog 精确查询以及 SQL 与正式 Catalog 联合查询仍由各自的现有路径完成。

首轮四个 SQL 场景的 History 校验失败。单指标工具 Trace 已包含结构化请求与 Capability Trace ID，历史写入却要求同时存在 DAG Task ID。写入现已按实际字段保存这些关联，读回的公开 Trace 与实时响应一致。前端证据面板展示真实 PDF 页码、审核文本行号、提取方式与文件 Hash。

[逐场景模型输出、ODS 对账、引用与 Trace](../../reports/warehouse/integrated_e2e_v1.json)

## FastAPI 与 Chrome 联调

实际 Chrome 使用本地同源代理访问 FastAPI。1 月订单量为 2312，追问 2 月得到 2049。联合查询显示 2312、5538 和两条正式引用。销售额歧义由用户明确选择口径后继续。月度和渠道图表使用 12 和 4 个真实数据点，切换图表与表格无需发起查询。历史详情保留 4 个任务、4 条 Capability 记录和 5 个工具事件，打开详情期间没有额外聊天请求。

已审核 PDF 的 OCR 引用显示第 2 页、审核文本第 1–3 行和原文 `Order count: 48`，实时与历史接口证据一致。浏览器测试在服务端提交后中断响应，重试沿用同一 Message ID 并取得同一 Query ID，回执标明重放。浏览器请求未带 Bearer。综合浏览器运行记录 11 次聊天请求、6 次生成调用和 2397 个生成模型 Token，另有 1 次 Embedding 索引和 1 次查询。PDF 测试文档已撤下。

浏览器的部分成功、依赖跳过及网关超时界面还通过了受控 HTTP 故障模拟。原有 FastAPI 和 PDF PostgreSQL 测试分别覆盖注入 SQL 超时、模拟 Catalog 索引缺失、真实撤下、替换当前版本和伪造引用的校验。曾用无日期问题测试浏览器重放，实际服务要求澄清；最终验收用明确时间窗让重放覆盖已成功提交的指标查询。

[Chrome 请求、模型尝试、图表数据及 PDF 引用](../../reports/warehouse/integrated_e2e_browser_v1.json)

## 回归与历史复评

启用 PostgreSQL 与本地 OCR 的 Python 全量回归通过 194 项，跳过 1 项单独启用的 Gemini 测试。前端 4 项测试、TypeScript 检查与生产构建通过，构建仍提示已有 ECharts 按需包超过 500 kB。只读角色的 ACL 回归验证了无法访问 ODS、无法写入 DWS；专业 Agent、DAG 和 Catalog 定向恢复回归通过。并行运行期间，另一个评测进程写入导致 Catalog 恢复的快照校验拒绝保存恢复凭证。顺序运行后该测试通过，遇到活跃数据库写入时仍可能无法保留恢复凭证。

历史专业 Agent 报告复评得到 12/13，复现旧记录的 `sql_capability_trace_unlinked`。复评执行了本地 ODS 对账，产生 0 次外部调用，历史原始报告保持原样。以上固定提案、故障注入和历史复评均未计入真实模型准确率。

## 能力清单与下一步

已经过综合验收的主要路径包括多轮单指标、双指标及正式定义、受控分组和图表、普通知识 RAG、审核后 PDF 引用、历史 Trace 与消息重放。下一步可给现有 Planner 增加一个明确的 SQL 与授权文档双任务契约，先用一条真实业务场景验证来源分别可追溯、任一任务失败时正确返回 partial。验收样本数量较少，结果仅代表本次模型和用例。
