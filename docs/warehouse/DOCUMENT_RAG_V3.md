# Document RAG v3

Knowledge Agent 通过现有文档问答工具检索已审核、已发布的 PDF。普通业务规则和技术文档问题可以检索同一 Owner 有权使用的 PDF 与现有文本知识。显式 PDF 问题限定到 PDF 检索范围。正式指标定义继续由 Metric Catalog 精确查找处理。

## 运行与引用

先按 [Document RAG v2 审核与发布说明](DOCUMENT_RAG_V2.md) 配置本地知识库，提取、审核并发布获授权的 PDF。随后通过现有问答会话提问。例如可使用 `PDF 中带有 Scanned verification page 标题的页面，请逐字摘录该页的 Order count 那一整行。` 复现合成样例。

检索复用现有 Gemini Embedding 和 pgvector 余弦排序。PDF 候选须匹配 Owner、活跃版本和审核清单中的原件 SHA-256。模型接收允许使用的 Chunk，返回 Chunk ID 和逐字摘录。服务用检索到的记录生成页码、行号、来源 Hash 和提取方式，随后再次通过 RLS 检查版本与摘录。若文档在回答生成期间失效，相关引用会移除。保留的摘录在答案中标记为“原文摘录”。

API 中 `page_number` 为实际 PDF 页码，`line_start`、`line_end` 为对应的审核文本行范围。它们定位整个 Chunk，具体摘录仍用原文逐字定位。OCR 修订保留审核文本行号和实际页码。引用含 Owner 访问范围，具体 Owner 由会话凭据及数据库 RLS 约束。历史是当时执行的审计记录，旧版快照不会作为新回答的候选。

文档问答的 Plan ID、Task ID、Attempt ID 关联到现有 Knowledge Agent 工具事件。检索作用域、PDF 检索开关、引用及证据状态随查询写入 History。

## 验收

| 真实 Gemini 问答 | 页码与提取方式 | 验证通过的原文 |
| --- | --- | --- |
| 原生文本页 | 第 1 页，`native_text`，审核文本第 1–3 行 | `Order count: 24` |
| 图片扫描页 | 第 2 页，`ocr`，审核文本第 1–3 行 | `Order count: 48` |

授权合成样例先实际审核，再将 2 个 Chunk 发布到 PostgreSQL。两个回答的 API 引用和 Trace 与持久化 History 一致，完成验收后撤下样例。Gemini Embedding 执行 3 个调用批次，文档批次 2,372 ms，两次查询分别为 2,184、2,100 ms。响应未提供 Embedding Token 用量。Gemini 回答模型实际调用 2 次，分别为 3,599、3,625 ms，报告中的总 Token 为 422、427。[实际引用、History Trace 与调用记录](../../reports/warehouse/document_rag_v3.json)

固定模型提案测试单独执行，使用实际 PostgreSQL 和 OCR，验证未审核内容、跨 Owner 访问、伪造引文、版本切换、撤下、Catalog 优先和 History。用模型执行期间切换版本的场景验证返回前的二次检查。Python 全量回归 194 项通过，1 项可选 Gemini 测试跳过。真实 Gemini 验收见上表。
