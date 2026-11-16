# Frontend v3 查询历史与 Agent Trace

在仓库根目录分别启动 API 和前端。首次配置令牌及数据库连接参见 [FastAPI 本地接口](FASTAPI_API_V1.md)。

```sh
PYTHONPATH=src .venv/bin/python -m dataquery_agent.api
```

```sh
npm --prefix frontend ci
npm --prefix frontend run dev
```

可打开 [127.0.0.1](http://127.0.0.1:5173/) 或 [localhost](http://localhost:5173/)。页面记住当前会话中收到的查询 ID。点击侧栏记录或回答下的“查看查询历史”，前端发起 `GET /api/queries/{query_id}`，抽屉展示后端已保存的摘要。刷新页面会清空页面内存列表。无权访问和查无记录共用提示，沿用后端的 Owner 隔离。若存在定向恢复的上一条查询链接，可继续用同一历史接口查阅。

时间线按后端提供的任务执行顺序排列。展开可查看实际 Agent、耗时、依赖、失败原因和数据来源。Capability 预检和执行前复核单独列出，工具事件保留实际引用的 Capability Trace ID。当前回执与历史记录按任务或 Trace 标识对比。恢复时，新旧 Attempt ID 可同时出现，原失败记录通过上一条查询链接查看。

Vite 服务继续只绑定环回地址。两个 Host 仅接受各自匹配的 Origin。历史代理只允许 UUID 路径上的 GET 请求，Bearer 由代理添加。历史摘要可能仅包含行数、标量或已保存的有限结果行。旧记录和部分事件缺少细节时，界面显示“未提供”。

```sh
npm --prefix frontend test
npm --prefix frontend run build
WORKSPACE_HISTORY_SCREENSHOT=/tmp/dataquery-history.png npm --prefix frontend run test:browser
WORKSPACE_ORIGIN=http://localhost:5173 npm --prefix frontend run test:browser
```

## 验收记录

两个本地地址均完成 Chrome、FastAPI 和 PostgreSQL 联调。联合查询返回 2,312 笔订单、5,538 条订单商品明细和两条正式引用。当前回执与保存记录的 Plan ID、Attempt ID、4 个任务、4 条 Capability 校验和 5 个工具事件一致。两个 SQL 工具事件各自引用同一任务和 Attempt 的执行前复核记录。打开历史后新增聊天请求数为 0。联合查询 ID 分别为 `6d8ccf0e-7be5-4425-9bc0-fdbc5342faae`（127.0.0.1）和 `50c8d6ee-5904-4aea-aa5f-38a59f5b7cac`（localhost）。

PostgreSQL 集成测试通过注入 SQL 或 Catalog 故障，复核了依赖跳过、历史的 Owner 隔离、定向恢复的前序查询 ID 及两个 Attempt。恢复沿用两条原始 SQL 结果，新增指标查询执行数为 0。Chrome 使用受控响应覆盖跳过原因、恢复链接、缺失字段与 404，也复验了真实澄清、断网重试、精度及引用。

TypeScript、生产构建、两项代理测试及两次 Chrome 测试通过。Python 回归 185 项通过，1 项可选 Gemini 测试跳过。实测非法 Host、外部 Origin、两个本地地址间交叉 Origin 和凭据文件请求均被拒绝。浏览器请求和构建产物均未检出令牌。Vite 提示主包约 947 kB，gzip 后约 300 kB。
