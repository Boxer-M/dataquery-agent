# Document RAG v2

PDF 提取、审核和发布分别由本地 CLI 执行。审核者逐页比对原件，必要时提供修订文本并记录依据。发布端用受信本机密钥验证审核回执，并重新核对 Owner 和原件 SHA-256。仅修改提取 JSON 的审核状态、修订文本或页码无法通过验证。

## CLI 复现

在已配置 PostgreSQL、Gemini 和本地 Owner 的工作区应用迁移。沿用 `sqlshed_knowledge_admin` 和 `sqlshed_knowledge_reader`，以及既有的 pgvector 768 维列、活跃版本指针和 RLS。审核员和发布员是拥有相应本机密钥及凭据的可信操作者，当前 CLI 的 Owner 来自 `.env.agent-meta`。

```sh
export PYTHONPATH=src
.venv/bin/python scripts/provision_knowledge.py
.venv/bin/python -m dataquery_agent.rag.pdf_cli init-key
```

`init-key` 只需执行一次。审核密钥为本地 `.document-review-key`，权限 0600。审核回执也以 0600 写入新文件。如下生成绑定到本机 Owner 的合成样例提取清单。

```sh
umask 077
.venv/bin/python - <<'PY' > extract.json
import json
from dotenv import dotenv_values
from dataquery_agent.document_ingestion import extract_pdf
owner = dotenv_values(".env.agent-meta")["AGENT_OWNER_TOKEN"]
print(json.dumps(extract_pdf("output/pdf/document-extraction-sample.pdf", owner=owner), ensure_ascii=False))
PY

.venv/bin/python -m dataquery_agent.rag.pdf_cli review \
  extract.json output/pdf/document-extraction-sample.pdf receipt.json \
  --slug local-synthetic-pdf --title "Synthetic PDF verification" \
  --basis "Compared both text pages against the synthetic original"
.venv/bin/python -m dataquery_agent.rag.pdf_cli publish \
  receipt.json output/pdf/document-extraction-sample.pdf --allow-external
.venv/bin/python -m dataquery_agent.rag.pdf_cli search \
  "Scanned verification page Order count 48" --allow-external
```

需要修订时，在 `review` 命令传入 `--corrections`，文件内容为以页码字符串为键的 JSON，对应值是该页核对后的完整文本。回执授权以本机 HMAC 签名确认，审核依据随版本保存。发布和搜索分别经 `--allow-external` 明确授权向 Gemini 发送审核后的段落或检索问题。原 PDF、边界框及审核记录均留在本地。私有清单和回执应保存在受保护的本地目录。

## 版本与引用

- 相同 Owner 和来源 slug 保持稳定的知识库 `document_id`。改变原件或修订文本会生成不同的文档版本。版本记录关联原件 SHA-256、提取清单 ID、提取器版本、审核依据及逐页提取和审核文本哈希。原文与修订文本分别保存在本地提取清单和审核回执，发布后的文本保存在 Chunk。
- 每个 Chunk 复用 `Passage`。例如扫描页 `page:2;lines:1-3` 指向**审核文本行号**和实际 PDF 页码。修订后不继承 OCR 坐标，空白页不生成 Chunk。存在提取失败或部分提取页的整份文件需重新提取后再审核。
- 重复发布同一有效版本不会重建 Chunk 或调用 Embedding。已发布的旧版本保留以供审核追溯，RLS 只返回 Owner 可见的当前版本。专用 PDF 检索返回来源和版本记录，并使用现有逐字引用校验。PDF 归类为 Owner 私有的普通证据，权威等级为 1，现有 Catalog 精确查找仍查询正式目录。
- 当前审核依赖相同工具环境能再次提取出同一文本，版本变化导致提取清单不一致时须重新提取与审核。首版审核权限以本机受信用户、Owner 凭据和签名密钥为边界。

## 验收结果

- 本机 pgvector 0.8.7 中实际写入两个 Chunk，向量维度均为 768。第 1 页为原生文本，第 2 页为 OCR，第 3 页空白。真实查询命中第 2 页 `page:2;lines:1-3`，余弦分数约 0.8413，引用原文 `Order count: 48` 通过校验。另一 Owner 可见 Chunk 为 0，样例验收后撤下。
- Gemini Embedding 成功返回两个批次。索引批次 `RETRIEVAL_DOCUMENT` 处理 2 段，耗时 2,916 ms。查询批次 `RETRIEVAL_QUERY` 处理 1 条，耗时 2,925 ms。此次响应未提供 Token 用量。仅使用项目合成样例和对应查询。
- 真实 PostgreSQL 生命周期测试使用固定测试向量验证审核文本修订、原件替换、相同版本幂等及历史版本恢复拒绝。全量 Python 回归 193 项通过，1 项可选 Gemini 测试跳过。上面的真实 Embedding 运行单独通过 CLI 采集。

[真实索引、向量检索和带页码的 Chunk 报告](../../reports/warehouse/document_rag_v2.json)
