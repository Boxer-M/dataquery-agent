# Real-LLM End-to-End Evaluation v1

黄金集包含 13 条请求。每个版本的 8 条请求实际收到 Gemini 生成响应，另外 5 条由确定性路径完成。模型样本单独统计。6 条预测试覆盖意图提取或业务规划的原始提案，另外 2 条验证合成退款规则的有证据回答与缺证据拒答。

同一评测 Runner 依次加载 DAG 版 `1994f12` 和专业 Agent 版 `a9fb8b1`，使用同一本地 PostgreSQL、相同模型配置、相同业务角色和同一评测所有者。两个版本各创建独立会话。结构化查询使用真实指标服务，数值以独立 ODS SQL 对账。正式引用校验指标 ID、版本、原文与历史记录。模型输入限于黄金问题、既有规划提示和指标 ID 与名称，以及此前已验收的合成退款文档。ODS 行、真实客户资料和非合成文档留在本地。报告仅记录经过脱敏的模型输出和应用 Trace。

| 指标 | DAG 版 | 专业 Agent 版 |
|---|---:|---:|
| 整体结果 | 13/13 | 12/13 |
| 原始模型意图 / 实体提案 | 6/6 / 6/6 | 6/6 / 6/6 |
| 合法计划 | 2/2 | 2/2 |
| 工具路径 | 13/13 | 13/13 |
| SQL 独立 ODS 对账 | 5/5 | 5/5 |
| SQL Capability Trace 可关联 | 5/5 | 4/5 |
| 正式目录引用 | 3/3 | 3/3 |
| 拒绝与澄清 | 4/4 | 4/4 |
| 真实生成调用 / Query Embedding 请求 | 8 / 2 | 8 / 2 |
| 生成输入 / 输出 / 总 Token | 3876 / 652 / 4528 | 3876 / 624 / 4500 |
| 请求耗时 P50 / P95 | 2.148 / 6.787 秒 | 2.111 / 5.298 秒 |

端到端耗时按 13 条请求计算，包含无需调用模型的 Fast Path。P95 使用最近秩法，本样本取最大值。两个版本的调用顺序分开，样本量有限，延迟与 Token 差异不适合作为性能收益证据。专业 Agent 版与 DAG 版共用同一个 Planner、Validator 和工具，专业 Agent 的额外选择与能力复核为确定性执行。

专业 Agent 版联合请求中，SQL 数值与正式引用均正确，两个 SQL 工具事件中的 `capability_trace_id` 指向专业 Agent 内部重新校验生成的 ID，而响应 `routes` 只保存预检记录。失败分类为 `sql_capability_trace_unlinked`。建议先把重新校验的路由附到执行 Trace，再扩充带歧义、工具故障和恢复的真实样本。当前样本没有提供增加 Supervisor 的证据。

最初评分把单指标请求省略的空分组和空筛选识别为不同参数。最终评分按 Metric Query Service 的默认值归一化，使用原始真实响应重新核对 ODS，未重新请求 Gemini。

```bash
PYTHONPATH=src .venv/bin/python tests/evaluate_agent_e2e.py --revision a9fb8b1
.venv/bin/python -c 'import json; print(json.dumps(json.load(open("reports/warehouse/agent_e2e_evaluation_v1.json"))["specialists"]))' > /tmp/agent-e2e-specialists.json
PYTHONPATH=src .venv/bin/python tests/evaluate_agent_e2e.py --rescore /tmp/agent-e2e-specialists.json
```

`--rescore` 接收单版本采集结果。已提交报告包含 `baseline` 和 `specialists` 两个对象，复评分时先提取所需对象。比较版本时，可把 `1994f12` 导出到独立目录，使用该版本的 `src` 和同一套本地配置运行同一个 Runner。通过同一个本地 `E2E_FIXTURE_OWNER` 环境配置让两轮共享合成知识索引。

[黄金用例](../../tests/fixtures/agent_e2e_v1.json) · [两版本报告与完整结构化 Trace](../../reports/warehouse/agent_e2e_evaluation_v1.json)

## Capability Trace 关联修复验收

历史真实评测保留原样。修复验收先对历史联合用例复评分，复现 `sql_capability_trace_unlinked`。随后把该用例已保存的 Gemini 规划提案送入现有校验器，重新执行真实 PostgreSQL、Catalog 查找和历史持久化。本次复验没有新 Gemini 调用。

预检和执行复核均保存在外层路由、统一 Trace 和 Query History。每条记录绑定 Plan ID、Task ID、Attempt ID 和获准的指标请求。专业 Agent 的 SQL 工具事件必须指向本任务唯一的执行复核记录。无专业 Agent 的 DAG 数值查询使用实际获准的预检记录。执行门在调用指标服务前验证关联，复核拒绝或跨任务 Trace ID 会阻止对应 SQL。

```bash
KNOWLEDGE_POSTGRES_TESTS=1 CAPABILITY_LINK_REPORT=/tmp/capability-linkage.json \
  PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p test_capability_trace_linkage.py -v
```

[修复前后 Trace 与 PostgreSQL 验收](../../reports/warehouse/capability_trace_linkage_v1.json)
