# Intent 与 Tool Routing v2 配对验证

按依赖顺序核对了远端 PR #46、#47，从 `00ae252` 创建分支。原真实基线由 `d8d5b42` 收录，运行时提交、三份评分器的 Git blob、Golden 版本与哈希、ODS 指纹、模型及 Prompt 配置都记录在[逐题配对](../../reports/benchmark/intent_tool_routing_v2_pairs.json)中。真实运行使用同一份 58 条 Development / Validation 清单、`gemini-3.5-flash-lite`、`gemini-embedding-001` 和已授权的相同索引材料。Holdout 明文未读取。

| 方案 | 实现与费用 | 误路由边界 |
| --- | --- | --- |
| A：修订确定性入口 | 明确定义提示、计量动作和扫描页提示，补齐窄范围观察窗口澄清。分类本身增加 0 次模型调用 | 依赖已知句式。完整模型提案仍经既有验证器 |
| B：规则不确定时增加结构化分类 | 需要额外模型请求、Token 及超时处理，并验证分类与实体证据的一致性 | 可覆盖较隐晦的表达，分类也可能误判，需要既有 Capability 与授权校验兜底 |

选择 A。定义进入 Catalog 精确匹配，带统计窗的分布请求返回数值路径，扫描页请求沿用 PDF 授权检索，观察窗口超出支持范围时保留指标实体并澄清。现有复合请求处理、Owner、Capability 和安全校验继续负责工具授权。定向测试还检查否定定义、带范围的数值请求和原件坐标描述。真实候选运行在定向测试收紧否定复合尾句和澄清入口位置之前启动；六条受影响的真实用例随后均以最终代码做固定输入对照。否定尾句新增覆盖由定向回归验证。

## 配对结果

| 指标 | 基线 | 候选 | N/A |
| --- | ---: | ---: | ---: |
| Intent | 42/45 | 44/45 | 13 |
| 实体 Precision / Recall / F1 | 37/37 · 37/40 · 74/77 | 40/40 · 40/40 · 80/80 | 22 |
| Tool Routing | 33/43 | 38/43 | 15 |
| Plan | 4/8 | 4/8 | 50 |
| SQL | 17/23 | 18/23 | 35 |
| 已执行 SQL 与 ODS 一致 | 17/17 | 18/18 | 41 → 40 |
| E2E | 46/58 | 51/58 | 0 |

工具路由 6 条失败转成功、1 条成功转失败，端到端 8 条失败转成功、3 条成功转失败。六条固定输入确认了两条定义、一个退货数值、一个观察窗和两条 PDF 的预期边界变化。端到端变化还受模型提案、服务与回答波动影响：技术文档旧版在 503 后失败，本轮成功，该变化不归因于路由。候选版退货数值请求也经历 503 后重试成功，独立固定输入证实其工具边界变化。另三条端到端转失败分别为复购指标分组拒绝、支付尝试按状态生成了业务计划而单指标 Intent 判分失败，以及 RAG 找到候选但未返回有效引用。

Validation 的失败题目及 Trace 在归因时被查看，其中三条参与固定路由对照，完整 ID 见逐题文件。因此这个 Validation 对照有已知暴露，不当作独立泛化验证。未引入 Holdout，也未把真实 API 波动算进固定路由结果。

| 运行观测 | 基线 | 候选 |
| --- | ---: | ---: |
| 生成 / Embedding 请求 | 46 / 22 | 47 / 22 |
| 有用量记录的输入 / 输出 Token | 28,644 / 3,770 | 29,393 / 3,831 |
| 端到端 P50 / P95 | 10.164 / 18.251 秒 | 3.346 / 13.664 秒 |
| 服务失败 | 503 两次，超时一次 | 503 三次 |

这是两次独立真实调用的观测，延迟差异没有单独控制服务负载。Ledger 和失败 Trace 保存在[本轮真实运行报告](../../reports/benchmark/intent_tool_routing_v2_candidate.json)。

## 评分保护与复现

基线与候选使用同一评分器版本和 Golden。已有空参数回归独立验证了省略空分组/空筛选等价，结果哈希错配与实质筛选变化不能触发补分；逐题保留捕获结果哈希及评分。候选实际执行的 18 项 SQL 结果均与 ODS 一致，另外 13 个结构化案例的 18 次请求独立复核完成。无分母调整。

```bash
PYTHONPATH=src:tests .venv/bin/python -m benchmark.routing_v2_fixed
PYTHONPATH=src:tests .venv/bin/python -m benchmark.live_baseline \
  --allow-live-model --allow-four-reviewed-technical-spans --out /tmp/routing-v2-new.json
PYTHONPATH=src:tests .venv/bin/python -m benchmark.live_baseline --verify-sql --out /tmp/routing-v2-new.json
```

Python 全量 222 项，221 项通过，1 项真实 Gemini 可选测试跳过。前端 5 项测试、类型检查和生产构建通过。下一步优先复核四个仍失败的规划提案。
