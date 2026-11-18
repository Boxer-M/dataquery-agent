# XLSX Artifact 持久化与授权下载（C2.2）

基线为 C2.1 已合并的 main `e8d5b43`。只增加后端显式导出与下载，不接入前端、Vite代理或自然语言导出路由。

## 源结果与身份

原 Query History 只保留 result_summary，普通多指标结果没有完整数值，中文名称也未保存，不能从旧摘要安全重建报表。现在在原 ConversationManager 完成查询并写入同一 Query History JSON 的链路中，增加私有 `export_snapshot`：最多6项已校验、完整、单窗口无分组汇总值及原 Catalog中文名。快照不保存模型Prompt、业务明细或文档全文，也不增加PostgreSQL表或额外历史写入事务。

普通单指标Fast Path的已校验标量也只在此边界适配为C2.1汇总形状，不触发新规划或SQL。比较、分组、partial、截断、NULL等不符合C2.1要求的结果不生成可用快照。旧记录未保存快照时返回409，不重新查询、不解析回答正文、不覆盖旧历史。

`query_id` 是本项目 canonical Run关联标识；Artifact记录源query_id，沿原历史读取回溯Plan、Task及Trace，不增加Run或Task状态机。名称和数值取查询完成时的快照，导出时不使用后续Catalog名称覆盖旧结果。

身份继续来自FastAPI现有Bearer及服务端 manager_factory。导出先通过 `manager.get_query(owner, query_id)` 的PostgreSQL Owner JOIN校验；下载先查Artifact Owner，再重新检查源查询的当前可访问性。源查询被清理或不再可访问时，已有Artifact也拒绝下载。客户端不能指定Owner、文件路径、文件名或指标结果。

## 后端API

| 方法 | 路径 | 成功响应 |
| --- | --- | --- |
| POST | /api/queries/{query_id}/artifacts/xlsx | 201，Artifact元数据；请求JSON为 `{}` |
| GET | /api/artifacts/{artifact_id}/download | 200，XLSX二进制及附件文件名 |

POST返回字段为 artifact_id、query_id、filename、created_at、sha256、byte_size，不返回Owner或内容。调用C2.1 `generate_metric_xlsx` 后才保存。下载使用 `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`、`Content-Disposition: attachment`、`Cache-Control: private, no-store`及nosniff。

401表示缺少有效本地令牌；不可访问/未知Query、Artifact或丢失内容统一404；旧历史、不完整或精度不支持为409；非法字段/UUID为422；存储失败503，容量配额507，过大内容413。复用原结构化错误格式，不泄露异常堆栈和私有路径。

示例（令牌只存在当前可信本地终端环境，不写进命令文件或浏览器）：

```sh
curl -X POST "http://127.0.0.1:8000/api/queries/$QUERY_ID/artifacts/xlsx" \
  -H "Authorization: Bearer $DATAQUERY_HTTP_TOKEN" -H 'Content-Type: application/json' -d '{}'
curl "http://127.0.0.1:8000/api/artifacts/$ARTIFACT_ID/download" \
  -H "Authorization: Bearer $DATAQUERY_HTTP_TOKEN" -o report.xlsx
```

仍是环回、本地开发身份环境，不宣称已实现多人登录。A/B隔离测试通过可信服务端身份上下文切换执行，HTTP客户端本身不能切换Owner。

## 持久化与原子性

复用本地PDF暂存采用的私有SQLite BLOB机制，独立领域库为 `data/staging/xlsx-artifacts/artifacts.sqlite3`（已被gitignore排除）。目录700、库文件600，沿用UID、符号链接及常规文件校验模式。表记录Artifact ID、Owner Hash、源Query ID、ASCII文件名、UTC创建时间、Hash、字节数和工作簿BLOB。

文件内容与元数据在一条INSERT和同一SQLite事务中提交，没有分离文件/元数据的中间态。提交后才返回ID。异常ROLLBACK，测试实际插入后抛出保存故障，确认没有可下载记录。下载重新核对Owner、内容Hash、长度和文件名，内容或库文件丢失时404。

单产物上限1 MiB，总内容配额64 MiB，事务锁等待0.2秒；不无限重试。组件重建后从相同目录读取，ID保持有效。未添加部署卷，不能将本机持久化能力宣称为跨容器重建持久化。每次显式POST创建新Artifact，尚无导出幂等键或自动过期清理；源历史过期后文件仍占本地配额但不可下载。

## 实测链路与统计口径

真实本地PostgreSQL + FastAPI ASGI测试请求，使用固定模型提案，未运行真实Gemini。完整脱敏记录在 `reports/warehouse/xlsx_artifact_download_v1.json`：

```text
已完成查询（私有export_snapshot）
 → generate_metric_xlsx → XLSX bytes
 → SQLite事务保存 → Artifact ID
 → 当前Owner下载 → 与保存字节/Hash一致
 → Excel回读：订单量2312，成功支付金额230864.57，金额格式0.00
 → 新建存储组件与FastAPI app → 原ID下载字节相同
```

报表值直接来自真实查询的已持久化快照。A能够导出和下载，B两者均404；无令牌401、未知ID404、客户端Owner字段422、缺失内容404、源权限撤销404，失败插入回滚均通过。

统计严格分开：最初业务指标SQL为2次；导出及下载新增业务指标SQL为0；真实Gemini为0。SQLite报表记录读取（含配额SELECT、核对及拒绝场景）和INSERT写入，以及源Query History访问检查，按实际报告单独计数。SQLite初始化DDL和事务控制不计入该CRUD统计；原 manager_factory 可能执行的只读技术元数据/ACL查询也不冒充指标SQL。

默认接口测试使用固定结构化结果及临时SQLite，不需要PostgreSQL或模型；显式打开数据库开关时才运行真实验收。未执行真实TCP网络服务、Chrome或前端验收，不将ASGI请求写成浏览器测试。

```sh
CI_NO_EXTERNAL_MODELS=1 .venv/bin/python -m pytest tests/test_artifact_api.py -q
CI_NO_EXTERNAL_MODELS=1 KNOWLEDGE_POSTGRES_TESTS=1 .venv/bin/python -m pytest tests/test_artifact_api.py -q
CI_NO_EXTERNAL_MODELS=1 .venv/bin/python -m pytest -q
```

现有CI隔离集成Runner增加artifact_api测试，未改工作流授权及部署步骤。没有新增依赖、工具权限、自由SQL或Agent执行路径。本轮停止于后端API。

实测结果：离线Artifact接口5通过、1项数据库验收按开关跳过；真实PostgreSQL专项6通过；相关数据库回归62通过；默认Python全量236通过、82项环境门控跳过。统计分别记录，不相加为独立样本。真实验收使用临时SQLite目录并在结束后清理，报告中的Artifact ID用于核对该次执行，不是当前日常API库中的常驻下载链接；日常调用默认使用上述持久化目录。
