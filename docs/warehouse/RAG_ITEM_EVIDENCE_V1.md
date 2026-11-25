# PDF 双问逐项证据判定 v1

基于最新 `origin/main=e9d388f`，分支 `codex/rag-item-evidence-v1`。参考 `4a87fc2` 预标注集、难例整轮报告和两题三配对复测。未合入历史候选a50654e的全局Prompt修改；单问继续使用main的knowledge-extractive-v1。

## 目标、输入、输出与调用链

已复现：两项事实和引用都有，模型仍返回partial；另一项未知时，模型有时连已知答案也不返回。旧校验器正确检查原文存在性，却不负责纠正模型的整体完整性标签。

本版输入是明确的PDF双问；输出为两个稳定子项、各自的可核验原文答案/不足原因及引用，整题由服务端计数。调用链：

`原PDF检索/证据过滤 → 确定性拆分两项 → 同一次知识生成 → 按ID独立校验原文 → 按项复核权限/当前版本 → 服务端聚合 → 逐项展示/History/Trace`。

不新增拆分模型、Planner、工具、调度器、数据库表或依赖。新阶段名knowledge_items只区分一次模型调用的输出合同，仍使用原Runtime的权限、并发槽及调用预算。

## 支持范围

只在现有KnowledgeService的PDF-only路径启用。可拆形式：可选“根据已审核PDF，”前缀，共享明确的“YYYY年M月＋ASCII渠道标识＋渠道”，两个名词事实用“和/与/及”连接，以“分别是多少/分别多少”结束。

例如：

- `根据已审核PDF，2025年1月web渠道的订单量和支付尝试次数分别是多少？`
- `根据已审核PDF，2025年1月web渠道订单量和实际退款到账天数分别是多少？`

不是全文题目硬编码：月份、渠道和名词均由原文提取，未维护新的业务同义词或指标SQL。按原文次序得到item_1、item_2；每项继承同一个明确的年月/渠道，不能把模型生成的其他条件替换进来。

未覆盖：不同月份/渠道分别作用于两个子句、无明确年月渠道、三个以上事实、比较/归因复杂句、其他提问格式及一般文本文档。重复标签、额外并列连接词拒绝拆分。这些输入继续走原合同，不偷偷规范化或执行新的SQL。SQL Planner和检索算法不改。

## 结构化合同与逐项安全

模型只返回：

```json
{"items":[
  {"id":"item_1","status":"supported","claims":[{"chunk_id":"...","quote":"逐字原文"}]},
  {"id":"item_2","status":"insufficient","claims":[]}
]}
```

模型不再返回整题status。沿用抽取式答案：子项answer由通过校验的quote组成，不展示未经引用验证的自由回答。

- ID来自服务端拆分，必须完整、唯一；未知ID不能映射成已有项或贡献支持计数。
- 某ID缺失或重复，该项为insufficient，分别记录missing_item/duplicate_id；不会挑选重复中的方便版本。
- 某项格式非法、状态与claims不一致或任一quote非法，该项整体不计有据，保留另一有效项。
- 原文校验直接复用validate_claim。引用只能来自已召回的片段，继续校验逐字原文和安全内容。
- 当前PDF权限/版本复核若删除某项引用，该项所有引用和answer都清理，reason_code=source_invalidated，其他有效项保留。外层SQL＋PDF的第二次来源复核也同步更新嵌套子项和渲染，避免根引用已清空、子项仍携带失效文本。
- 整体JSON/顶层结构错误及模型异常沿用现有安全失败，不把模型失败伪装为已完成的子项判断。
- ID归属和引用原文校验仍不能自动证明语义支持；模型若把某真实原文错误分配给子问题，仍须由逐项评测发现，本版不宣称通用语义蕴含验证。

| 有效项 | 整题evidence_status | 内容 |
|---|---|---|
| 2/2 | supported | 两项原文答案，各自引用 |
| 1/2 | partial | 保留有据项，按标签指出另一项证据不足 |
| 0/2 | insufficient；无活动证据时保留原expired等语义 | 拒答、不显示失效原文 |

KnowledgeService Python响应增加可选evidence_items；原answer_zh、citations等字段保留。History只在存在子项时增加审计字段；Trace保存拆分方法、稳定ID、原因码和支持数。现有HTTP接口沿用答案、引用与Trace投影，本轮不新增前端组件或公开API字段。

