# Milvus 索引就绪 v1

基线 `54f3e90`（PR #71）；分支 `codex/milvus-index-readiness-v1`。

## 目标与调用链

避免显式同步中部分批次失败后，Milvus 返回看似正常但不完整的候选集。

`显式 sync → PostgreSQL syncing → Milvus 50 条/批 upsert → 全量预期 ID/元数据读回 → 锁定 PG 文档并验证版本/Chunk 集 → ready`。

`Retriever → 当前可见范围就绪检查 → Milvus 或 pgvector → 原 PostgreSQL FTS → 原 RRF → 新 PG 快照权限/版本复核 → 原引用校验`。

每个文档记录一个当前同步目标，包含文档/索引/Embedding 版本、排序后的全部预期 Chunk ID、attempt_id 和状态。跨 Owner 仍由知识文档 RLS 决定可见性；查询角色只能 SELECT。就绪写入由已有 ingestion admin 完成，不授予 reader 写权限。同步仍先用 reader 读取可复制向量，admin 不用于扩大导入范围。

## 状态与并发

- 开始同步先提交 syncing；异常留下 failed（进程硬退出留下 syncing），两者均不能走 Milvus。
- 按文档 ID、chunk_index 的确定顺序写入，每批 50。重复同步仍 upsert 相同 Chunk 主键。
- 逐批 Strong 查询全部预期 ID，验证无缺项/重复及版本 token、Owner、Embedding 元数据；未通过不得 ready。
- 完成时按稳定文档顺序 `FOR UPDATE`，检查未撤销、active_version/active_index 未变，以及 PG Chunk 集仍与目标完全相同。已有发布/撤销更新同一文档行，最终检查与 ready 写入在同一个 PG 事务内。
- attempt_id 条件更新阻止旧同步覆盖较新的尝试；跨多个文档的完成更新整体提交或回滚。
- 请求对可见范围中的每个文档比较 ready、版本和完整 Chunk 集。任一不符，向量通道整体回退，原因 `milvus_index_not_ready`。未安装迁移也安全回退。
- ready 索引上的正常空结果不回退；无可见文档保持空检索语义。版本换新未同步时现在回退，替代 v1 的正常空结果行为。
- 不缓存 readiness；最终证据仍经过原 PG 新快照及 PDF 引用复核。有效性保证以复核时点为准，不提供覆盖模型调用的跨系统原子性。

## 安装与运行

已有本地角色配置时可执行项目原初始化入口（新增迁移被纳入其中）：

```sh
PYTHONPATH=src .venv/bin/python scripts/provision_knowledge.py
PYTHONPATH=src .venv/bin/python scripts/milvus_vectors.py sync
CI_NO_EXTERNAL_MODELS=1 .venv/bin/python -m pytest tests/test_milvus_backend.py tests/test_milvus_readiness.py -q
PYTHONPATH=src:tests .venv/bin/python tests/evaluate_milvus_readiness.py \
  --local-services --out /tmp/milvus-readiness-live.json
CI_NO_EXTERNAL_MODELS=1 KNOWLEDGE_POSTGRES_TESTS=1 .venv/bin/python -m pytest tests/test_business_knowledge.py -q
CI_NO_EXTERNAL_MODELS=1 .venv/bin/python -m pytest -q
```

仅迁移 007 亦可由本地数据库管理员执行；不修改 ODS/DWD/DIM/DWS/ADS。显式同步还需现有 `.env.knowledge-admin` 作为 readiness 写入身份；无写入配置时拒绝同步，不假报完成。

## 真实验收

实际时间以 JSON 的 started_at 为准。本轮使用真实本地 PostgreSQL 和 Milvus，不调用外部模型。

取之前已授权合成 PDF 的原生页和扫描页，制作两个本地派生 PDF：40 个原生页，以及 11 个原生页加 1 个扫描页。每个文件仍在生产的 40 页限制内。经现有提取、显式审核签名及发布，得到 **52 个 Chunk**。页面文本、标题和 heading 输入必须与历史真实 Gemini 文档向量完全一致，否则复用器拒绝。

检索使用已有扫描页文档向量作为 ANN 探针，问题为 `Order count: 48`。这是索引完整性与引用功能测试，**不是新 Query Embedding、真实 LLM 回答或检索质量 Benchmark**。固定归因提案仅用于原引用校验。唯一扫描证据位于第二个 PDF 第 12 页，排序后恰在第二批。

| 步骤 | 实际结果 |
|---|---|
| 首批写入 | Strong 读回确认 50 条，目标证据尚未写入 |
| 第二批注入失败 | 调用批次为 50 + 2；第二批在写入前抛异常，状态 failed |
| 同步中与失败后查询 | requested=milvus、actual=pgvector、reason=milvus_index_not_ready；第12页 `Order count: 48` 引用有效 |
| 完整重试 | 再次写入 50 + 2；读回 52 个唯一 ID，状态 ready |
| 重试后同题 | actual=milvus、fallback_reason=null，引用与回退阶段是同一 Chunk、第12页、相同 Hash |
| 同步中发布新版本 | 旧同步返回 milvus_sync_target_changed，无法 ready；请求回退且只引用新版本 |
| 同步中撤销文档 | 旧同步无法 ready，最终证据不含被撤销文档 |

测试结束撤销两个派生文档，删除测试 Milvus 向量；历史审核和失败元数据保留，不能作为活动证据返回。首次脚本运行因固定归因字段误写而失败，修正测试脚本后重跑通过，未据此修改业务逻辑，报告保留该尝试说明。

新增离线专项与原 Milvus 专项合计 **11 passed**。全量离线 **264 passed、88 skipped**；原有真实 PostgreSQL RAG 专项 **12 passed**。生成模型、外部 Embedding 调用均为 **0**。不重复真实断连测试。

## 文件与边界

- `sql/agent_meta/007_milvus_readiness.sql`：版本化迁移、RLS 和权限。
- `rag/milvus_readiness.py`：就绪读取、尝试标识、版本锁定及状态更新。
- `rag/milvus.py`：同步生命周期、完整集合读回。
- `rag/vector_candidates.py`：未就绪回退。
- `tests/test_milvus_readiness.py`、`tests/evaluate_milvus_readiness.py`：离线故障及显式真实服务验收。
- `reports/warehouse/milvus_index_readiness_v1.json`：逐阶段状态、后端 Trace、引用和源码 Hash。

没有引入依赖或后台同步器。Milvus 集合被外部删除、已就绪向量被绕过同步器手工删除，不会自动改变 PG readiness；需重新同步或运维修复。读回不是 PG/Milvus 分布式事务，也不消除最终权限复核之后的撤销时间窗口。范围上限沿用 10000 Chunk；本轮只证明 52 Chunk、两批的正确性，不作性能声明。

本批包含迁移、同步安全边界和真实故障报告，超过通常 200 行约定，为一个完整验收里程碑的明确例外。下一步仅审查本地提交，再由用户创建和合并 PR。
