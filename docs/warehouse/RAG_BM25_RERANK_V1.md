# BM25 与独立 Rerank 检索实验 v1

基线main：`6f069b8`（已合并PR#73）；实验日期2026-11-25，分支`codex/rag-bm25-rerank-v1`。只做本地提交，不推送。

## 目标、实现和安全边界

- 输入：已有Owner范围、问题和真实Query Embedding；输出：TopK候选及检索/精排Trace，交给原KnowledgeService和Citation校验。
- `rag/ranking.py`：标准库实现Okapi BM25，k1=1.2、b=.75，正IDF `ln(1+(N-df+.5)/(df+.5))`，tf饱和与长度归一化。算法参考[Lucene BM25Similarity](https://lucene.apache.org/core/10_2_2/core/org/apache/lucene/search/similarities/BM25Similarity.html)，本项目不是Lucene全文引擎。
- BM25语料是PostgreSQL同一次权限范围查询得到的全部active兼容Chunk，不是向量TopN。按简单英文/数字词元和中文二元组计tf/df；不额外做同义词扩展，也不声称中文词典分词。最多2000Chunk/200万字符，超限明确报`bm25_corpus_limit`，不在截断语料上伪算IDF。大库需后续持久化倒排索引。
- FTS路径完整保留，仍采用PostgreSQL simple全文+CJK二元组及ts_rank_cd。两路仍由RRF（常数60）融合，按Chunk ID去重。
- `GeminiReranker`是**独立的LLM listwise精排**，不生成答案，不是cross-encoder、BM25分数重命名或RRF。只收TopN已授权文本，必须返回完整且唯一的ID排列；未知/重复/缺失ID、超时、网络/模型错误均保留RRF顺序。原有向量通道可选pgvector或Milvus，未增加第二套存储。
- 默认仍是FTS+向量+RRF，Rerank关闭。程序可传`keyword_backend="bm25"`、`reranker=GeminiReranker(...)`、`rerank_top_n=N`；也可显式设置`RAG_KEYWORD_BACKEND=bm25`、`RAG_RERANK=gemini`，不要求启用。N须>=K且<=30；请求文本<=24000字符；默认timeout5000ms，实验10000ms，最大20000ms；输出预算1500Token，每次最多1次模型调用、SDK attempts=1。
- 不新增依赖。Gemini SDK、PostgreSQL角色、RLS、文档审核/发布/撤销机制均复用。
- RRF后精排前已有新快照复核，精排后再次PG复核Owner、active版本/index、撤销和审核状态；引用生成后仍执行现有current_pdf_citations。精排失败也必须完成后置复核。
- Trace包含BM25语料大小/参数、RRF顺序、rerank输入/输出ID、成功/回退原因、HTTP状态、调用数、Token及耗时；runtime_trace已有工具投影保存这些字段。不记录API Key或provider异常全文。

## 冻结数据与实验协议

- 标签：`tests/evaluation/rag_ranking_v1.json`，调用前冻结，SHA256 `bb42e2d8ece7ae317cb3882b12e0f64439f6d52c79164dc97207201268b2abaf`。来源为4a87fc2已审核六页PDF和独立ODS统计。
- 同一Owner、同一文档版本`pdf-b189040051a301742b646516beaf50900bfe305dbdf7b979`，6个真实文档向量复用；10条Query Embedding一次生成，模型gemini-embedding-001、768维归一化、RETRIEVAL_QUERY。四组共用同一向量，不对每组重新Embedding。
- 生成及精排配置模型models/gemini-3.5-flash-lite（成功精排实际返回gemini-3.5-flash-lite），精排Prompt版本gemini-listwise-rerank-v1；未显式设置temperature/top_p。
- A/B/C/D各10次，按问题交替组别顺序。8条有答案、2条无答案（3月、实际退款到账天数）；中文别名、正式Metric ID、相近月份/渠道均覆盖。K=2、N=6，候选池每通道min(3K,30)。
- 每个有答案问题只有一个精确相关页，二元相关性；不把相近月份/渠道或不同口径列为相关。MRR=首相关名次倒数，nDCG使用log2折损及理想排序归一化；无相关证据题N/A，不当作零或通过。重复页不重复获益。
- 延迟不含已复用向量的外部Embedding时间，含真实PG、RRF、精排和最终权限复核。首次Embedding5536ms单列。

## 四组结果

| 组 | Recall@2 | MRR@2（和/分母） | nDCG@2（和/分母） | P50 ms | P95 ms | 精排成功/回退 |
|---|---|---|---|---:|---:|---|
| A | 7/8 | 6.000/8 = 0.7500 | 6.2619/8 = 0.7827 | 39.0 | 59.8 | 0/0 |
| B | 8/8 | 6.500/8 = 0.8125 | 6.8928/8 = 0.8616 | 36.9 | 62.4 | 0/0 |
| C | 8/8 | 7.500/8 = 0.9375 | 7.6309/8 = 0.9539 | 4935.2 | 8165.9 | 8/2 |
| D | 8/8 | 7.500/8 = 0.9375 | 7.6309/8 = 0.9539 | 3346.0 | 5486.3 | 6/4 |

每组2题无答案，召回指标N/A数量2；延迟使用全部10次，包括503回退。P50/P95用线性插值。C有2次、D有4次503回退，不能把C/D全部结果称为模型成功精排。每题仅一次，没有独立重复轮，P95不是可靠生产容量证明，也不能把两组模型故障耗时差异归因于BM25。

## 正确证据排名变化

| 问题 | 相关页/事实 | A | B | C | D |
|---|---|---:|---:|---:|---:|
| 2025年1月web渠道下单笔数 | p1，order_count1337 | 4（Top2未中） | 2 | 1 | 1 |
| 2025年2月mobile渠道订单量 | p4，order_count626 | 2 | 2 | 1 | 1 |
| 2025年2月web渠道支付尝试次数 | p6，payment_attempt_count1244 | 2 | 2 | 2（503回退） | 2（503回退） |

在这8条有答案题中未观察到正确证据下降；其余题保持第1名。未制造下降样本，不外推为不会退化。逐题TopK原文、完整候选名次、RRF顺序、精排排列和错误均保存在JSON。

## Citation、拒答及安全验收

- 有答案真实生成抽查：alias-jan-web，BM25+Rerank Top2包含p1；生成阶段HTTP503，**未完成**，不计错误拒答或正确率。
- 无答案真实生成抽查：no-march，检索仍返回相邻月份；Gemini返回insufficient、空引用，**1/1有效响应正确拒答**。
- 有答案补充：真实PostgreSQL+固定响应通过，原文`Period 2025-01; channel web; order_count: 1337.`、p1及当前Owner/版本引用核对成功。明确不是成功的真实模型回答。
- 真实PostgreSQL固定精排安全测试1通过：两种关键词路径跨Owner均无结果；精排中发布新审核版本后旧候选全剔除；撤销后抛TimeoutError时虽回退RRF，最终PG复核仍剔除旧候选。测试原文同一已授权PDF，未追加外发资料。
- 安全夹具首次运行因QueryVectors键未做NFKC规范化而失败（尚未进入检索）；修正夹具后复跑通过。没有放宽生产校验。
- 所有测试Owner文档已撤销，active_version=NULL；未触碰业务ODS/DWD/DIM/DWS/ADS。

## 调用、Token与范围限制

- 20次真实精排尝试：14成功、6次503回退，无应用重试。独立回答2次：1成功拒答、1次503未完成。没有为四组重复生成回答。
- 精排成功响应Token合计：{'input': 15975, 'output': 5264, 'total': 21239}；成功回答Token输入462/输出18/合计480。失败请求Token、Embedding Token未提供，费用不估算为0。
- BM25相对基线Top2多命中1/8题，精排改善部分相关证据名次，但增加数秒延迟和模型调用。六页小语料/10题仅证明接入、功能和诊断表现；不证明大库吞吐、独立Holdout泛化或中文长文检索收益。中文正文分词只有离线算法测试，PDF正文为英文，不能声称已测中文文档语义召回。
- 不修改默认配置；建议继续保留A，B/C/D仅显式实验。扩大独立标签和测量稳定性后再决定是否上线BM25或按需精排。

## 复跑与实现文件

- `src/dataquery_agent/rag/ranking.py`：BM25、独立精排及错误回退；`retrieval.py`接入；`runtime_trace.py`投影。
- `tests/evaluate_rag_ranking.py`：冻结10题四组Runner；`tests/test_rag_ranking.py`离线公式/中文/排列/故障/复核测试；`tests/test_rag_ranking_postgres.py`真实PG版本权限测试。
- 本次完整批次包含新算法、安全测试和四组实验Runner，超过常规200行提交指导；为保证同一可验收里程碑保留在一个功能提交，不拆成不可验收的算法/测试片段。

```sh
cd /private/tmp/dataquery-rag-bm25-rerank-v1
PYTHONPATH=src:tests '/Users/yxz/Documents/DataQuery Agent/.venv/bin/python' -m pytest -q
RANKING_POSTGRES_TESTS=1 PYTHONPATH=src:tests '/Users/yxz/Documents/DataQuery Agent/.venv/bin/python' -m pytest tests/test_rag_ranking_postgres.py -q
# 收费显式运行；先备份原JSON，最大20精排+2回答、1批Query Embedding。
PYTHONPATH=src:tests '/Users/yxz/Documents/DataQuery Agent/.venv/bin/python' tests/evaluate_rag_ranking.py --live
```

复跑使用新隔离Owner和新版本，但同一原件/审核文本、标签和算法参数。原始真实结果不得覆盖后冒充历史原样复现。正式配置仅在显式环境变量或构造参数启用，默认FTS和Rerank关闭。

最终回归：`pytest -q` **295通过、90环境跳过**；`RANKING_POSTGRES_TESTS=1`专项 **1通过**。离线覆盖BM25公式与中文二元组、稳定排序、精排TopN排列和TopK截断前顺序、非法排列、超时/异常回退、输入预算、关闭默认值和精排后的权限剔除。未重新执行Frontend/Chrome：本轮没有修改前端或API契约。
