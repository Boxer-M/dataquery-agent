# 经营查询规划

经营问题可经 Gemini 规划为并列查询、跨期比较、排行或指标阈值筛选。运行入口为 `BusinessQueryPlanner(service).answer(question, generate)`，或从项目目录运行：

```sh
PYTHONPATH=src .venv/bin/python -m dataquery_agent.business_query_planner \
  '2025年2月折扣后商品销售金额环比变化多少'
```

调用使用既有 Gemini 配置及 [指标服务只读登录](METRIC_QUERY_SERVICE.md)。JSON 返回经校验的 `plan`、每步 `tools` Trace、组合后的 `results`、中文 `answer_zh` 和总 Trace ID。澄清或能力不足时返回业务消息。用户可以根据澄清重新描述问题。

## 计划与执行

1. Gemini 根据真实指标目录提出指标 ID、原文时间片段、原文分组和筛选、分析类型、比较、排序、Top N 和未解决规则。输出 JSON 通过严格字段与类型检查。
2. Plan Validator 从问题核对指标、时间、阈值和排序依据，再使用 `MetricQueryService.validate` 验证每个请求。不同指标须分别支持同一分组和筛选。销售、支付、退货在统一展示键上对齐时，保留各自的事实粒度和事件时间。
3. 验证器生成全部 `required_tool_calls`。例如“2025年2月折扣后商品销售金额环比变化多少”展开为同一指标的两个请求：

   | 步骤 | 指标 | 起始日 | 结束日（右开） |
   |---|---|---|---|
   | 1 | `item_net_sales_amount` | 2025-01-01 | 2025-02-01 |
   | 2 | `item_net_sales_amount` | 2025-02-01 | 2025-03-01 |

4. 执行器依次调用指标服务，最多 6 步。沿用单次连接及查询超时，累计 30 秒后停止发起新步骤，已启动的步骤仍受服务自身超时约束。每步记录请求、状态、耗时、来源和 Trace。Gemini 的可重试 HTTP 状态允许一次重试，数据库步骤由既有服务处理。
5. Python 使用 Decimal 对齐、排序、计算及生成中文说明。模型输出无法指定 SQL、数据库账号、字段或数据源。说明按实际结果填充，没有额外的模型生成环节。

## 计算与支持边界

- **跨期比较**采用两个完整自然月，或两个互不重叠且等天数的窗口。较早窗口为基期。环比要求相邻自然月。绝对变化为本期减基期。相对变化以正基期为分母，基期为零、为负或有缺失时返回原因和空比例。百分比指标的绝对变化用百分点展示。
- **排行和筛选**支持一个业务维度，方向和排序指标必须明确。单指标的 Top N 默认降序，同值按分组键稳定排序。Top N 上限为 100，空值不参与排名。交叉条件使用同分组指标的十进制阈值，多个阈值同时满足。阈值按指标本身单位输入，比例用小数。涉及销量与支付的交叉条件是在共同分组上的指标组合，不对应同一批订单。
- **比率**沿用工具在目标粒度聚合的分子和分母，校验结果与二者一致。零分母保留 NULL。需要总计时重新查询总计粒度，不能把分组百分比平均或将可能重叠的去重人数跨组相加。
- **部分失败**保留成功查询。缺失组与查询失败都保留为空。排序或阈值依赖的整项查询失败时跳过该处理并提示。跨期只计算两侧都有有效值的指标。
- **限制**沿用 2023–2025 年已对账窗口。排行与跨期比较分开请求。复杂同比、逐单明细、原因归因、财务净收入、毛利和真实退款到账需独立业务口径及能力验证。长尾表达仍需补充样例。当前分解的是已定义标准指标请求。

## 评测复现

业务用例见 [business_planning_v1.json](../../tests/fixtures/business_planning_v1.json)，评测入口见 [evaluate_business_planning.py](../../tests/evaluate_business_planning.py)。PostgreSQL 真值侧使用既有 SQL Shed 学习只读账号，运行时指标查询使用专用指标账号。

