# Benchmark Infrastructure v1

基于已核对的远端 `main@8e3edc2`。统一入口只组合已登记的测试和评测器。现有 Agent、Planner、Retriever 和 SQL 执行路径没有变更。

## 数据集

[Golden Schema](../../tests/benchmark/golden.schema.json) 定义每条记录的身份、来源 JSON Pointer、原始 ID、原始问题、会话上下文、任务类别、预期业务状态、指标和工具、计划约束、独立 SQL 来源、引用来源和审核状态。[冻结数据集](../../tests/benchmark/golden_v1.json) 和 [迁移报告](../../reports/warehouse/benchmark_migration_v1.json) 可供复核。

`legacy_reviewed` 表示沿用仓库的人写预期，且没有发生本轮新的双人标注。`needs_review` 表示该记录只有粗粒度期望或生命周期上下文，还需人工给出可评分标签。101 条中 94 条沿用旧预期，7 条待复核。迁移不把归档模型输出变成金标。为便于复核，同一问题出现在不同 fixture 时，每条原记录仍有独立 `case_id` 与来源。4 条只引用其他 fixture 的 Unified 测试没有自己的 `question` 字段，Runner 保留这四条引用测试的 ID，所以执行数与 101 条问题记录的迁移口径不同。

规范化文本只做 NFKC、casefold 和符号移除。显式语义等价表采用既有口径中的有限等价表达。模板 ID 可进一步去除日期参数，保持语义等价与参数变化的区别。故障、Owner 和撤下状态由上下文区分。检查器阻止同一规范化文本、语义族或模板跨 split。全部迁移记录进入 development。举例，1 月「下单笔数」与「订单量」可共享语义族，而「去年有多少下单笔数」只能共享模板。分组规则是保守治理起点，未标出的近义族仍需人工审查。

## 运行

在仓库根目录执行。默认使用固定提案。仅显式选择 Gemini 或 Browser 并传入 `--allow-live-model` 才会调用真实外部模型。浏览器模式使用现有测试签名、Chrome 和 Vite，先释放已有的 8000 端口，保留 Vite 在 5173 运行。沿用 [本机准备](LOCAL_RELEASE.md) 和 [授权合成样例](RELEASE_DEMOS.md)。

```sh
export PYTHONPATH=src:tests
.venv/bin/python -m benchmark.runner --migration
.venv/bin/python -m benchmark.runner --mode deterministic
.venv/bin/python -m benchmark.runner --mode fixed
.venv/bin/python -m benchmark.runner --mode replay --archive-variant baseline
.venv/bin/python -m benchmark.runner --mode postgres
.venv/bin/python -m benchmark.runner --mode gemini --allow-live-model
.venv/bin/python -m benchmark.runner --mode browser --allow-live-model
```

可用 `--out /tmp/result.json` 保存单次。默认基线按模式独立呈现。deterministic 调用已有纯本地测试；fixed 运行 Unified 与 Business Planner 的固定提案、合成执行结果；replay 对历史捕获的原始业务结果重新评分，SQL 使用归档中独立计算的 ODS 值。replay 不连接数据库、不重发 Prompt、不把历史延迟或 Token 当成当前的开销。postgres 使用固定模型、真实指标查询和独立只读 ODS 对账，另用固定向量检索临时私有合成规则，结束撤下。gemini 使用已有 23 个综合用例的授权模型路径。browser 使用已有 Chrome 断言，按一个断言组合单独计数，聊天遥测行没有独立金标。

每份报告记录模式、代码提交/工作树状态、数据集 SHA、执行用例集合、计时、模型配置/响应标识和 Prompt 版本。实库模式运行前后对七张 ODS 表的行数及按主键排序的内容摘要作一致性检查。快照是 ODS 内容指纹，业务查询与独立参考连接并非同一数据库事务。历史报告缺少原快照指纹时以归档 SHA 标记。不同模式、模型、快照及用例集合产生不同 `comparison_key`；Runner 不合并单次报告。

## 评分与判读

| 指标 | 自动评分口径 |
| --- | --- |
| Intent、Plan、Tool Route、Task Success | 有可用标签才使用原评测断言。正向 Plan 对照关键计划约束，拒绝属于任务状态和工具边界。固定提案的任务成功包含其原有合成结果断言。 |
| Entity P/R/F1 | 指标 ID 集合做 micro 计数。精确率分母是预测实体总数，召回率分母是预期实体总数，F1 分母是二者之和。 |
| SQL Result | 只对独立 ODS 或已经独立计算的历史 ODS 值计分。金额精度由原 Decimal 对照保持。 |
| Retrieval Recall@6 / MRR@6 | 仅有独立相关性标签及候选时计分。真实 PG 基线标签为授权合成规则的来源和人工确认的小标题，使用索引发布 ID 识别来源；无相关目标的拒答没有检索分母。 |
| Citation Validity | 验证已标注来源、正文摘录和版本等索引证据。是否足以支持回答的每个主张单列人工项。 |
| Calls、Tokens、Latency、Errors | 计真实外部生成、观测到的工具调用和可取得的 Token。固定或重放可明确为 0 次生成，缺少观测的字段为 N/A。P50/P95 使用最近秩，仅按有计时记录的案例；错误分类统计失败。 |

计分指标输出分子、分母与 `not_applicable`。`not_applicable` 按案例数计，Entity 与 Recall 的分母则按实体或相关项计。分母为 0 时 `value=null`，也用于 `citation_semantic_support` 的未人工评审项。浏览器和部分旧报告缺少逐用例工具时延或 Token 计量，不推导缺失字段。外部运行失败只输出错误类别，报告不保存密钥、原始 Prompt、未脱敏内容或全量客户明细。

[第一份统一 Baseline](../../reports/warehouse/benchmark_baseline_v1.json) 按模式保存实测。评分重现测试比较固定用例的计分字段和运行身份，墙钟耗时本身允许波动。
