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
