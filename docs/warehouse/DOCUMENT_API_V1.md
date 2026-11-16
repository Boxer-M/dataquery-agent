# Document API v1

FastAPI 在环回地址 `127.0.0.1:8000` 提供 PDF 导入、提取预览、审核和发布。直连使用现有 Bearer 令牌。Vite 同源代理使用服务端保存的令牌，浏览器通过 `X-DataQuery-Client: workspace` 调用允许的路线。写请求还需匹配工作台 Origin。所有操作从服务端身份派生 Owner。

## HTTP 契约

| 方法与路径 | 请求 | 成功响应 |
| --- | --- | --- |
| `POST /api/documents` | 原始 `application/pdf` 字节 | `201`，`document_id`、导入 `version`、`file_sha256`、`extraction_version`、状态和页摘要 |
| `POST /api/documents/{id}/versions` | 新 PDF 字节 | `201`，同一 `document_id`。文件变化后返回新的导入版本与 `unreviewed` 状态 |
| `GET /api/documents/{id}` | — | `200`，当前导入和审核状态、页摘要及已有发布回执 |
| `GET /api/documents/{id}/pages/{page}` | 从 1 开始的页码 | `200`，另含 `page` 对象。包括 `text`、`method`、`status`、`error`、可用的 OCR `blocks` 和 `reviewed_text` |
| `POST /api/documents/{id}/review` | JSON，`version`、`title`、`basis`、可选的 `corrections` | `200`，`reviewed` 与可用于发布的 `review_version` |
| `POST /api/documents/{id}/publish` | JSON，`version`、`review_version`、`external_embedding_approved: true` | `200`，`published` 与 `publication`。重复发布返回同一知识版本，`indexed: false` |

页状态沿用提取器的 `ok`、`no_text`、`partial`、`error`。OCR 坐标如有提供，使用归一化后的裁剪框坐标系。修订后的文本以独立字段返回，原始 OCR 位置仅对应提取结果。不可访问的 ID 返回 `404`。过期导入或审核版本返回 `409`。大小超限返回 `413`，类型错误返回 `415`，非法 PDF 或参数返回 `422`。所有错误的 JSON 形状为 `{"error":{"code":"…","message":"…"}}`。

## 本地请求示例

先用现有的 [审核签名配置](DOCUMENT_RAG_V2.md) 初始化环境。以下命令假定令牌通过本地安全配置注入 shell。发布前只选择已获准发送审核文本的文档。

```sh
base=http://127.0.0.1:8000/api/documents
curl -sS "$base" -H "Authorization: Bearer $DATAQUERY_HTTP_TOKEN" \
  -H 'Content-Type: application/pdf' --data-binary @output/pdf/document-extraction-sample.pdf

# 从上一响应取得 id 和 version
curl -sS "$base/$id/pages/2" -H "Authorization: Bearer $DATAQUERY_HTTP_TOKEN"
curl -sS "$base/$id/review" -H "Authorization: Bearer $DATAQUERY_HTTP_TOKEN" \
  -H 'Content-Type: application/json' --data-binary @review.json
curl -sS "$base/$id/publish" -H "Authorization: Bearer $DATAQUERY_HTTP_TOKEN" \
  -H 'Content-Type: application/json' --data-binary @publish.json
```

审核请求中 `corrections["2"]` 是第 2 页人工核对后的**完整文本**。发布请求使用审核响应返回的版本。

```json
{"version":"导入响应的 UUID","title":"Synthetic PDF verification","basis":"人工比对合成原件","corrections":{}}
```

```json
{"version":"导入响应的 UUID","review_version":"审核返回的 pdf-…","external_embedding_approved":true}
```

真实导入响应节选如下。

```json
{"state":"unreviewed","extraction_version":"pdf-extraction-v1","pages":[{"page":1,"method":"native_text","status":"ok"},{"page":2,"method":"ocr","status":"ok"},{"page":3,"method":"ocr","status":"no_text"}]}
```

## 安全与版本

暂存区 SQLite 保存在 Git 忽略的 `data/staging/document-api/`。目录和文件只允许本机当前用户访问。上传字节、原始提取、所有修订以及审核签名留在该暂存区。HTTP 仅返回业务字段与版本标识。原件、文件路径、Owner 散列、审核签名及密钥没有对应的下载接口。

上传上限 10 MiB、40 页。复用 pypdf 文件结构校验、Apple Vision 光栅限制，以及隔离提取进程的 CPU 与墙钟限制。单页预览有响应大小上限，审核 JSON 上限 512 KiB，暂存内容配额 128 MiB。并行写请求会被拒绝，发布期间拒绝其他文档写操作。只有显式发布会将经审核的标题和文本片段发送给 Gemini Embedding。pgvector 沿用现有的 Owner 权限、幂等索引和活跃版本事务。知识来源登记为私有 PDF。正式指标定义仍从 Catalog 取得。

新上传或新审核后，旧审核回执无法再次发布。已在知识库生效的版本会继续服务，直到新版本通过审核并成功发布。若索引已提交而 API 在记录回执前中断，重试会找回同一知识版本。预览展示当前暂存版本和本 API 记录的发布回执。旧版本保留在本地审计记录中。v1 提供当前版本预览与发布，暂存配额内的版本留存由本机操作者管理。

## 实测

两个合成 PDF 经环回 HTTP 导入，覆盖原生文本、扫描页和空白页。审核修订后使用真实 Gemini Embedding 和 PostgreSQL 发布。重复发布返回相同版本且 `indexed: false`。通过 `/api/chat`，Knowledge Agent 引用当前发布版本第 2 页的 `Order count: 48`，来源 Hash 与原始上传文件一致。文档索引与查询共两批 Embedding 调用。测试文档随后撤下。

权限回归验证了异 Owner 的预览、审核、替换与发布均为 `404`，伪造签名、过期回执和未经审核的发布失败。注入索引超时时，暂存记录仍为 `reviewed`，PostgreSQL 中没有有效文档。边界测试覆盖了体积超限、伪 PDF、JavaScript 和超页数输入。

[真实 HTTP 发布与引用报告](../../reports/warehouse/document_api_v1.json)

实际 Vite 代理通过 `127.0.0.1` 和 `localhost` 两个工作台来源验证。两边提交 PDF 均为 `201`、预览 OCR 页均为 `200`、外部 Origin 均为 `403`。代理只转发请求，令牌由服务端添加。
