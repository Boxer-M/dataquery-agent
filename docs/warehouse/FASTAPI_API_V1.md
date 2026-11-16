# FastAPI 本地接口 v1

API 将 HTTP 请求交给现有 Conversation Manager。Agent 继续负责规划、能力校验、只读查询和证据校验。FastAPI 与 Uvicorn 提供 HTTP 和 ASGI 服务，现有 Pydantic 2 提供请求与响应模型。[FastAPI 请求与响应文档](https://fastapi.tiangolo.com/tutorial/)

## 启动

在仓库根目录使用项目 Python 3.12 环境。首次运行时生成独立的本地访问令牌。命令以 `600` 权限创建被 Git 忽略的 `.env.api`，已有配置时跳过此步骤。Owner 由服务端沿用现有会话配置。

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python - <<'PY'
import os
from secrets import token_hex
with os.fdopen(os.open(".env.api", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as file:
    file.write("DATAQUERY_HTTP_TOKEN=" + token_hex(32) + "\n")
PY
PYTHONPATH=src .venv/bin/python -m dataquery_agent.api
```

服务绑定 `127.0.0.1:8000`。从本地配置读取令牌并在请求中使用 `Authorization: Bearer <本地令牌>`。

| 接口 | 用途 |
|---|---|
| `GET /api/health` | 存活检查，响应 `{"status":"ok"}` |
| `POST /api/conversations` | 创建当前 Owner 的会话，响应 `201` 和 `conversation_id` |
| `POST /api/chat` | 在指定会话提交问题。相同消息重试使用相同 `message_id` |
| `GET /api/queries/{query_id}` | 读取当前 Owner 可访问的历史摘要、引用和 Trace |
| `GET /openapi.json` | 查看 Pydantic 生成的接口契约 |

先创建会话。将返回的 `conversation_id` 和客户端为本次消息生成的 UUID 放进请求。

```json
{
  "conversation_id": "11111111-1111-4111-8111-111111111111",
  "message_id": "22222222-2222-4222-8222-222222222222",
  "question": "2025年1月订单量是多少？"
}
```

响应节选如下。指标服务返回的 Decimal 字符串保留原精度。联合查询的 `trace` 还可包含计划、任务、执行批次、Capability 和工具事件，正式目录引用位于 `citations`。

```json
{
  "status": "completed",
  "query_id": "33333333-3333-4333-8333-333333333333",
  "rows": [{"bucket": "all", "value": "2312"}],
  "row_count": 1,
  "rows_truncated": false,
  "trace": {"tasks": [], "capability": [], "tools": []},
  "replayed": false
}
```

业务状态包括 `completed`、`partial`、`clarification_required` 和 `failed`，随 HTTP 200 返回。澄清选项位于 `clarification_choices`。HTTP 401 表示令牌无效，404 对应缺失或无权访问的会话和历史，422 对应结构校验失败，413 对应大小限制，500 返回通用服务错误。历史只返回原会话已保存的摘要和证据，重复提交沿用其回执。

## 本地安全边界与验收

API 使用单一显式本地开发身份。入口检查环回地址和 Host，启动器禁用代理头。请求模型拒绝额外字段。服务端 Owner、会话 Lease 和消息幂等机制继续由 Conversation Manager 管理。响应采用字段白名单和现有脱敏规则，排除原始 SQL、模型 Prompt、私有配置和内部异常细节。问题最长 500 字，HTTP 请求体上限 8 KiB，指标行最多返回 50 行，序列化响应上限 256 KiB。健康接口仅报告 HTTP 存活。当前部署边界为本地环回访问。

实况启动验证了 Uvicorn 通过本机 TCP 返回健康响应。HTTP 专项使用 ASGI 请求、固定模型提案与真实 PostgreSQL，验证订单量 2,312、联合查询订单商品明细数 5,538、两条正式引用、可追溯历史及 Owner 拒绝。全量回归 185 项通过、1 项可选 Gemini 测试跳过。

```bash
API_POSTGRES_TESTS=1 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p test_api.py -v
```
