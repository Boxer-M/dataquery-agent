# Business Planner Reliability v3

PR #48 已合入远端 main，基线代码为 `089c376`。此前路由报告的真实调用早于最后两处补丁。本轮用合并后的最终路由代码补跑了完整 58 条真实基线。基线和候选使用同一 Golden 版本、ODS 快照、模型、样本、评分器和授权材料。只有 Planner Prompt 和输出 Schema 发生版本变化。Holdout 明文保持封存。

[逐题配对](../../reports/benchmark/business_planner_v3_pairs.json)包含样本和评分器识别信息。[基线逐请求](../../reports/benchmark/business_planner_v3_baseline.json)与[候选逐请求](../../reports/benchmark/business_planner_v3_candidate.json)分别保留调用、重试、工具与耗时。[基线提案](../../reports/benchmark/business_planner_v3_baseline.proposals.json)与[候选提案](../../reports/benchmark/business_planner_v3_candidate.proposals.json)记录问题、已脱敏的结构化字段、实际提供的 Catalog 摘要、Schema 字段、模型版本、Prompt 哈希和校验结果。

## 提案级诊断

| 用例 | 真实基线提案和拒绝 | 正式 Catalog 与预期计划 | 候选 |
| --- | --- | --- | --- |
| gross-discount | `time_windows` 丢了原文开头的「从」，复现 `window_mismatch` | 两个商品明细金额按下单时间、品类汇总，原文日期范围作为一个完整片段 | 两步汇总通过 |
| items-products | 历史 `detail_required` 本轮未复现。模型返回 `summary`，但把「重复商品只在后一列去重」列作未决规则，返回澄清 | 明细数按 `order_item_id`，商品数按 `product_id` 在整个请求窗去重，两个聚合都支持品类 | 两步汇总通过 |
| account-pair | 把「同一账户只计一次」列为未决规则，复现 `unresolved_business_rules` | 两个指标按 `customer_id`，至少一单和至少两单分别计数，都支持国家分组 | 两步汇总通过 |
| payment-pair | 把「使用支付事件时间」列为未决规则，复现 `unresolved_business_rules` | 两个指标使用 `paid_at`，全部尝试和失败尝试分别计数，都支持支付方式 | 时间规则已被识别。模型只返回「支付方式」，原文是「按支付方式」，仍拒绝分组 |

gross-discount 是 **A，模型生成错误**。items-products、account-pair 和 payment-pair 的真实基线证据均指向 **A 与 B，模型误标且输入缺少 Catalog 规则**。旧 Prompt 提供指标 ID 与名称，但没有给出这几项定义、事件时间、计数粒度或去重语义。旧请求只有 JSON MIME / 文本约束，**C，缺少 API Schema 约束**是横切风险，现有字段足以表达四题，无法证明 Schema 本身导致历史错误。四题没有证据指向 **D，校验器误判**或 **E，业务口径真的缺失**。保留的失败提案与用户需求之间仍存在明确差异。

固定提案对照将同一份实际基线提案、候选提案和人工构造的合法汇总提案交给两个版本的 Validator。两个版本逐项一致，合法计划的请求逐项匹配正式指标与 Golden。另用明确标为构造的 `detail` 提案复现 `detail_required`，确认不会把逐笔明细强转汇总。该构造不是历史模型原文。

## 方案与改动

| 方案 | 设计与取舍 |
| --- | --- |
| A，改进已有 Planner 输入输出 | 输入附上原文时间片段和已明确匹配的可用指标的正式规则。JSON Schema 约束原有字段结构和枚举。上下文增加少量 Token，仍为单次规划调用 |
| B，预解析或规范化 | 可将精确引用还原为原文片段，但必须同时证明备选唯一并保留用户原有语义。存在掩盖模型遗漏的风险，尤其是分组与未决业务规则 |

选择 A。Planner v3 使用现有字段表达汇总和明细，并为已有 SQL + PDF 路径保留原文 PDF 字段。生产校验仍逐项验证时间、分组、规则和指标，Owner、Capability 和 SQL 校验保持原路径。没有新增 Planner 模型调用。本轮 payment-pair 不通过的原文分组问题保留在结果中。

## 配对结果

| 指标 | 合并基线 | 候选 |
| --- | ---: | ---: |
| Plan Validity | 4/8，50 N/A | 6/8，50 N/A |
| Tool Routing | 37/43，15 N/A | 39/43，15 N/A |
| SQL | 17/23，35 N/A | 19/23，35 N/A |
| 已执行 SQL 与独立 ODS 一致 | 17/17 | 19/19 |
| E2E | 51/58 | 53/58 |
| 生成 / Embedding / 工具 | 47 / 22 / 51 | 45 / 22 / 55 |
| 输入 / 输出 Token | 29,758 / 3,784 | 30,996 / 3,588 |
| P50 / P95 | 2.710 / 9.666 秒 | 3.070 / 9.827 秒 |

候选另用同快照直接复核 13 个结构化用例的 18 条 SQL 请求。整体 5 条失败转成功、3 条成功转失败。其中 OCR 由服务失败转为成功，单列为外部服务波动。两轮请求数和延迟包含服务重试等因素。基线出现各一次 503、429 和超时，候选出现两次 503。

四个诊断案例因已用于分析，单独记录。Development 以现有固定测试回归语义和保护边界。额外分析的 19 条未用于本轮调试的 Validation 排除了以往路由失败分析已查看的题目及本轮诊断四题，完整名单在逐题文件。该组 E2E 从 **19/19 降至 16/19**，Plan 从 1/1 降至 0/1，Routing 从 17/17 降至 15/17，SQL 从 7/7 降至 5/7。三个退化分别是退货异常双指标的模型误选 `cross_filter` 且没有阈值、折扣单指标被拒绝筛选、现有 RAG 路径未给出有效引用。这个结果限制了对候选泛化能力的判断。

## 回归与复现

Python 全量复测 224 项，223 项通过，1 项独立真实 Gemini 可选测试跳过。首次全量测试的现有 PDF 复核用例曾触发 `unsafe_document_or_question`，相同代码重跑通过。未修改 PDF 或文档安全逻辑。前端 5 项、TypeScript 类型检查和生产构建通过。

```bash
PYTHONPATH=src:tests .venv/bin/python -m benchmark.planner_v3_live --allow-live-model --out /tmp/planner-v3.json
PYTHONPATH=src:tests .venv/bin/python -m benchmark.live_baseline --verify-sql --out /tmp/planner-v3.json
```
