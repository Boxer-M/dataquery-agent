# 业务知识检索与可信引用

自然语言问题进入 Unified Agent 后，定义或规则问题走知识检索。固定的退货联合分析先调用现有 Metric Query Service，再检索同一指标的正式定义。数字显示数据库来源，定义单独显示文档、章节或原文位置、版本。会话历史保留当时的 Chunk ID 和逐字引用，后续索引更新不会重写旧引用。

## 数据与来源

| 类型 | 范围与用途 |
|---|---|
| 正式目录 | 来自版本化 Metric Catalog。只有与当前目录条目哈希一致的索引才能用于指标定义，SQL 仍由指标服务执行。 |
| 业务规则 | 由操作者核对 HTTPS 来源、主题和生效时间后导入，只在文档所有者范围内检索。 |
| 技术文档 | 仓库白名单中的指标血缘、字段设计及查询资产设计，适用于项目实现说明。 |
| 合成测试 | 仅显式开启合成模式且属于同一所有者时可见。答复标为合成测试。 |

SQL Shed 没有正式平台退款政策。目录中有商品明细退货率，缺少正式订单退款率。退货金额是源记录金额，无法据此得出实际到账日期。文档证据不能发布指标、修改查询资产或触发工具调用。

## 索引与执行

- Markdown 和 TXT 按标题、段落及表格行分块。超大单段、超量文档、已识别凭据或提示注入文本会被拒绝。PDF 适配器调用可选 Poppler `pdftotext`，保留页码及该页提取文本的行号。当前 Mac 缺少 Poppler，测试使用固定的分页提取输出。
- 文档 ID 由所有者范围、类型和来源决定。原文版本和向量索引版本分别保存，Chunk ID 绑定位置、内容与索引版本。重新索引先完成 Embedding，再用短事务切换指针。并发旧请求不能覆盖新状态。删除操作会停用活跃指针并保留审计记录。
- pgvector 使用 768 维单位向量和精确余弦距离，叠加可解释的精确词命中奖励。小规模语料先使用精确检索。模型与向量版本不兼容时单独报告，重新导入同一原始版本可切换模型。
- Gemini Embedding 使用现有 SDK 的 `gemini-embedding-001`，区分查询与文档任务并在本地归一化。使用已有远端服务可免去安装本地模型与 Python 新依赖。[Gemini Embedding 文档](https://ai.google.dev/gemini-api/docs/embeddings)
- SQL 角色没有知识表权限。知识读者与写入角色独立。读者在只读事务中按服务端可信所有者上下文执行 RLS，且只能看到活跃版本。读者凭据只供可信服务端使用，不能交给最终用户。
- 过期和未生效文档不进入模型输入。对于同主题、同最高权威级别的不同有效规则，保守报告冲突。生成模型只选取可逐字核验的引文，程序验证引用 ID、引文内容及定位。逐字验证能约束事实来源，语义相关性仍需业务评测。

## 本地复现

在容器需要重新创建时使用 pgvector 镜像覆盖配置。沿用原 Compose 项目和数据卷。初始化脚本需要本地 PostgreSQL 管理员，两个知识角色的凭据写入被忽略的 `.env.knowledge*`，权限为 `600`。

```bash
docker compose -f compose.yaml -f compose.rag.yaml up -d db
export PYTHONPATH=src
.venv/bin/python scripts/provision_knowledge.py
.venv/bin/python tests/evaluate_business_knowledge.py --mode fixed
.venv/bin/python tests/evaluate_business_knowledge.py --mode live-synthetic
.venv/bin/python -m dataquery_agent.rag.cli list --public
.venv/bin/python -m dataquery_agent.rag.cli chunks "$DOCUMENT_ID" --public
```

经确认可向 Gemini 发送文档后，使用 `seed --allow-external --technical-version <git-commit>` 导入退货目录和白名单技术文档，`--all-metrics` 扩展为整个目录。使用 `index <path> --type <type> --title <title> --source <URI> --version <version> --allow-external` 导入已核准文档。更新时指定新版本。业务规则还需要主题和生效日期。Embedding 会发送文档片段或用户问题，生成阶段会发送经授权检索的片段。请先确认文档和问题可发送至该服务。

使用 `search <question>` 查看候选、得分和定位；`ask <question>` 可运行知识及联合分析；`delete <document_id>` 停用文档。使用会话 CLI 的 `send` 与 `history` 可追踪历史引用。合成演示的开关仅在知识 CLI 和专门评测中显式启用。

[初始评测报告](../../reports/warehouse/business_knowledge_v1.json)记录了固定向量、合成文档和独立 ODS 对账。后续授权验收将正式目录 38 个指标片段与三份白名单技术文档中的 4 个指定片段用真实 Gemini Embedding 编入 pgvector。文件与片段哈希、检索候选、引用及权限隔离结果见[正式索引报告](../../reports/warehouse/repository_embeddings_v1.json)。

复现授权验收时，先运行 `PYTHONPATH=src .venv/bin/python tests/evaluate_repository_embeddings.py` 生成预检清单。复核来源、行号和片段数量后，使用 `--approved-manifest <manifest.json>` 执行。清单与实际请求内容的哈希必须一致。验收只调用 Embedding，引用在本地核对。
