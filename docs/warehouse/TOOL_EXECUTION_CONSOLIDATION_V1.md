# Tool Execution Consolidation v1

## 调用链与收敛范围

| 路径 | 原调用链 | 当前调用链 |
| --- | --- | --- |
| 单指标 | Unified Agent → Capability → Metric Query Service，返回后由 Unified Agent 校验 | Unified Agent → 绑定 Capability → `execute_bound_metric` → `execute_metric` 调用指标服务并校验 |
| 指标 DAG | Planner metric handler → `call_tool` → Unified Agent 回调 → 指标服务。返回时 Unified Agent 和 Planner 各校验、标准化一次 | Planner DAG → 已配置的指标 handler → 绑定后执行 → `MetricExecution.task_result`，直接使用一次标准化所得的行 |
| SQL + Catalog/PDF | 指标 handler 通过 Data Analysis Agent 复核并调用指标。Knowledge Agent 分派知识工具 | 数据专业角色保留复核并使用同一指标执行接口。Catalog、独立文档问答和 PDF 联合任务使用共享的专业角色 Trace 投影 |
| 独立 Planner CLI | Planner → `call_tool` → 固定模板指标服务并校验 | Planner → `call_tool` → 共用 `execute_metric`，保留独立入口及服务自身的请求校验和只读角色 |

集成 DAG 路径减少一次 `call_tool` 回调层和一次重复结果校验。工具执行核心集中在 `metric_tool.py`。两个专业角色的工具选择为确定性程序。Data Analysis Agent 对齐预检和执行前复核的能力合同。Knowledge Agent 区分正式目录和文档检索，执行仍由原有知识服务处理。两者继续提供原有 `execute` 接口。

## 接口

现有应用协调器使用可信的结构化指标请求和 Capability 决策。示例中的 binding 包含 `plan_id`、`task_id`、`attempt_id`、授权对象及阶段。

```python
execution = agent.execute_bound_metric(request, effective_route, trace, binding=binding)
event, rows = execution.task_result(planned_call, agent_call=specialist_trace)
```

`MetricExecution` 持有原始结果、已校验并标准化的行和工具耗时。原结果用于单指标展示和恢复检查点，标准化行用于组合计算。失败沿用既有错误码。Task 事件使用同一次工具执行测得的耗时，DAG 继续管理依赖与顺序。HTTP 从原 `query_id` 投影 `run_id`，工具和授权通过 Plan、Task、Attempt 关联。

## 安全与验收

- SQL 工具记录关联同一 Task 实际生效的 Capability。复核拒绝或跨任务 Trace 关联阻止对应 SQL。专用读角色、动态和明细路径的 SQLGlot、文档当前版本和引用检查继续由既有执行边界负责。
- 真实 PostgreSQL、pgvector 和 Apple Vision 回归覆盖独立 PDF、SQL + PDF、Owner 隔离、历史回放和部分失败。Catalog 补证沿用原 Plan 和 Task，新增 Attempt，已完成的 SQL 在恢复过程中调用 **0 次**。
- Python 224 项中通过 223 项，跳过 1 项可选真实 Gemini 测试。固定提案用例检查两个指标执行两次 SQL、标准化两次并产生两个工具事件。
- 前端 5 项测试、TypeScript 检查和生产构建通过。真实 Chrome 通过隔离的本机 FastAPI 和生产构建联调。固定模型提案，实际查询 PostgreSQL，返回 2025 年 1 月订单量 2312、明细数 5538。联合查询有 4 个任务和 4 条 Capability 记录，实时与历史 Trace 完全一致。打开历史新增聊天请求 0 次。现有 Vite 代理白名单由前端自动测试覆盖。

本接口面向可信 Python 调用方。DAG、Planner、所有权和工具运行权限仍由现有边界管理。
