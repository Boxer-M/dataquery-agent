# Milvus 向量候选后端 v1

基于 `origin/main=1d5371c`（Neo4j PR #70 已合并），分支 `codex/milvus-vector-backend-v1`。真实模型、Milvus、PostgreSQL 对照于 **2026-11-20** 执行；2026-11-23 整理交付，未将历史结果算作新模型调用。

## 接口与调用链

`Retriever → 向量候选接口 → pgvector 或 Milvus → PostgreSQL 权限与有效版本复核 → 原全文通道 → 原 RRF → 原引用校验`。

- `src/dataquery_agent/rag/vector_candidates.py`：两种候选提供者，共用 `search(pg, scored, params)` 返回当前原文行及后端 Trace。默认 pgvector 保留原排序与阈值。
- `src/dataquery_agent/rag/milvus.py`：Python 标准库 REST 客户端、固定集合 Schema、已有向量显式同步。不增加 pymilvus、gRPC 等依赖。
- `src/dataquery_agent/rag/retrieval.py`：配置选择、独立关键词通道、最终新 PostgreSQL 快照复核及 RRF。
- `src/dataquery_agent/runtime_trace.py`：投影可选后端信息，兼容没有新字段的旧 Trace。
- `scripts/milvus_vectors.py`：初始化本地私密配置、按当前 Owner 同步既有向量。不生成 Embedding。

沿用 `gemini-embedding-001`、768 维、原单位归一化契约 `gemini-embedding-001-768-unit-retrieval-v1`、余弦相似度阈值 0.43。本次 Milvus 使用 FLAT/COSINE；导入检查维度、主键与索引口径。关键词仍是 PostgreSQL simple FTS + 中文二元组，RRF 常数 60，候选池上限为 `min(3*K,30)`。没有新增 BM25 或 Reranker。

Milvus 保存 Chunk ID、向量、Owner 哈希、文档/索引版本和 Embedding 模型版本，不保存标题、原文、PDF 或审核密钥。Chunk ID 为稳定主键，重复同步使用 upsert，逻辑记录不重复。查询使用 Strong 一致性；物理日志中的 upsert 历史不能当作有效记录数。

## 权限与版本权威

1. 在原 KnowledgeStore 只读角色、Owner RLS、模型/版本、PDF 审核与检索范围过滤下，PostgreSQL 给出允许的文档版本 token。不存在跨 Owner 的“全库取前 K 再过滤”。
2. Milvus 在 ANN 前应用允许的 Owner 和版本 token、模型版本过滤。
3. 返回的 ID 再经同一授权条件复核，原文、页码和余弦分数由 PostgreSQL 提供。只对候选重算同口径分数，不在正常 Milvus 路径执行 pgvector 全库候选排序。
4. 外部调用结束后，在新的 PostgreSQL 快照中再次复核向量与关键词候选，过滤中途撤销/换版的 Chunk，然后进行 RRF。
5. 继续沿用回答后的当前 PDF 引用复核。有效性以各次复核时点为准，未建立覆盖外部模型调用全过程的长锁或跨系统原子事务。

显式导入只允许原 `sqlshed_knowledge_reader`，拒绝使用绕过 RLS 的 admin 对象导出。已有知识版本、审核签名和 PostgreSQL 表结构不变。仅复制当前可见的已发布向量；默认每次最多 10000 Chunk，显式文档范围最多 1000 项，单次 Milvus 查询版本范围最多 2000 项。

## 配置和复跑

真实运行使用 Milvus v2.6.26 单容器开发实例，内嵌 etcd、本地存储；镜像已固定实际拉取的 digest，容器限制 3 GiB / 2 CPU。只暴露 `127.0.0.1:19530`，不暴露管理端口、etcd 或 MinIO。开启认证，随机 root 密码仅在被忽略、权限 600 的 `.env.milvus`。这是本地单人服务，不宣称完整多租户 Milvus RBAC；权限权威始终在 PostgreSQL。

```sh
# 仅首次执行；已有配置时拒绝覆盖，不打印密码。
PYTHONPATH=src .venv/bin/python scripts/milvus_vectors.py init-config
docker compose --env-file .env.milvus -f compose.milvus.yaml up -d
# 等容器 healthy；同步当前服务端 Owner 可见的已有 Gemini 向量。
PYTHONPATH=src .venv/bin/python scripts/milvus_vectors.py sync
# 可显式缩小导入范围：sync --document-id DOCUMENT_SHA256_ID
```

