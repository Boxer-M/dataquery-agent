# Runtime Identity & Trace Consolidation v1

会话服务取得消息租约后分配 `query_id`。FastAPI 将该值投影为 `trace.run_id`。它是审计关联键，入口产生的 `trace_id` 和各工具产生的 `trace_id` 保留各自用途。会话外直接调用 Agent 时仍可使用原有入口 `trace_id`，这类调用没有会话审计键。

| 标识 | 生命周期 |
| --- | --- |
| `conversation_id`、`message_id` | 会话归属和消息幂等。回放复用已保存的查询键 |
| `query_id`、`trace.run_id` | 一次已持久化的会话请求。恢复请求有自己的查询键，并通过 `previous_query_id` 关联前次请求 |
| `plan_id`、`task_id` | 逻辑计划和子任务。单指标调用使用一个节点。联合分析使用现有 DAG 节点 |
| `attempt_id` | 一次执行尝试。Catalog 恢复复用任务 ID，为重新运行的节点记录新 Attempt。已完成 SQL 保留原 Attempt |
| `capability_trace_id` | SQL 工具事件指向同一 Plan、Task、Attempt 的生效授权记录 |

## 模块依赖

此前 Unified Agent 和 DAG 提供运行证据，Query History 与 API 分别读取并映射工具字段。现在两者都调用 `runtime_trace.trace_view`，工具字段白名单和错误码映射集中在一处。FastAPI 再执行公开字段校验、脱敏和数量限制。历史仍保存原 JSON 键，旧记录按现有字段读取。消息回放继续只返回原执行的摘要和查询标识，完整 Trace 通过历史查询获得。

单指标通过原 Metric Query Service 执行，使用现有 Capability 绑定校验。联合任务的排序、依赖传播及状态来自现有 DAG。Catalog 补证的一次批量检索仍记录一个工具事件，关联 Plan 和新 Attempt。两个补证节点可指向同一次工具检索。

## Trace 样例

下例为 PostgreSQL HTTP 集成场景的字段缩写。测试使用固定意图提案和实际 SQL，2025 年 1 月订单量返回 `2312`。字母标识替代运行生成的 ID。

```text
query_id = Q    trace.run_id = Q
plan_id  = P    attempt_id   = A
task P/1, attempt A, status ok, trace_id T
tool SQL, task P/1, attempt A, trace_id T, capability_trace_id C
capability C, task P/1, attempt A, phase preflight
```

SQL + PDF 集成测试检查指标、PDF 和汇总节点共享同一 Plan 和 Attempt，并核对汇总依赖两个上游节点。恢复测试检查新 Run 和前次查询的关联、Catalog 新 Attempt、原 SQL Attempt 以及 SQL 调用次数。PDF 测试使用固定提案和测试向量，并实际访问 PostgreSQL。

```sh
API_POSTGRES_TESTS=1 KNOWLEDGE_POSTGRES_TESTS=1 DOCUMENT_RAG_POSTGRES_TESTS=1 \
  PYTHONPATH=src:tests .venv/bin/python -m unittest \
  test_runtime_trace test_api test_catalog_recovery test_pdf_knowledge
```
