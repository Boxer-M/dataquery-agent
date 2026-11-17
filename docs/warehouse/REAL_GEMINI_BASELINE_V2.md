# Real Gemini Baseline v2

## 运行状态

产品代码基线为 `48fd946`，包含按序合并的 PR #43、#44、#45。结果保存于[结构化基线报告](../../reports/benchmark/real_gemini_baseline_v2.json)。本次真实运行受到 Gemini 地区限制，模型准确率与真实模型延迟仍为 N/A。

正式运行发出 1 次 `gemini-embedding-001` 索引请求及 1 次 `gemini-3.5-flash-lite` 生成请求。两次均返回 HTTP 400，服务端指出当前地区不受支持。无 503、429 或重试。正式运行前另有 1 次 Embedding 探测，收到相同地区错误。报告的正式调用数不含这一探测，合计实际尝试 3 次。未收到可用输出、Token 或账单数据。

## 样本及适用性

- 纳入计划的 58 条包括 23 条有既有独立执行和判分路径的历史用例，以及 35 条公开审核用例。逐条保留 ID、语义族、模板、Split 与任务类型。
- 20 条新增待审、7 条历史待审和封存 Holdout 不参与评分。另有 71 条旧记录未接入本轮独立的 live 判分映射，包含重复 fixture、固定提案及故障注入等场景。覆盖清单记录逐条原因，旧记录的完整 live 迁移仍是待办。
- 19 条依赖真实 Embedding 的用例因索引准备失败跳过，其中包含 8 条新获授权技术文档用例。另 39 条进入 Agent，23 条遇到生成服务阻断，2 条因依赖此前失败的会话而计为 N/A。14 条无需成功外部调用的本地路径具备评分条件。
- 按用户本轮授权，技术文档只允许 `TABLE_DESIGN.md` 的第 3–6、23–28、71–72、88–89 行。代码按源 Hash 与标签逐段校验，以隔离 Owner 发布临时夹具，再将真实检索 Chunk 映射回相关证据。四段在本次地区受阻运行中没有发送。正式目录及原有授权合成退款和 PDF 夹具继续使用各自的来源约束。未读取 Holdout 明文。

## 本地路径观测

| 指标 | 分子 / 分母 | N/A（14 条中） |
| --- | ---: | ---: |
| Intent Accuracy | 9 / 10 | 4 |
| Entity Precision / Recall / F1 | 8 / 8 · 8 / 11 · 16 / 19 | 2 |
| Tool Route Accuracy | 7 / 10 | 4 |
| SQL Result Accuracy | 1 / 2 | 12 |
| 已执行 SQL 的 ODS 一致率 | 1 / 1 | 13 |
| Citation Validity | 5 / 7 | 7 |
| 拒绝或澄清保护 | 5 / 5 | 9 |
| Task Success | 10 / 14 | 0 |
| Plan Validity、Recall@6、MRR@6、引用语义支持 | N/A | 14 |

本地可评分路径的端到端 P50/P95 为 17/42 ms。报告另外列出全部进入 Agent 的调用的按类延迟、模型及工具尝试、阶段耗时和脱敏 Trace。可复核的引用打分核对来源、版本与原文，语义支持保留人工审核。全部真实生成成功样本数为 0，故真实生成准确率、延迟和 Token 均无分母。固定模型提案和历史 Replay 执行数均为 0。

另以审核请求参数直接执行 ODS 及只读指标服务，对 **13 条、18 次** 公共结构化 SQL 真值完成独立复核。独立对账与端到端数字评分分别呈现。本轮实际发现的确定性缺口包括两条正式定义问法偏入 RAG、一条退货异常统计请求偏入 Catalog，以及一条支持范围澄清未提供预期指标实体。对应的 Trace 保存在报告中，后续实验应优先验证定义/数值路由，避免把该问题误归因为检索排序。服务恢复后再测 Context Engineering 和检索消融。

## 请求预算与费用

Runner 对每次出站调用先留出预算，累积上限为 110 次生成、30 次 Embedding、1,050 万保留 Token、20 美元估算上限。生成保留完整模型最大输出窗口并计入思考用量。输入使用保守预留及消息字节限制。账单金额仍以提供商记录为准。价格依据为 Google Gemini API 标准付费价，地区错误无可计价用量，因此费用为 N/A。[Google 官方定价](https://ai.google.dev/gemini-api/docs/pricing)

## 复现

```bash
PYTHONPATH=src:tests .venv/bin/python -m benchmark.live_baseline \
  --allow-live-model --allow-four-reviewed-technical-spans --out /tmp/gemini-baseline-new.json
PYTHONPATH=src:tests .venv/bin/python -m benchmark.live_baseline \
  --verify-sql --out /tmp/gemini-baseline-new.json
PYTHONPATH=src:tests .venv/bin/python -m unittest discover -s tests -p 'test_real_gemini_baseline.py'
```

在 Gemini 提供商允许的执行环境重跑时，保留本次报告为地区受阻记录。初次运行生成、Embedding 和 PostgreSQL 明细使用现有适配器，离线对账入口只访问本机 PostgreSQL。快照、样本和来源摘要收录于报告。

Python 全量运行 217 项，216 项通过，1 项真实 Gemini 可选测试跳过。前端 5 项测试及 TypeScript 类型检查和生产构建通过。
