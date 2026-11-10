# SQL Shed 电商指标 v1.0.0

本轮只交付指标规格、标准 SQL、真实只读验证结果与模型建议，没有创建 DWD/DWS 实体表。SQL Shed 是官方合成练习数据。此版本是分析口径契约，不代表财务确认或业务方已签署的最终口径。

## 使用方式与共同规则

机器定义：[ecommerce_v1.json](../metrics/ecommerce_v1.json)，每项包含名称、定义、字段、粒度、公式、事件时间、状态、去重、NULL/异常、可用性、限制和 SQL 标识。共同规则在 `defaults`，须与单项一起读取。

标准 SQL：[ecommerce_v1.sql](../sql/metrics/ecommerce_v1.sql)，用 `-- metric: metric_id` 定位，可由 `read_queries` 加载；参数以 psycopg 字典绑定，不拼接用户输入。验证结果：[ecommerce_metrics_v1.json](../reports/ecommerce_metrics_v1.json)，包括 PostgreSQL 实际字段、类型、约束、质量和三个窗口结果，仅含汇总及元数据。

```sh
PYTHONPATH=src .venv/bin/python scripts/validate_ecommerce_metrics.py --check
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

需本地 PostgreSQL 与已忽略的 `.env` 中 POSTGRES_USER/POSTGRES_PASSWORD；地址 127.0.0.1:5433，库 sqlshed。校验器通过数据库强制 READ ONLY + REPEATABLE READ 保证同一快照，只执行仓库内版本化 SELECT，不调用 Gemini/SQL Guard。`--check` 比对已提交基线；有意更新快照时先审查差异，再用 `--output reports/ecommerce_metrics_v1.json` 写报告。执行时间按真实 UTC 记录。

时间窗均为 `[start,end)`。源时间是无时区 TIMESTAMP，不假定 UTC/北京时间；支付和退货使用各自事件时间，不套用订单日期。币种字段缺失，金额只能标为“源金额单位”。空窗计数/金额为 0；零分母比率为 NULL，比率按 0..1 输出并四舍五入到 8 位小数。金额一直使用 NUMERIC，不经浮点数。必填金额缺失不补零；主键、关系、必填时间和金额质量不合格则不接受 Ground Truth。业务异常保留且单独报告。

## 真实字段、关系与风险

四张事实源表的全部字段（ID/数量为 INTEGER，文本为 VARCHAR，布尔为 BOOLEAN，金额为 NUMERIC(12,2)，时间为 TIMESTAMP WITHOUT TIME ZONE）：

| 表及粒度 | 字段；`?` 表示可空 |
|---|---|
| orders：order_id 主键 | order_id, customer_id, ordered_at, shipped_at?, status, channel, shipping_country, coupon_code? |
| order_items：order_item_id 主键 | order_item_id, order_id, product_id, quantity, unit_price, discount_amount |
| payments：payment_id 主键 | payment_id, order_id, method, amount, paid_at, status, failure_reason? |
| returns：return_id 主键 | return_id, order_item_id, returned_at, reason, refund_amount, restocked |

必要维度字段：customers.customer_id → orders.customer_id；customers 的 signup_date 为 DATE、loyalty_tier 可空、marketing_opt_in 为 BOOLEAN。products.product_id → order_items.product_id，产品含 sku、product_name、category_id、当前 unit_price/unit_cost 和 DATE discontinued_at?；categories 含 category_id、category_name、parent_category_id?。完整字段类型见报告。渠道直接来自 orders.channel，没有独立源渠道表。

实测状态：订单 cancelled 5407、delivered 64897、processing 5362、returned 4399、shipped 9935；支付 captured 89748、failed 5252。渠道 mobile 27978、partner 6325、phone 3499、web 52198；支付方式 bank_transfer/card/gift_card/paypal。新增状态必须审查定义，不自动猜测成功含义。

7 张表主键唯一且非空；7 条关系无孤儿。orders → items/payments 均一对多；items → returns 当前至多一条，但标准 SQL 不依赖这一偶然基数。categories 自关联根节点可空，未声明 FK，实际关联检查通过。

60227 个订单有多明细，5230 个订单有多支付。直接 JOIN items/payments/returns 后出现 227035 行，商品净额变成 9515423.04、成功支付额变成 29047100.55、退货金额变成 526408.81，均非正确基线。正确做法是独立聚合各事实后按目标粒度连接，不能用 SUM(DISTINCT amount)“修复”（不同事实也可能同金额）。

订单时间范围 2023-01-01 01:38:09 至 2025-12-31 23:39:43；shipped_at 有 10769 个 NULL，发货最晚到 2026-01-04；其他必填事件时间均有效。loyalty_tier 有 5296 个 NULL、discontinued_at 有 1847 个 NULL、coupon_code 有 70285 个 NULL；可空维度不能用 INNER JOIN/过滤 NULL 偷删事实。全部金额两位小数，无 NULL、负数和非有限值。

## 八项最终口径与 Ground Truth

全量窗口 `[2023-01-01,2026-01-01)`，退货率观察截止 `as_of=2026-01-01`，均为源无时区时间。

| metric_id / 中文名称 | 公式、状态和时间归属 | 全量值 |
|---|---|---:|
| order_count / 订单量 | COUNT(orders)，按 ordered_at，全部状态（含取消） | 90000 |
| item_net_sales_amount / 折扣后商品销售金额 | SUM(quantity×明细 unit_price−discount_amount)，按订单 ordered_at，全部状态；整行折扣只减一次 | 9024059.76 |
| payment_attempt_count / 支付尝试次数 | COUNT(payments)，按 paid_at，含失败和每次重试 | 95000 |
| captured_payment_amount / 成功支付金额 | SUM(payments.amount)，按 paid_at，仅 captured，不筛订单状态 | 8998225.89 |
| payment_attempt_success_rate / 支付成功率 | 同支付事件窗 captured 次数 / 全部尝试次数 | 89748/95000 = 0.94471579 |
| return_record_count / 退货记录数 | COUNT(returns)，按 returned_at，不筛 restocked 或订单状态 | 12000 |
| return_record_amount / 退货记录金额 | SUM(refund_amount)，按 returned_at，无退款成功状态可筛 | 498266.03 |
| item_return_rate / 商品明细退货率 | 同一下单批次、截至观察日存在退货记录的不同明细数 / 批次全部明细数 | 12000/215000 = 0.05581395 |

前 7 项是可用的源记录统计；销售金额不等于支付额，也没有资格命名为“退款后收入”。支付成功按 captured 代理，不能保证银行最终结算；failed.amount 的 517197.15 不是成功支付额。商品现价不参与历史销售额。退货没有数量、退款完成状态/时间或支付流水关联，因此不能计算退货件数、已完成退款额或严谨的财务净收入。

商品明细退货率为“有条件可用的源记录关联率”：分母保留所选下单批次全部明细（包括取消单明细）；分子用 EXISTS，截断 `returned_at < as_of`，每条明细最多计一次。要求 `as_of >= end`。不以退货月份归属分母，不按商品件数加权；跨期汇总应合并分子分母，不能平均月率。跨观察日不可累加；最近批次观察不成熟，不适合直接比较表现。

已知 5962 条退货早于下单，1849 条退款高于明细净额。本版本不悄悄剔除这些记录，所以退货率不等价于“有效退货率”，退货金额不等价于“可信退款额”；指标输出必须带此限制。另一个清洁口径如需剔除异常，应另起 metric_id 和版本，不能覆盖本基线。as_of 仅按当前快照的事件时间过滤，源没有入库时间，不能重建“当时系统已经知道什么”。

2025-01 月标准 SQL 实测：订单 2312、商品销售金额 231920.18、支付尝试 2438、成功支付 230864.57、成功率 2304/2438=0.94503692；当月退货记录 329、退货金额 12833.47；同月下单批次截至 2026-01-01 的退货明细率是 **314/5538=0.05669917**，不是 329/5538。空窗所有计数/金额为 0、两项比率均为 NULL。

## 第一版最小模型建议（尚未创建）

DWD 保留四种独立事实，不建立把订单、明细、支付和退货摊平的宽事实表。以下订单关联同时提供 customer_id、channel，商品关联通过 product_id/category_id；状态都是当前源快照而非状态变更历史。

| 规划事实表 | 粒度 / 主键 / 关联 | 金额 | 事件时间 |
|---|---|---|---|
| dwd.order_fact | 一订单 / order_id；customer_id→客户，channel→渠道 | 无，避免在多事实间复制后误加 | ordered_at；shipped_at 可空 |
| dwd.order_item_fact | 一订单明细 / order_item_id；order_id→订单，product_id→商品；继承下单日期、渠道用于汇总 | unit_price, discount_amount NUMERIC(12,2)，quantity；item_net_amount 用不受 12 位限制的 NUMERIC 派生 | 继承 ordered_at，没有独立明细创建时间 |
| dwd.payment_attempt_fact | 一支付尝试 / payment_id；order_id→订单，继承渠道；保留 method/status | amount NUMERIC(12,2)，不去重重试 | paid_at（源事件时间） |
| dwd.return_record_fact | 一退货记录 / return_id；order_item_id→明细，派生 order_id/channel | refund_amount NUMERIC(12,2)，保留 restocked 和 reason | returned_at，保留 ordered_at 用于质量标记 |

源异常增加 `return_before_order`、`refund_above_item_net` 等质量标记，保留原金额和原事件时间，不能通过 DWD 改写历史。每个事实主键唯一，金额派生与汇总用足够精度 NUMERIC；对必填缺失和孤儿阻断加载，不补造业务事实。

首批只需共享 dim_date（date 主键，被订单/支付/退货日期扮演不同角色）和 dim_channel（channel_code 主键，来自四个源枚举，显示名称不冒充官方名称）。客户、商品/品类本轮没有指标分组依赖，可暂沿 ODS 关联验证；待需要客群、商品或品类分析时再建 dim_customer_current（customer_id）和 dim_product_current（product_id，关联 category_id→categories.category_id；类别名/父类别可展开）。当前快照不能伪装为 SCD2 历史，不用现价重算旧明细。支付状态、订单状态、method 先保留事实编码，不为 8 项指标额外建维度表。

| 规划 DWS 表 / 联合主键 | 内容及关联 | 日期与金额 |
|---|---|---|
| order_day_channel / (order_date,channel) | 各自从订单和明细聚合后按日期/渠道合并：订单数、明细数、商品净额 | ordered_at 日期→dim_date；净额 NUMERIC |
| payment_day_channel_method / (payment_date,channel,method) | 尝试数、captured 尝试数、captured 金额；比率现算 | paid_at 日期→dim_date；成功金额 NUMERIC |
| return_day_channel / (return_date,channel) | 退货记录数、退货记录金额；按明细→订单唯一关联渠道 | returned_at 日期→dim_date；退货金额 NUMERIC |
| item_return_cohort_day / (order_date,channel,as_of) | 下单批次明细数、截至观察日有退货的不同明细数；保留分子分母及异常计数 | ordered_at 日期归属，as_of 为排他观察截止；无金额 |

DWS 的 channel→dim_channel；method 留作事实编码。NULL 分组使用明确未知成员，不能删行（当前渠道和事件时间非空）。同一 as_of 的日批次可汇总；一个批次多个 as_of 只能选同一观察快照，不能相加。订单/商品净额可按下单日加总，支付和退货必须按各自事件日加总，三个金额不得直接拼成“净收入”。

## 实现边界、复盘与下一步

`metrics.py` 只加载命名 SQL 和校验无时区窗口；`validate_ecommerce_metrics.py` 只读执行 SQL、验证结构质量、输出及比对基线。测试用独立内存合成数据验证取消订单、行折扣、支付重试、跨月事件、多退货去重、截止边界和空分母，绝不作为真实业务数据。

已实际执行全部 29 项单元测试（新增 6 项）并通过，PostgreSQL 只读 `--check` 基线复核通过；事务只读状态为 on。本轮无新增依赖，不更改 SQL Guard/Gemini，不操作 Olist，不创建数仓表。本次为一个完整指标规格里程碑，8 份结构化定义、8 条标准 SQL、质量查询和真实汇总报告使总变更超过通常 200 行；该例外用于保证定义和可验证证据同时交付。

面试可讲：先声明每个指标在数什么、用哪个事件时钟，再控制事实粒度；订单销售金额、支付尝试和退货是三个独立事实，退货率要把分子拉回同一订单批次，并固定观察时点。能算出数字并不意味着数字具备财务或业务解释资格。

待业务确认：取消单是否纳入另一个“有效销售”指标、税费运费与折扣分摊、币种、源时区、captured 的结算含义、退货时间异常处理及统一观察期。下一步最小任务：仅实现并验证 dwd.order_item_fact 的粒度和金额派生契约，其余表继续保持规划。
