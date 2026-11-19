# 混合意图路由与双路检索 v1

基线 `77f86db`，分支 `codex/hybrid-routing-retrieval-v1`。生产变更只涉及既有检索、
路由、上下文与Trace；未修改Metric Catalog、指标SQL、Validator业务规则或数据库权限。
这是完整功能批次，包含测试和原始验收报告，超过日常200行限制；没有新增依赖或框架。

## 两项可调用功能

1. `Retriever(store, embedding, mode="hybrid").search(...)`：默认双路；`mode="vector"` 可单独运行纯向量对照。
   `KnowledgeService.local` 继续复用这一入口，正式 Catalog Exact Lookup 不走相似检索。
2. `ConversationManager.process_message(...)`、原 POST `/api/chat`：完整明确的单指标查询可直接执行；
   短追问只修改确认条件；复杂问题仍经既有模型提案/Validator；冲突先澄清。

```python
# owner 来自可信服务端身份；不要接受客户端伪造Owner。
retriever = Retriever(store, embedding, mode="hybrid")
rows, trace = retriever.search("扫描核验页面记载了多少订单？",
    today=today, owner=owner, reviewed_pdf=True, top_k=2)
```

## PostgreSQL 全文检索与RRF

- 向量通道：cosine距离，独立取至多3K/30条，再应用0.43门槛；纯向量模式没有旧关键词0.08加分。
- 全文通道：从全部可见Chunk独立查询，使用 `to_tsvector/simple`、`to_tsquery`、`ts_rank_cd`。
  词元按OR组合，不复用向量LIMIT结果。按Chunk ID去重，RRF分数为各通道 `1/(60+rank)` 之和。
- 中文显式补充U+4E00–U+9FFF字符二元组，英文/数字使用词元；不是自然语言中文分词器。
  PostgreSQL原生simple把“退款审核”保存为整词，应用补充分解为“退款/款审/审核”。
- 没有BM25或模型Rerank，也未引入GIN索引。当前在可见小语料上计算tsvector；不宣称大规模性能。
- 两条SQL复用同一可见性CTE和只读Repeatable Read快照；保留RLS、Owner、当前版本、索引版本和PDF审核门。
  过期内容只保留必要提示元信息，不作为有效证据。无召回不再误报为索引版本不兼容。
- API/History Trace保存通道候选ID、原始向量池、融合方式及最终ID；不保存额外凭据或原始模型Prompt。
  引用仍经过现有逐字匹配及PDF当前版本、Hash、Owner复核。

## 冻结样本与评测边界

`tests/hybrid_cases_v1.json` 在实现前冻结：9条路由、5条检索，包括2条有页码的正例，
以及无答案、跨Owner、撤销3条负例。复用合成测试PDF，不读取Holdout。
评测K=2；现有知识回答服务默认上限6。真实语料只有2个有效Chunk，按前2项重评；
本次@2和@6集合相同。不能以这个小集证明泛化提升。

路由固定对照复用可信Git源码加载器，运行旧77f86db的Router/Manager/Context及相同的固定提案，
其他共用能力保持一致；这是路由消融，不是完整旧系统部署。两次适配器调试失败保留于本地忽略目录，
不计入真实模型成绩。没有修改Golden答案。

| 验收 | 改前/纯向量 | 改后/双路 |
|---|---:|---:|
| 原33条固定提案回归 | 33/33 | 33/33 |
| 本批9条固定提案整体任务 | 6/9 | 9/9 |
| 本批有路由标签的固定样本 | 5/8，N/A 1 | 8/8，N/A 1 |
| 实时Gemini整体任务 | 未运行完整9题旧版 | 8/9 |
| 实时路由（有标签） | 未测 | 8/8，N/A 1 |
| 真实进入Planner的计划通过 | 未测 | 1/2，其他7题N/A |
| 真实Embedding Recall@2 | 2/2，N/A 3 | 2/2，N/A 3 |
| 真实Embedding MRR@2 | reciprocal-rank和2/2=1.0 | reciprocal-rank和2/2=1.0 |
| 正例引用支持（预期原文+页码） | 2/2 | 2/2 |
| 无答案/Owner/撤销正确拒答 | 3/3 | 3/3 |

时间冲突样本只有“应澄清”的标签，路由项为N/A，不默认算通过。初始原始报告的缺失路由默认True
由统一汇总器按同样规则重评三组结果；没有靠改变Golden或只改候选分母制造提升。
支持率按冻结原文和页码判定，不是任意答案的语义蕴涵评分。

## 关键词独立命中的完整记录

`reports/warehouse/hybrid_keyword_proof_v1.json` 使用真实PostgreSQL、真实OCR及审核PDF，
但故意使用固定正交向量做通道独立性测试，不能算作真实Embedding效果。
七份同源测试PDF形成7个第1页干扰项，超过K=2时原始向量池LIMIT6；第2页向量与问题正交。
问题“48” → 全文命中不在原始向量池中的第2页Chunk → RRF进入Top2 →
引用 `Order count: 48`，PDF第2页审核文本第1–3行 → 当前版本、Hash和Owner校验通过。
另有中文“退款审核如何通知”通过全文通道命中“退款审核通过后站内信通知。”。
两个示例都明确为合成测试资料；临时文档在测试结束后撤下。

## 真实执行与完整Trace

