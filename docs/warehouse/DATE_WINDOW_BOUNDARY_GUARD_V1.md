# 日期边界修饰拒绝回归 v1

基线：`origin/main=15ef12a`，包含 PR #67 / `4146ad2`。分支 `codex/date-window-boundary-guard-v1`。

## 根因与最小修复

原文 `从2025-04-01至2025-06-30（结束日不包含）按品类同时列折扣前商品记录金额、商品行折扣金额；分别保留两列。` 和将修饰换成 `不计算最后一天` 的问题，固定模型提案均省略修饰，修复前错误进入 ready，窗口为 `[2025-04-01,2025-07-01)`。该次复现仅运行规划，没有执行 SQL。

旧正则依赖有限的动词与语序。现改为保守识别显式 ISO 日期上下文里的首日、末日、起始日、结束日、第一天、最后一天等边界锚点；当前解析器不解释这些额外限定，统一澄清，不尝试猜测开闭边界。已有数字端点修饰和区间括号规则继续保留。

- `src/dataquery_agent/intent_time.py`：共用 `date_boundary_modifier`，原文优先、模型提案次之；审计记录原文命中片段及其字符位置。
- `src/dataquery_agent/business_plan.py`：直接进入 `PlanValidator.windows` 时也执行同一原文检查，避免绕开 Planner 边界。
- `tests/test_explicit_date_windows.py`：8 种边界表达 × 模型保留/省略两种提案；覆盖原文前置、尾置、区间内部、起始日排除、结束日排除及相互冲突。

无需新增依赖。没有改动业务指标、SQL、Capability、Owner 或数据；没有新增记忆功能。

## 实测 Trace

| 原文修饰 | 原文识别 | 计划/HTTP状态 | 原因 | 新增指标 SQL |
|---|---|---|---|---|
| 结束日不包含 | `user_question`，`结束日`，字符 [23,26) | clarify / clarification_required | unsupported_date_boundary | 0 |
| 不计算最后一天 | `user_question`，`最后一天`，字符 [26,30) | clarify / clarification_required | unsupported_date_boundary | 0 |

实际索引和原始提案见 `reports/warehouse/date_window_boundary_guard_v1.json`；位置为 Python 字符偏移、右开，不是 PDF 坐标。模型保留修饰或省略修饰时均从原文产生同一澄清路径，不依赖模型自报语义。所有新增 HTTP 反例同时核对 History Trace。

正常区间及仅省略“从”仍通过；原日期偏移和缺端点反例仍拒绝。`不含退款` 不命中日期边界检查，日期规范化完成后由原业务校验返回 `unsupported_filter`，不会当成日期错误或静默忽略业务条件。

## 验收结果与复跑

- 固定提案 + 真实 PostgreSQL 日期专项：6 passed。
- 原正例 completed，24 品类 × 两指标，48/48 单元格与独立 ODS SQL 一致，指标查询 2 次。
- 既有 7 次反例 + 新增 16 次边界反例：23/23 澄清，每次新增指标 SQL 为 0。
- 相关 PostgreSQL 回归：43 passed（含日期专项、Planner、Conversation、DAG、专业角色、Catalog恢复、Capability Trace、API、HITL）。
- Python 离线全量：246 passed、86 skipped。数量重叠，不将不同运行的测试数相加。
- 真实 Gemini：未执行，实际调用 0；本轮依固定提案验收，不声称完成新的真实模型回归。未修改前端，未进行 Chrome 验收。

```sh
CI_NO_EXTERNAL_MODELS=1 .venv/bin/python -m pytest -q
CI_NO_EXTERNAL_MODELS=1 .venv/bin/python -m pytest tests/test_explicit_date_windows.py tests/test_business_query_planner.py -q
CI_NO_EXTERNAL_MODELS=1 CONVERSATION_POSTGRES_TESTS=1 DATE_WINDOW_REPORT=/tmp/date-boundary-pg.json .venv/bin/python -m pytest tests/test_explicit_date_windows.py -q
CI_NO_EXTERNAL_MODELS=1 CONVERSATION_POSTGRES_TESTS=1 KNOWLEDGE_POSTGRES_TESTS=1 API_POSTGRES_TESTS=1 HITL_POSTGRES_TESTS=1 .venv/bin/python -m pytest tests/test_explicit_date_windows.py tests/test_business_query_planner.py tests/test_business_planner_v2.py tests/test_conversation.py tests/test_task_dag.py tests/test_specialists.py tests/test_catalog_recovery.py tests/test_capability_trace_linkage.py tests/test_api.py tests/test_hitl.py -q
```

边界锚点采取保守拒绝，明确表示包含首尾日也可能要求用户用无修饰的完整日期表达确认；不宣称支持任意中文边界措辞。本轮不推进跨会话记忆，下一步仅审查并合并本分支。