## 离线及真实数据库验证

15项离线专项覆盖：2/2、1/2、0/2、缺失、重复、未知ID、非法子项、单项坏引用、当前版本失效、外层二次校验、整体坏结构、原单问合同、无证据不调用模型、拆分范围、History与Trace。

全量离线 **279 passed、89 skipped**。额外的真实PostgreSQL＋固定输出专项 **1 passed**，验证UnifiedAgent现有PDF入口、两项结果持久化、API/History Trace相等、跨Owner History拒绝和相同message_id不重跑模型。它不是Gemini验收，不混入下表。

## 真实模型配对

沿用4a87fc2同一PDF字节和标签。两题各3组main/候选交替，共12个真实知识生成试次；本轮一次新审核版本固定，旧已撤销版本未复活。

同一文档版本、Owner、原文和查询向量；每题仅检索一次pgvector混合结果，随后冻结片段及顺序，每次模型前仍做新的PG RLS/原文一致性检查。模型名和生成参数相同，响应Schema/Prompt改为逐项合同是本轮实验变量，不能声称两版Schema相同。没有额外拆分或审查模型调用。

| 题目 | main完整通过 | 候选完整通过 | main已有证据保留 | 候选已有证据保留 |
|---|---:|---:|---:|---:|
| 订单量＋支付尝试次数，两项都有据 | 0/3 | 3/3 | 6/6事实 | 6/6事实 |
| 订单量＋退款到账天数，仅前项有据 | 3/3 | 3/3 | 3/3事实 | 3/3事实 |
| 合计 | 3/6 | 6/6 | 9/9 | 9/9 |

所有候选答案均按item_1/item_2核对，不能仅靠整题标签通过：

- 第一题item_1引用第1页order_count=1337；item_2引用第5页payment_attempt_count=1408，三次均supported/supported。main三次事实齐全但原始整体status为partial。
- 第二题item_1保留第1页1337，item_2三次均insufficient/空引用，并按“实际退款到账天数”标签明确提示不足；main本轮也三次partial并保留已知事实。旧报告曾在相同条件下波动，因此**本轮没有测得已有证据保留数提升**，不能用历史不同轮次差异冒充效果。
- 错误引用数两版均0。模型正确响应12/12，有效配对6/6，HTTP429=0，未完成=0。

| 成本 | main | 候选 |
|---|---:|---:|
| 真实生成调用 | 6 | 6 |
| 输入Token | 6975 | 7773 |
| 输出Token | 1026 | 1310 |
| 总Token | 8001 | 9083 |

共12次生成调用、17084生成Token。新增查询Embedding仅1批2输入；文档向量复用，新文档Embedding为0，Embedding Token未知。适配器调用与SDK内部HTTP重试不混称，未估算费用。

第一次真实验收后补了外层来源复核同步嵌套子项的防护，并抽取共用渲染函数；没有改Prompt、拆分、逐项解析或聚合规则。15项离线与真实PG固定输出通过；六个保存的真实候选响应对最终解析/聚合/渲染离线重放结果完全一致。**未为该来源失效防护重复调用Gemini，也未宣称完成其真实中途撤销模型验收。** JSON分别保存真实调用时源码Hash和交付源码Hash。

## 文件与复跑

- `src/dataquery_agent/rag/item_evidence.py`：拆分、Schema、逐项校验、计数和晚期来源过滤。
- `rag/answer.py`：复用原知识调用，分组渲染；`agent_prompts.py`注册Schema和Prompt版本。
- `query_history.py`、`runtime_trace.py`：可选子项审计；`rag/routing.py`同步外层版本复核。
- `tests/evaluation/item_evidence_pair_v1.json`：独立预标子项答案、ID、证据页和值；4a原Golden与PDF按原字节复用，不更改预期。
- `tests/evaluate_item_evidence.py`：真实配对Runner；`reports/warehouse/rag_item_evidence_v1.json`保存每次原始JSON、子项校验、最终输出、逐项评分、Token、来源Hash及以前两轮报告Hash。