实际生成模型 `gemini-3.5-flash-lite`，配置 `models/gemini-3.5-flash-lite`；
Embedding `gemini-embedding-001`，768维。全部真实阶段合计生成适配器18次、成功Embedding批次18次，
返回Token为输入6707/输出1465/总8172；包含1次503、2次504及实际重试，失败调用未提供Token。
Embedding Token未提供；没有可靠账单价格，因此不计算费用。计数不等于SDK底层全部HTTP请求。

改前与候选SQL+PDF均正确返回2312和PDF第2页48；最终联合复验也通过ODS、引用、DAG、History。
改前发生503，耗时31.502秒，候选20.923秒，最终复验21.946秒；不能把服务波动归因于Router加速。
9条真实路由含5条零模型路径，P50=64ms/P95=30245ms（nearest-rank），不是纯LLM延迟。
后续只读快照一致性和候选池Trace补充由数据库回归验证，没有伪称再次完成全部真实模型复跑。

真实Trace示例：

```text
2025年1月订单量是多少？ → exact_metric_explicit_window → Capability/Metric Tool → 2312，模型0次
那 2 月呢 → context_time → 继承order_count，只修改起止日期 → 模型0次
那 2 月，只看mobile渠道 → context_followup → 保留指标，显式替换渠道 → 模型0次
退款最多 → return_measure_conflict → 澄清退货记录数/金额 → 工具0次
SQL+PDF → sql_pdf_joint → planning.ready → metric + pdf + summary → 全部成功
```

仍失败的历史样本：原问“从2025-04-01至2025-06-30按品类同时列折扣前商品记录金额、商品行折扣金额；分别保留两列。”
模型在一次504后重试，输出time_windows=["2025-04-01至2025-06-30"]，省略“从”；
Validator按原文片段一致性返回window_mismatch，不执行SQL。外部重试与业务校验失败分别记录。
下一轮唯一优先项：仅对可由原文和日期解析共同证明等价的前导“从”做受控提案规范化；
同一问题应得到正确两个金额列，同时篡改日期、冲突时间、缺失窗口仍必须拒绝。不要放宽Validator。

## 回归、报告与复跑

- Python离线全量：241 passed、84 skipped。既有33条固定提案仍为33/33。
- 真实PostgreSQL回归41 passed（HITL另轮启用）；含HITL和新增检索的补充轮16 passed。
- 最终补强“原始向量LIMIT池之外”的证明及Catalog/DAG/HITL专项15 passed；各轮有重叠，不相加。
- 前端测试11 passed；TypeScript及构建通过，既有大chunk警告保留。
- 原始真实报告、固定对照、关键词证明及 `hybrid_summary_v1.json` 全部保留；摘要可离线重评。

```sh
PYTHONPATH=src:tests .venv/bin/python tests/evaluate_unified_agent.py --mode fixed
PYTHONPATH=src:tests .venv/bin/python tests/evaluate_hybrid.py --mode fixed --baseline --out /tmp/before.json
PYTHONPATH=src:tests .venv/bin/python tests/evaluate_hybrid.py --mode fixed --out /tmp/after.json
# 以下显式调用收费模型，仅发送本项目已授权的合成PDF片段及必要指标上下文：
PYTHONPATH=src:tests .venv/bin/python tests/evaluate_hybrid.py --mode live --out /tmp/live.json
.venv/bin/python tests/summarize_hybrid.py
CI_NO_EXTERNAL_MODELS=1 HYBRID_POSTGRES_TESTS=1 KNOWLEDGE_POSTGRES_TESTS=1 HITL_POSTGRES_TESTS=1 .venv/bin/python -m pytest tests/test_hybrid_routing_retrieval.py tests/test_hitl.py tests/test_business_knowledge.py -q
```

可用于简历：实现带Owner/版本治理的pgvector与PostgreSQL全文RRF检索，支持中文二元组；
9条固定提案路由对照由6/9到9/9，真实Gemini联合查询通过独立ODS和PDF页码核验。
不要把受控正交向量测试写成真实Embedding召回率提升。

## PR #66 CI 补充修复

用户提供的运行日志 `44516352272`：142 passed、1 failed，唯一失败为
`SpecialistPostgresTests.test_two_specialists_and_single_metric_fast_path`。
原断言要求单指标有1次模型调用，但新确定性Fast Path实际为0；SQL数值检查已经通过。
修复断言为零模型调用，并显式检查命中 `exact_metric_explicit_window`，没有跳过测试或放宽执行门。
Node.js 20提示为独立警告，不是本次退出原因。

补充不依赖macOS OCR的PostgreSQL全文/RRF集成用例：中文关键词命中向量原始LIMIT池之外的片段，
跨Owner和撤销后均不可读。该方法放在CI原本启用的KnowledgePostgresTests中。
同时将已合并的会话历史API测试纳入CI集成清单。所有模型和向量均固定，不调用Gemini。
本地原失败专项及知识测试15 passed；默认Python回归241 passed、85 skipped；安全检查通过。
完整Compose隔离项目 `dataquery-ci-pr66-fix` 使用与CI相同的四份配置，146项集成全部通过，
真实HTTP冒烟返回2312且History Trace一致，外部模型调用0，check容器退出码0。
本地为Linux ARM64容器，并非GitHub托管runner的新运行；云端结果以修复提交后的检查为准。
`smoke.source_commit` 为构建时基线77f1cbf，镜像已包含本次工作区测试修补。