配置优先级为构造参数 `backend` → 环境变量 `RAG_VECTOR_BACKEND` → `.env.milvus` 中同名项。默认 pgvector。在服务端设置 `RAG_VECTOR_BACKEND=milvus` 并重新启动 API 即可切换；`Retriever(..., backend='milvus', mode='vector'或'hybrid')` 可逐次对照，不改前端或指标服务。

```sh
CI_NO_EXTERNAL_MODELS=1 .venv/bin/python -m pytest tests/test_milvus_backend.py -q
# 明确调用收费模型：5 条查询向量一次生成、1 条真实回答；--fault 仅停/启本轮 Milvus。
PYTHONPATH=src:tests .venv/bin/python tests/evaluate_milvus.py --live --fault --out /tmp/milvus-live.json
CI_NO_EXTERNAL_MODELS=1 KNOWLEDGE_POSTGRES_TESTS=1 .venv/bin/python -m pytest tests/test_business_knowledge.py -q
CI_NO_EXTERNAL_MODELS=1 .venv/bin/python -m pytest -q
# 停止本轮服务但保留向量数据，不加 --volumes。
docker compose --env-file .env.milvus -f compose.milvus.yaml down
```

全量导入不自动发生在 API 启动或文档发布过程中。文档发布/更新后需显式同步；当前 Milvus 没有对应版本时正常空结果不会触发回退。已撤销/旧版本的向量可以暂留，但不能越过 PostgreSQL 门禁；正式存量清理策略未在本轮实现。

## 真实同题对照

使用已授权合成 PDF，原件 Hash `8716be31f972a13bf13b2f2d1daa84913859c6d327e4d501aa8a7df490400c28`。从历史已审核记录中，仅按这个原件 Hash 与完全相同的输入文本复用真实 Gemini 文档向量，重新走当前测试 Owner 的审核发布流程。没有复活原已撤销文档、访问其他实际业务 PDF 或向模型上传整个仓库。

活动语料只有 **2 个页 Chunk**。冻结 `tests/milvus_cases_v1.json` 的 4 个问题：3 个有答案、1 个无答案；纯向量和混合模式各比较两个后端，共 **16 条记录**。所有组合共用同一批文档/查询向量，Top K=2、关键词通道和 RRF 配置一致。

| 组合 | Recall@2 | MRR@2 | 原文及页码支持 | N/A |
|---|---:|---:|---:|---:|
| pgvector 纯向量 | 3/3 | 3.0/3 = 1.0 | 3/3 | 1 |
| Milvus 纯向量 | 3/3 | 3.0/3 = 1.0 | 3/3 | 1 |
| pgvector + FTS/RRF | 3/3 | 3.0/3 = 1.0 | 3/3 | 1 |
| Milvus + FTS/RRF | 3/3 | 3.0/3 = 1.0 | 3/3 | 1 |

引用支持使用固定归因提案、标签页码和逐字原文校验，**不是 16 次真实模型回答准确率**。无答案问题四种组合都召回了两页不相关内容；没有把它计作成功，也没有测试真实模型对该负例的拒答。原有阈值与检索策略保持不变。

逐题检索耗时（ms，`retrieval_trace.elapsed_ms`；复用同题查询向量，不含新 Embedding/生成调用）：

| 问题/目标 | pgvector 向量 | Milvus 向量 | pgvector 混合 | Milvus 混合 | 三个正例的排名 |
|---|---:|---:|---:|---:|---|
| Order count: 48 / 第2页 | 25 | 38 | 29 | 92 | 四种组合均第1名 |
| 扫描核验页订单数改写 / 第2页 | 43 | 28 | 17 | 30 | 四种组合均第1名 |
| Native verification page 原文 / 第1页 | 19 | 25 | 21 | 29 | 四种组合均第1名 |
| 实际退款到账银行卡 / 无答案 | 15 | 27 | 19 | 28 | N/A，不计分 |

每题只运行一次，顺序固定，冷启动和连接开销会影响耗时。两页语料且 K=2，不能据此证明召回、延迟或吞吐提升。报告还保留包含引用复核的整行评测耗时，不能与纯检索耗时混用。