```sh
PYTHONPATH=src .venv/bin/python tests/evaluate_business_planning.py --mode fixed
PYTHONPATH=src .venv/bin/python tests/evaluate_business_planning.py --mode postgres
PYTHONPATH=src .venv/bin/python tests/evaluate_business_planning.py --mode live
INTENT_GEMINI_TESTS=1 METRIC_QUERY_POSTGRES_TESTS=1 WAREHOUSE_POSTGRES_TESTS=1 \
  PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

固定模式验证计划、工具参数、结果处理与拒绝逻辑。PostgreSQL 模式使用固定计划，调用真实指标服务并与独立 ODS SQL 比较。live 模式同时验证真实 Gemini 和真实工具。两类真实模式的数值检查分别重算变化值、排序和阈值，不采用模拟工具结果。

本轮完整回归 92/92。固定用例 19/19，真实 PostgreSQL 6/6，最终真实 Gemini 6/6。首轮真实 Gemini 为 4/5，其中逐笔明细场景遇到一次模型调用失败。加入有限重试后，最终样本中的 4 个执行场景与 ODS 一致，2 个澄清或拒绝场景均未执行数据库查询。延迟、逐例检查以及 3 个真实计划与查询 Trace 见[评测报告](../../reports/warehouse/business_planning_v1.json)。

## Business Planner v2

| 请求 | 既有路径 | v2 |
|---|---|---|
| 标准指标数值、跨期、排行 | 原 Planner 和指标服务 | 沿用既有候选计划、校验和执行 |
| 两个明确指标或一个多义别名 | 精确实体解析统一标为歧义 | 分别返回 `multiple` 和 `ambiguous` |
| 数值附正式定义 | 固定退货联合查询 | 经过验证的标准计划可以选择附加正式定义 |
| 发布资产及受控动态 SQL | 各自的既有路径 | 沿用既有路径 |

Unified Agent 从明确的定义请求后缀识别联合任务，把数值问题送入原 Planner。最多支持五个正式指标，并继续使用既有步数、时间、维度、筛选和观察截止校验。模型输出仍由程序验证。能力路由通过后，指标服务和 Catalog 精确查找分别返回结果。目录精确查找使用活跃版本、内容哈希、权限及原文校验。

每个子任务记录执行状态、来源和 Trace ID。定义索引缺失时，SQL 数值仍可返回，整体状态为 `partial`，目录子任务为 `unavailable`，当前目录文字带缺少引用的提示。历史为新请求追加 `subtasks`，已有会话和历史兼容读取。

```bash
PYTHONPATH=src .venv/bin/python -m dataquery_agent.unified_agent '查询2025年1月订单量和订单商品明细数，并给出正式定义'
KNOWLEDGE_POSTGRES_TESTS=1 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p test_business_planner_v2.py -v
```

[真实 PostgreSQL 联合查询验收](../../reports/warehouse/business_planner_v2.json)使用固定候选计划、真实指标服务和独立 ODS 查询，并包含模拟目录索引缺失的 Trace。自由组合技术文档和跨任务调度留待后续工作。

## Orchestrator v2 顺序 DAG

已验证的 Business Plan 转成 `metric` 与 `catalog` 两类节点。每个节点包含任务 ID、类型、依赖 ID 和已验证参数。指标节点按时间窗调用既有只读指标服务。每个目录节点依赖同一指标的全部 SQL 窗口，使用既有 Catalog Exact Lookup。固定退货联合查询也进入同一执行层，继续返回原有指标结果字段。

执行前核对任务 ID 唯一性、依赖存在、无环、任务类型与最多 11 个节点的上限。有效图按确定顺序在当前请求内运行。必要依赖失败时，下游标记 `skipped` 并记录 `dependency_failed` 和失败依赖 ID。独立节点继续运行。计划沿用 30 秒启动预算和各工具超时。

历史记录沿用会话 Lease 与幂等回执，并保存计划 ID、Attempt ID、节点依赖和调度位置。Catalog 恢复沿用原有入口、检查点和历史安全校验。SQL 节点成功且满足检查点条件时，恢复会复用已保存的结果，更新待恢复目录节点的 Attempt ID。

```bash
KNOWLEDGE_POSTGRES_TESTS=1 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p test_task_dag.py -v
```

[真实 PostgreSQL 验收与 Trace](../../reports/warehouse/task_dag_v1.json)包含合法计划、受控指标故障和定向恢复。在未来扩展并行或多 Agent 执行时，可沿用节点状态和依赖契约，并为并发新增单独的执行与故障测试。
