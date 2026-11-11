# 元数据管理与能力判断

此入口接受结构化业务需求，返回 `STANDARD_METRIC`、`DETAIL_QUERY`、`NEEDS_CLARIFICATION` 或 `UNSUPPORTED`。例如，支付成功率可复用标准服务；同一订单是否先失败后成功需要支付尝试级时间和订单键，选择 DWD 明细候选。

```sh
PYTHONPATH=src .venv/bin/python -m dataquery_agent.capability_resolver \
  --resolve '{"metric_id":"payment_attempt_success_rate","start_date":"2025-01-01","end_date":"2025-02-01","group_by":["channel"]}'

PYTHONPATH=src .venv/bin/python -m dataquery_agent.capability_resolver \
  --resolve '{"analysis_id":"payment_failure_then_capture","start_date":"2025-01-01","end_date":"2025-02-01","group_by":["channel"]}'

PYTHONPATH=src .venv/bin/python -m dataquery_agent.capability_resolver \
  --model dwd.payment_attempt_fact
```

Python 可用 `CapabilityResolver(CapabilityRegistry(MetadataService.local().snapshot())).resolve(demand)`。请求指定 `metric_id` 或 `analysis_id`，并在可查询路径提供日期窗 `[start_date,end_date)`。可选字段包括 `group_by`、`filters`、`event_time`、`observation_cutoff`、`requested_source`、`joins`、`expected_metadata_version`。`joins` 只是待检查的候选关系，每项包含 `from`、`to`、`from_column`、`to_column`。它无法扩展已批准路径。当前窗口沿用自然语言入口已验收的 2023–2025 年。

## 来源与一致性

1. [Metadata Service](../../src/dataquery_agent/metadata_service.py) 使用已配置的 SQL Shed 学习只读账号，在可重复读事务内查询 PostgreSQL 系统目录。读取列类型、可空性、主键、唯一约束、外键和实际 Schema/列权限。技术清单包括 7 张 ODS 表，系统目录读取不扫描其业务行。
2. [Capability Registry](../../src/dataquery_agent/capability_registry.py) 合并 18 个版本化模型、现有指标注册表和 [能力契约](../../warehouse/capability_contract.json)。实体列和约束与模型清单核对，ADS 粒度来自发布契约并单独标记。标准能力继承指标目录及模板，附加契约只登记必要的时间语义、逻辑关联、质量限制和明细需求。逻辑关联须与实际字段类型和目标唯一约束相符。物理 FK 和逻辑关联分别标记证据来源。
3. [Capability Resolver](../../src/dataquery_agent/capability_resolver.py) 检查请求口径、已验收模板、必要字段、时间、关联和有效权限。标准路径按指标账号权限检查，并返回可交给既有服务的 `metric_request`。明细路径仅返回 `execution_policy: plan_only`、计划所需字段、键和限制。读取元数据所用账号的权限不等同于执行授权。ODS 直读和非登记关联均被拒绝。

注册表从实时结构生成。技术结构、权限快照、契约及 SQL 资产内容分别产生摘要，任一变化会更新能力版本。请求可携带预期版本以拒绝旧缓存。实体类型、列、可空性或约束发生漂移时，仅阻断依赖受影响模型的路径。结构检查覆盖可见约束，视图的业务定义与数据内容验收仍由现有发布流程负责。CLI 展示的 ADS 能力为候选说明，当前标准路由只使用已验证的 DWS 模板。

## 选路示例

| 需求 | 路径及依据 |
|---|---|
| 按渠道的支付成功率 | `STANDARD_METRIC`，`dws.payment_day` 使用支付事件日、状态和尝试计数 |
| 当前品类的商品净额 | `STANDARD_METRIC`，`dws.product_sales_day.product_id → dim.product.product_id`，目标唯一，品类为当前快照 |
| 同单失败后成功 | `DETAIL_QUERY`，`dwd.payment_attempt_fact` 物理主键为 `payment_id`，按订单内 `paid_at` 严格先后判断。按渠道时通过 `order_id` 多对一关联 `dwd.order_fact` |
| 按订单和品类查询商品净额 | `DETAIL_QUERY`，日汇总丢失订单键，选择订单明细并多对一取得品类 |
| 支付金额按商品品类 | `UNSUPPORTED`，同单多个商品品类没有批准的分摊规则，直接关联会重复支付金额 |
| 销售额 | `NEEDS_CLARIFICATION`，明确商品毛额、净额或成功支付金额 |

执行侧后续须单独验证明细 SQL 模板与身份授权，再消费 `DETAIL_QUERY`。当前仅登记两个明细需求，不从任意问题推导关联。时间匹配不判断窗口内是否必有数据。相同订单中出现成功、失败两类状态不足以证明先后。

## 验证

```sh
CAPABILITY_POSTGRES_TESTS=1 INTENT_GEMINI_TESTS=1 METRIC_QUERY_POSTGRES_TESTS=1 \
  WAREHOUSE_POSTGRES_TESTS=1 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

100 项回归全部通过，含 4 项新增单元方法和 4 项真实 PostgreSQL 方法。版本化业务用例 20/20，其中 18 个也用实际 PostgreSQL 结构验证，漂移与撤权通过内存快照注入。26 项标准指标路由逐项核对了实际模型和权限。

2025 年 1 月支付成功率为 2,304 / 2,438，即 0.94503692；按订单和品类的商品净额有 5,014 组，共 231,920.18，ODS 与 DWD 逐组一致。先失败后成功的订单在 2025 年 1 月及全量验证窗口均为 0，ODS 和 DWD 集合一致。首轮测试曾假设结果非空，已按真实事实修正，并以独立合成 CTE 验证正常顺序、同刻、反序和右边界。见[业务用例](../../tests/fixtures/metadata_capability_v1.json)和[实测报告](../../reports/warehouse/metadata_capability_v1.json)。
