# 自然语言指标解析

中文业务问题可以通过 CLI 或 Python 查询已验证的经营指标。Gemini 提取原文中的指标候选、时间、分组和筛选，并标记复杂问题或缺失要素。解析器核对这些原文片段，从指标目录解析 ID，再用指标服务校验可用性、窗口、维度和筛选。查询工具仅接收校验通过的结构化参数。遇到“销售额”“退款金额”等表达时，回复候选口径的业务含义及可用状态。

模型可以提取条件，业务含义由目录、同义词映射和服务的支持矩阵决定。例如“下单笔数”映射到“订单量”，保留包含取消订单的定义。“已支付订单量”请求的状态口径不同，现有指标路径会拒绝该条件。涉及多个指标、对比、归因或排行时，返回需要拆分的问题。

## 调用

本地环境需要既有 Gemini 配置和 [指标服务只读登录](METRIC_QUERY_SERVICE.md)。

```sh
PYTHONPATH=src .venv/bin/python -m dataquery_agent.intent_semantic_resolver \
  '2025年1月的商品明细退货率'
```

Python 调用使用 `SemanticResolver(service).answer(question, generate)`。CLI 将 JSON 输出到标准输出。查询成功返回 `request` 和 `query_result`，保留定义、窗口、来源、分子分母及 Trace ID。澄清返回 `clarify` 和原因，存在多口径时附候选；能力不足返回 `unsupported`；模型及工具故障返回 `error`。工具返回的结构化错误保存在 `query_result`。

CLI 实跑得到 `item_return_rate`，请求窗口为 `[2025-01-01, 2025-02-01)`，观察截止为 `2026-01-01`。真实 PostgreSQL 返回 `314 / 5538 = 0.05669917`，来源是 `dws.item_return_cohort_day` 和 `dim.product`，Trace ID 为 `1fbc85f12ca7415b90dae2a0abfa348e`。

## 支持范围

时间支持完整年份、年月、ISO 日期及起止日期，以及今天、昨天、本月、上月、今年、去年。相对时间按 Asia/Shanghai 的日历解释。自然语言“从某日到某日”包含结束当天，查询时转换为右开窗口。入口限定在已对账的 2023 年至 2025 年内，超出窗口提示可查询日期。退货率沿用指标服务的发布观察截止。

分组和筛选沿用指标服务的约束。现有词表识别月、渠道、品类、商品等明确分组，以及已知渠道和明确的品类 ID。未支持的分组组合、筛选或模糊业务词返回说明。当前只接受一次单指标查询。长尾表达需补充用例及同义词或条件识别规则。比较和归因需要独立业务验证后再设计，澄清回复可由用户在下一条独立问题中重新表述。

## 测试证据

| 验证层 | 结果 |
|---|---|
| 版本化业务验收 | 19/19，固定模型片段验证匹配、澄清、限制和失败路由 |
| 本地单元与业务测试 | 4/4，含模型和工具异常、已知条件遗漏及时间截断检查 |
| PostgreSQL 集成 | 3/3 场景，固定解析输入、真实只读查询，与对应结构化服务请求对照 |
| Gemini 端到端 | 4/4 场景，真实 Gemini 解析，覆盖订单量、销售额澄清、渠道分组和退货率；其中 3 个调用真实 PostgreSQL |
| 完整回归 | 86/86，包含既有 80 项和新增 6 项测试方法 |

固定模型片段的业务验收只断言请求与控制流，不提供数据库真值。数据库参考对账沿用指标查询服务的既有 ODS 测试。真实 Gemini 成功率来自本轮 4 个场景，模型版本或表达变化需要重新验证。

```sh
INTENT_GEMINI_TESTS=1 METRIC_QUERY_POSTGRES_TESTS=1 WAREHOUSE_POSTGRES_TESTS=1 \
  PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

实现见 [语义解析和 CLI](../../src/dataquery_agent/intent_semantic_resolver.py)、[时间归一化](../../src/dataquery_agent/intent_time.py)、[业务用例](../../tests/fixtures/intent_business_v1.json)和[测试](../../tests/test_intent_semantic_resolver.py)。
