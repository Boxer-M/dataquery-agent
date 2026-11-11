# 统一 Agent 查询入口

统一入口接受一条中文问题，返回任务、业务口径、能力路径、查询结果、中文说明和关联 Trace。

```sh
PYTHONPATH=src .venv/bin/python -m dataquery_agent.unified_agent \
  '2025年1月成功扣款一共多少钱'
```

CLI 使用已配置的 Gemini、SQL Shed 元数据只读账号和指标服务账号。Python 入口为 `UnifiedAgent(service, capability).answer(question, generate, today=None, state=None)`。初始化能力解析器使用 `CapabilityResolver(CapabilityRegistry(MetadataService.local().snapshot()))`。默认以 Asia/Shanghai 日历解析相对时间，测试可显式传入日期。非空会话状态当前返回澄清，接口保留供后续扩展。

## 路由与执行

1. 任务识别结合原文中的明确指标、比较或排行特征、已登记明细语义以及模型提取结果。原文中的高信号提示注入和 SQL 指令会被拦截。销售额、退款金额、收入等多口径请求返回候选。缺少到账事件、利润口径或浏览数据时给出限制。
2. 单指标复用 Semantic Resolver 的时间、维度、筛选和指标校验。多指标复用 Business Query Planner 生成并验证计划。明确指定的业务事件时间随请求交给 Capability Resolver 校验。所有步骤必须通过字段、权限及来源检查才会开始执行。执行结果还要与已批准路径核对来源、窗口、指标和 Trace。
3. 标准指标调用 Metric Query Service。多指标结果计算与部分失败处理继续使用现有规划器。两项明细能力只返回 `DETAIL_QUERY` 和 `plan_only`，没有数据库业务查询。未登记的逐笔需求返回明确限制。
4. 模型单次请求沿用 20 秒超时。可重试 HTTP 错误或超时至多重试一次，每轮至多记录 6 次模型尝试。指标服务及规划器继续控制数据库超时和查询步数。Trace 记录每次尝试的阶段、版本、状态、延迟和可取得的 Token 数据。工具 Trace 关联其能力 Trace 和实际来源。

## 候选匹配与提示词

[Metric Candidates](../../src/dataquery_agent/metric_candidates.py) 保留目录名称和别名优先级。没有明确匹配时，从真实名称、别名与定义做字符二元组召回，至多提供 6 个候选。Gemini 只在候选内选择，且必须逐字引用原文。候选唯一且同一原文通过独立业务锚点和否定条件检查时，才确认用于查询。其他情况返回候选澄清。响应分别保留原文片段、模型提案、程序确认及候选列表。已支持的锚点集中在订单计数、成功支付金额、支付尝试数和失败尝试率。

[提示词构建器](../../src/dataquery_agent/agent_prompts.py) 为 Intent Extraction、Metric Resolution 和 Business Planning 标注版本。前两阶段分别使用 `intent-v2`、`metric-resolution-v2` JSON Schema，规划使用既有业务计划契约。提取只接收原文及少量识别提示，候选匹配只接收短名单与业务定义。模型上下文不包含数据库字段清单、业务查询数据或凭据。

提示注入检测覆盖已列举的明显指令。安全边界由目录、严格结构验证、固定模板、只读权限和能力检查共同落实。明细规划不执行，长尾多指标表达仍需要能被现有规划器识别的明确指标。元数据快照代表初始化时的结构与权限，长生命周期调用方需按发布版本刷新。

## 评测

```sh
PYTHONPATH=src .venv/bin/python tests/evaluate_unified_agent.py --mode fixed
PYTHONPATH=src .venv/bin/python tests/evaluate_unified_agent.py --mode postgres
PYTHONPATH=src .venv/bin/python tests/evaluate_unified_agent.py --mode live
CAPABILITY_POSTGRES_TESTS=1 INTENT_GEMINI_TESTS=1 METRIC_QUERY_POSTGRES_TESTS=1 \
  WAREHOUSE_POSTGRES_TESTS=1 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

固定模式使用模型与工具固定输出。PostgreSQL 模式使用固定模型输出，读取真实元数据与指标服务，并以独立 ODS SQL 对账。live 模式同时使用真实 Gemini。明细场景核对选路、粒度和零工具执行。候选召回与选定准确率按存在预期查询指标的用例统计；歧义处理用独立固定用例验证。查看[用例](../../tests/fixtures/unified_agent_v1.json)、[Runner](../../tests/evaluate_unified_agent.py)及[报告](../../reports/warehouse/unified_agent_v1.json)。

最终固定评测 33/33，真实 PostgreSQL 13/13，其中 11 个实际结果与 ODS 一致。真实 Gemini 首轮 5/8，复查定位到了模型结构和已登记明细片段的提取偏差。加结构约束与可证明的原文归一化后，第二轮 8/8，包含 7 个真实查询及 1 个只规划明细的场景。报告保留首轮、逐项复查和第二轮全部尝试。第二轮均值 4.554 秒，最长 7.503 秒，提供方返回 4,501 总 Token。样本结果用于描述本轮表现。