## 一条真实请求

输入：`PDF 中带有 Scanned verification page 标题的页面，请逐字摘录该页的 Order count 那一整行。`

实际聊天入口沿用默认 K=6（不同于上述统一对照的 K=2）：

1. Milvus 返回两个候选，扫描页 Chunk ID 为 `d58a39eb6d5b39ef31f962eb4d85fca35dfd89ad9fdc2e8137e4d38a2eb40638`。
2. PostgreSQL 初次与最终复核均无拒绝；原文及当前版本来自 PostgreSQL。
3. 原全文通道与向量通道经 RRF 合并，扫描页排在首位。
4. 真实 `gemini-3.5-flash-lite` 摘录 `Order count: 48`，通过当前 PDF 第2页、原文和版本校验。
5. API completed，Query History 与当前 Trace 一致；公开 Trace 保留 requested/actual=milvus 和 fallback_reason=null。

Query Embedding 实际 1 批、5 个输入、1651 ms，Token API 未返回。文档新 Embedding 调用 0，使用既有真实向量。真实回答调用 1 次，输入 321、输出 104、合计 425 Token，2252 ms；无失败重试。其他 16 条支持检查未调用回答模型。没有可靠价格依据，不估算费用。

## 版本、权限和故障验收

- 两次导入同版本后，按稳定主键查询均为 **2 条逻辑记录**，没有重复。
- 跨 Owner：两个后端、两种模式均无最终证据。
- 文档审核版本更新：正文保持不变以复用向量，新版本尚未同步时 Milvus 返回正常空结果，actual=milvus、无回退；旧两条向量仍物理存在。同步后两后端仅返回当前新版本。
- 真实停止 `dataquery-milvus-v1-milvus-1` 后，请求标记 requested=milvus、actual=pgvector、fallback_reason=milvus_connection_error，并返回仍通过 PostgreSQL 校验的证据。
- 恢复容器并校验/加载集合后，同题 actual=milvus；没有重新生成向量。
- 在真实 Milvus 已返回候选后撤销文档：旧事务可能仍看得到候选，但新 PostgreSQL 快照拒绝了两条 ID。最终证据和引用为空，回答模型调用 0；此时 Milvus 里仍有 4 条旧/新版本向量。
- 验收结束撤销合成文档，删除本轮测试向量与随机 Owner 的会话记录，不长期留下有效合成知识。报告保留脱敏证据。
- 离线补测超时回退、正常空结果不回退、候选伪造后的 PG 拒绝、非法配置/外部地址拒绝，以及旧 Trace 缺少后端字段时仍可读取。

## 回归、交付与边界

- 新后端离线专项 4 passed；原 RAG/权限/FTS 专项 12 passed。
- Python 全量离线 257 passed、88 skipped；包含外部依赖与显式开关的测试仍按原条件跳过。
- 相关真实 PostgreSQL/PDF/会话/DAG/HITL/恢复回归 66 passed。
- 前端原 11 项测试、TypeScript 和生产构建通过；既有大 chunk 提醒不变，未新增 Chrome 验收。
- 收尾仅补加导入角色拒绝与旧 Trace 可选字段保护，成功 Reader/检索路径不变；实际 Reader 零范围同步及离线回归通过，未为这些保护重复调用收费模型。

详细逐题与故障 Trace：`reports/warehouse/milvus_vector_backend_v1.json`。字段、公式、SQL安全、Catalog精确路由及文档审核口径未改变；默认 CI 不需要 Milvus、Gemini Key 或外部网络。未创建新的向量权威数据源或后台同步框架。

本轮超过通常 200 行，是同一可验收批次所需的后端、部署、同题实验和完整脱敏报告。已知边界是小语料、单机 FLAT、显式同步、无生产 RBAC/HA；不宣称生产可扩展性或拒答质量已被验证。下一步仅审查本地提交并手动创建 PR。

接口依据：[Milvus REST Search](https://milvus.io/api-reference/restful/v2.6.x/v2/Vector%20%28v2%29/Search.md)、[Upsert](https://milvus.io/docs/v2.6.x/upsert-entities.md)、[官方单容器参考](https://github.com/milvus-io/milvus/blob/v2.6.26/scripts/standalone_embed.sh)。没有执行远程脚本。