```sh
PYTHONPATH=src CI_NO_EXTERNAL_MODELS=1 /Users/yxz/Documents/'DataQuery Agent'/.venv/bin/python -m pytest tests/test_item_evidence.py -q
PYTHONPATH=src CI_NO_EXTERNAL_MODELS=1 ITEM_EVIDENCE_POSTGRES_TESTS=1 \
  DATAQUERY_TEST_CONFIG_ROOT='/Users/yxz/Documents/DataQuery Agent' \
  /Users/yxz/Documents/'DataQuery Agent'/.venv/bin/python -m pytest tests/test_item_evidence_postgres.py -q
# 显式调用真实模型；请输出到新文件，不覆盖既有验收记录。
PYTHONPATH=src /Users/yxz/Documents/'DataQuery Agent'/.venv/bin/python tests/evaluate_item_evidence.py \
  --live --config-root '/Users/yxz/Documents/DataQuery Agent' --out /tmp/item-evidence-rerun.json
```

所有配置只从本地安全目录读取，不复制或提交密钥。测试文档已撤销、活动版本为空；PG固定测试的会话也清理。没有数据库迁移、角色权限变化、依赖增加或对数仓数据的修改。

## 交付判断和限制

两题证明逐项合同和服务端聚合值得在**同样可拆句式**上扩大少量评测；不能宣称通用多问或全部RAG拒答已解决。更复杂句式继续旧路径；模型仍可能错配真实引用或把有据项判无据，不能用引用存在性当作语义证明。本轮到实现、专项测试、两题配对报告和本地提交为止，不扩大评测或改Planner。

本批源代码保持薄接口，测试/冻结标签/完整脱敏报告及既有合成PDF导致总行数超过通常200行，属于本次完整可验收批次的明确例外。下一步仅审查该实现及逐项评分，再决定是否扩展覆盖。

## 2026-11-25：证据绑定与分流审查修复

基于8ec9c1f追加修复，未改Prompt、指标目录或权限规则。新增回归先在原代码运行：4失败、2通过，复现交换引用仍supported、单项错配仍supported及子项另写渠道错误继承。

- `item_evidence.bind_support`复用MetricEntityResolver、正式名称/ID与既有ALIASES；只有完整标签唯一精确匹配才绑定。不引入相似度或新别名。
- 先执行逐字引用及Owner/当前版本/撤销复核，再校验每项原文中的指标、年月和渠道。失败原因`unsupported_item_evidence`只移除该子项，重新按有效项数聚合0/1/2为insufficient/partial/supported。
- 首版只支持**完整单行事实**：`Period YYYY-MM; channel CHANNEL; metric_id: NUMBER.`，NUMBER为十进制整数字符串或小数字符串，可带负号，无千分位或科学计数。引用须完整等于来源中的某一行，包含自己的年月、渠道、指标和数值。不会从邻行补上下文，也不会从回答正文取数或重算指标。数值直接来自这条通过逐字复核的事实，不另行断言PDF数值等于实时数据库。
- 无完整条件的简写、自然语言表格、多行拼接、未知/歧义指标不作“已支持”判定。不是通用语义蕴含验证器；保守拒绝可能增加，需另行评测。
- 同一Chunk的两条独立原文可分别支持两项；不按Chunk ID排斥复用。交换指标、错月份/渠道、来源不存在的数值或非完整行引用均不放行。
- 继承前检查子项是否另有年月日/渠道或未解决的选择表达；有则以原问题走旧路径，不拼出冲突条件。数字日期不同的原范围外题仍被原全匹配边界排除。

验收：专项23通过（原15＋新增8），全量离线287通过/89跳过。PostgreSQL固定响应1通过，覆盖实际UnifiedAgent入口、History、跨Owner历史读取拒绝和消息重放幂等。原两条范围外题另以固定响应复核通过。原两个审查复现已由新增测试直接断言修复后的正确行为；本轮生成模型/Embedding调用均0，不改历史真实评测结果。范围外真实A/B仍有一题受503阻断，未宣称已完成。

```sh
PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_item_evidence.py tests/test_item_evidence_binding.py -q
PYTHONPATH=src:tests .venv/bin/python -m pytest -q
# 独立worktree使用主目录的venv绝对路径；配置目录只作本地读取，不复制凭据。
ITEM_EVIDENCE_POSTGRES_TESTS=1 DATAQUERY_TEST_CONFIG_ROOT='/Users/yxz/Documents/DataQuery Agent' \
 PYTHONPATH=src:tests '/Users/yxz/Documents/DataQuery Agent/.venv/bin/python' -m pytest tests/test_item_evidence_postgres.py -q
```
