# React 问答工作台 v2

启动方法沿用 [Frontend v1](FRONTEND_V1.md)。工作台读取现有 FastAPI 的 `QueryResponse`，使用同源代理连接本机服务。

## 展示与交互

- 单指标汇总使用指标卡，其余有行结果使用 Ant Design Table。十进制字符串原样显示，可展开核对 API 原始结果行；截断结果会标明展示范围。
- 定义和引用来自 `definitions` 与 `citations`。按正式 Catalog 的精确 `source_location` 关联指标 ID，其他证据单列。引用可查看原文、版本、文档及片段 ID。所有内容按普通文本渲染。
- 部分完成时保留已返回的数据，并在公开任务状态存在时列出未完成的部分。缺失引用会明确标记。
- 澄清按钮把 API 返回的候选 ID（无 ID 时为候选名称）作为普通 `question`，沿用当前 `conversation_id` 并生成新的 `message_id`。是否可以恢复以及如何解释候选由后端判断。用户也可输入完整问题。网络重试使用原请求体。

当前 OpenAPI 的正式定义无独立公式字段，引用无独立文档内容 Hash 字段；常规单指标结果也可能仅在回答正文提供中文名和统计窗口。界面保留正文与实际返回的结构化信息，指标名缺失时卡片以指标 ID 标识。版本、单位和日期仅在 API 提供时按相应字段展示。

## 复测

```sh
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend run test:browser
```

Chrome 测试在本地 FastAPI、Gemini 和 PostgreSQL 可用时运行。可通过 `WORKSPACE_SCREENSHOT`、`WORKSPACE_HYBRID_SCREENSHOT`、`WORKSPACE_CLARIFY_SCREENSHOT` 保存单指标、联合证据和澄清截图。断网、部分完成与超时等受控场景在测试输出中单独记录。

## 真实验收记录

| 场景 | 实际结果 |
| --- | --- |
| 2025 年 1 月订单量 | 2,312，查询 ID `d1492cf8-0a84-4cc9-93ba-5d20373d7693` |
| 同会话追问 2 月 | 2,049，查询 ID `f207cc8a-586e-4f42-84f0-f6afabb22dae` |
| 1 月订单与商品明细联合查询 | 2,312 和 5,538，两条引用的版本、来源和片段 ID 与 API 一致，查询 ID `8fe7fab8-e2a6-44e3-bd41-d224c7357b5c` |
| 销售额澄清 | 选择折扣后商品销售金额，当前会话使用新消息 ID 完成查询，查询 ID `65f52b52-c281-4d4b-a6a4-6a38bd4cdf73` |

Chrome 注入单次断网后，重试以原请求体发往真实 API。独立受控响应覆盖部分完成、结果截断、业务失败、HTTP 504、超出 JavaScript 安全整数范围的十进制字符串，以及含 HTML 标签的定义文本。浏览器存储为空，聊天请求与生产构建均未检出 Bearer 令牌。

TypeScript、生产构建、两项代理测试及 Chrome 验收通过。含本地 PostgreSQL 集成的 Python 回归运行 186 项，185 项通过，1 项可选 Gemini 测试跳过。Vite 仍提示主包偏大，约 869 kB，gzip 后约 278 kB。
