# React 本地问答工作台 v1

工作台用 React、TypeScript 和 Ant Design 展示现有 Agent 的问答结果。浏览器用原生 fetch 调用同源 Vite 代理，代理向本机 FastAPI 添加 Bearer 令牌。

## 本地运行

按 [FastAPI 本地接口说明](FASTAPI_API_V1.md) 配置后端及本地令牌。在仓库根目录打开两个终端。

```sh
PYTHONPATH=src .venv/bin/python -m dataquery_agent.api
```

```sh
npm --prefix frontend ci
npm --prefix frontend run dev
```

打开 [本地工作台](http://127.0.0.1:5173)，确认 API 已连接。点击“新建会话”，发送“2025年1月订单量是多少？”，再发送“那2月份呢？”。可在同一会话内查看两次回答的行数、查询 ID 和返回值。

| 代码位置 | 职责 |
| --- | --- |
| `frontend/src/workspace.tsx` | 会话、消息发送和重试、业务状态及结果展示 |
| `frontend/src/api.ts` | 同源请求、超时和响应校验 |
| `frontend/server/local-api.mjs` | 代理访问检查、服务端鉴权、请求及响应限制 |
| `frontend/tests/browser.mjs` | Chrome 真实问答与受控故障验收 |

代理限定环回地址、Host、Origin、Fetch Metadata、请求方法及路由，仅转发健康检查、创建会话和聊天。令牌从仓库根目录的本地 `.env.api` 读取，会话页面状态保存在内存中。此代理用于本地单人开发，能够访问同一本机的进程属于信任边界。生产构建生成静态文件，API 联调使用 Vite 开发服务。

## 验收记录

- 真实 Chrome → FastAPI → Agent → PostgreSQL 路径返回 1 月订单量 **2,312**，同会话 2 月追问返回 **2,049**。查询 ID 分别为 `3a4cdb54-2dbd-40b9-8164-a5649f5a29ed`、`acffac4a-96aa-49b0-8944-d36a6fead441`。
- “2025年1月销售额”返回真实 `clarification_required`。浏览器受控注入断网、部分完成和 HTTP 504，验证显示及重试。断网后的重试实际发到后端，沿用原请求体和 `message_id`。
- 两项代理测试、TypeScript 检查及生产构建通过。Python 回归运行 186 项，185 项通过，1 项可选 Gemini 测试跳过。浏览器真实问答单独执行。
- 浏览器请求无 Bearer，浏览器存储为空。构建产物未发现实际令牌。实测外部 Origin 和伪造 Host 返回 403，未开放的历史路由返回 404，读取凭据文件返回 403。
- Vite 对当前 JavaScript 主包给出体积警告，约 866 kB，gzip 后约 277 kB。

```sh
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend run test:browser
```

浏览器验收要求两个服务均在运行，使用本机 Chrome。可用 `CHROME_BIN` 指定 Chrome 路径，用 `WORKSPACE_EMPTY_SCREENSHOT` 和 `WORKSPACE_SCREENSHOT` 分别保存会话首页及首条查询结果截图。
