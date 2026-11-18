# 经营指标分析 Skill 接入 v1（阶段 B）

## 执行链路

基线为阶段 A 已合并的 main `961d6dc`。不新增依赖或规划器，不实现 XLSX、文件生成或下载。

```text
原有 UnifiedAgent 路由（简单查询、正式定义、歧义及安全拒绝优先）
  → business()
  → business_planning_call() 读取可信清单，确定性选择适用经营 Skill
  → SkillRegistry 校验审核、启用、路径及 SHA-256，按需加载
  → 同一次 AgentRuntime.call("planning") 注入审核后的指导 JSON
  → 原 BusinessQueryPlanner.prepare / PlanValidator
  → 原 Capability / DAG / Metric Tool / 执行前授权
  → 原确定性结果组合及业务回答
```

`agent_prompts.py` 负责上下文拼装，`unified_agent.py` 只增加衔接和加载失败处理。Skill 身份由 `skill_registry.py` 返回可信清单的版本和 Hash。指令更新为 0.2.0，包含口径、时间、维度、比较、限制和观察/因果边界，不复制指标公式、SQL或凭据。

只有既有路由进入经营路径，且原文满足阶段 A 保守选择规则时才注入。普通并列请求没有命中规则时继续原路径，不为提高 Skill 命中率改变意图路由；不确定请求仍澄清。摘要不读取正文，未选资源和 XLSX 指令不加载。直接调用独立 BusinessQueryPlanner 的接口保持不变。

Skill 不是授权或执行代码。未匹配时继续原 Planner；选中但未审核、禁用、Hash/路径失效时，返回 `skill_unavailable`，不调用模型或工具。清单损坏/不可读也安全终止经营路径。加载成功但超过 16 KiB 上下文限额时拒绝注入。原规划输出 Schema、Validator、六次模型尝试预算、SQL安全、Owner及工具范围保持原约束。

落点是 planning 上下文。本轮不增加查询后的 LLM解释器；回答仍由既有结果组合、render 和 Insight 路径生成，不能把指令中的建议宣称为新增因果分析能力。

## Trace 与兼容性

`trace.skills` 经 `runtime_trace.trace_view()` 进入原 Query History JSON，无数据库表结构迁移；FastAPI 的 PublicTrace 新增可选列表，旧记录没有该字段时显示空列表。仅投影身份、原因、状态及计数，不返回正文、用户明细、凭据或内部 Prompt。

- `loaded`：通过原注册器的审核、启用及文件Hash校验。
- `context_injected`：原 planning 回调实际产生了携带 Skill 指令的模型适配器尝试。
- `planning_model_calls`：同一次规划的适配器尝试数，含有限重试；固定提案运行也产生适配器尝试，不能等同真实 Gemini调用。
- `planning_tokens`：API/适配器确实提供的整次 planning 用量，缺失或有重试未报告时为 null，不默认为零。
- `extra_model_calls=0`：没有专门用于 Skill选择、加载或解释的新模型调用。
- `extra_tokens=null`：不能独立测得 Skill相对无Skill运行的真实 Token差值。`instruction_bytes` 只是 UTF-8字节数，不冒充 Token。
- `context_version=business-skill-context-v1` 区分新增上下文格式；原 planning 输出契约版本保持兼容。

真实本地 PostgreSQL + 固定模型提案验收的脱敏记录见 `reports/warehouse/skill_business_integration_v1.json`。示例查询为“分析2025年1月订单量和成功支付金额的多指标汇总”，独立ODS与服务结果分别为 **2312** 和 **230864.57**。

```json
{"skill_id":"business-metric-analysis","version":"0.2.0",
 "reason":"explicit_complex_metric_analysis","loaded":true,"context_injected":true,
 "planning_model_calls":1,"planning_tokens":null,"extra_model_calls":0,"extra_tokens":null}
```

完整 Hash 位于报告。该示例真实 Gemini和Embedding调用均为0，固定回调1次；不能作为真实模型理解准确率或Token优化证据。

## 验证及复现

```sh
CI_NO_EXTERNAL_MODELS=1 .venv/bin/python -m pytest tests/test_skill_registry.py tests/test_skill_business.py -q
CI_NO_EXTERNAL_MODELS=1 KNOWLEDGE_POSTGRES_TESTS=1 .venv/bin/python -m pytest tests/test_skill_business.py -q
CI_NO_EXTERNAL_MODELS=1 .venv/bin/python -m pytest -q
```

专项覆盖适用选择、真实上下文注入、Skill有无对照、原单指标路径、歧义/unsupported、维度/粒度/步骤拒绝、执行前Capability拒绝、禁用/未审核/篡改、有限重试、Supervisor独立上下文、API/History投影和脱敏。PostgreSQL用例另验证独立ODS对账、持久化Trace、跨Owner拒绝和同message_id重放不重复查询。

现有 `scripts/ci/run.py` 的隔离集成套件纳入 `skill_business`，继续复用测试数据库、固定提案和外部模型禁用钩子，未改工作流权限或发布门禁。真实 Gemini、真实模型对照效果及 XLSX适配不在本轮验收范围。

本轮实测：阶段 B 专项15项（含真实PostgreSQL）通过；默认Python全量203通过、80项环境门控跳过；另一次本地PostgreSQL相关回归58项通过，覆盖DAG、专业Agent、Catalog/RAG、恢复、HITL、API和会话。前端6项通过，TypeScript与生产构建通过，保留既有图表chunk超过500 kB的警告。上述统计分别记录，不相加为独立样本数。未执行真实Gemini或本分支远端GitHub Actions。
