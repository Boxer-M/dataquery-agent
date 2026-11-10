# 指标目录、可用性与血缘

机器契约为 `metrics/warehouse_catalog_v1.json`（1.1.0-dws）：38项，原批准8项不变，18项限定DWS范围available，4项明细查询待验证，8项unsupported不变。每项记录 ID/中文名、定义/公式、粒度、事件/统计归属、状态、去重、维度、源依赖、是否已有汇总、拟用汇总、动态 SQL 能力、质量限制与独立 Ground Truth 方法。

**原8项保持不变**：目录复制 `metrics/ecommerce_v1.json` 的全部原字段，并记录原文件 SHA-256；原定义文件和 `sql/metrics/ecommerce_v1.sql` 均不修改。目录增加的路由元数据不是 Agent 已实现能力。18项扩展已有独立SQL/真实PostgreSQL验收，路径及312组比较见DWS_ACCEPTANCE。4项defined_unverified不作为可答指标；查询服务/Agent仍未接入。

## 原批准 8 项（已验证，不改变默认过滤）

| metric_id | 名称 / 全量已验证值 | 事实时钟 / 拟用汇总 |
|---|---|---|
| order_count | 订单量 90000 | 下单日 / order_day |
| item_net_sales_amount | 折扣后商品销售金额 9024059.76 | 下单日 / order_day、product_sales_day |
| payment_attempt_count | 支付尝试次数 95000 | paid_at / payment_day |
| captured_payment_amount | 成功支付金额 8998225.89 | paid_at，仅 captured / payment_day |
| payment_attempt_success_rate | 支付成功率 89748/95000=0.94471579 | paid_at，成功尝试/全部尝试 / payment_day |
| return_record_count | 退货记录数 12000 | returned_at / return_day |
| return_record_amount | 退货记录金额 498266.03 | returned_at / return_day |
| item_return_rate | 明细退货率 12000/215000=0.05581395 | 下单队列+as_of / item_return_cohort_day |

窗口 [2023-01-01,2026-01-01)，退货队列 as_of=2026-01-01。包含取消单和源异常；金额不标币种。退货率是记录关联率，不是有效售后件数率；禁止将当月退货记录数/当月明细数替代它。

## 原22项扩展的本批处理（18通过，4待明细实现）

| ID / 中文名称 | 计算与时钟 | 血缘与拟用汇总 |
|---|---|---|
| order_item_count / 商品明细数 | COUNT(明细)，下单时间 | items→order_item_fact→order_day/product_sales_day |
| ordered_unit_count / 下单商品件数 | SUM(quantity)，下单时间，含异常数量 | 同上 |
| item_gross_sales_amount / 折扣前商品记录金额 | SUM(quantity*明细单价)，下单时间 | 同上 |
| item_discount_amount / 商品行折扣金额 | SUM(discount_amount)，下单时间 | 同上 |
| cancelled_order_count / 取消状态订单数 | 下单窗内当前 status=cancelled 订单数，无取消时钟 | orders→order_fact→order_day |
| cancelled_order_rate / 取消状态订单占比 | 同窗取消订单/全部订单，零分母NULL | 同上，合并分子分母 |
| average_order_record_amount / 每单商品记录金额 | 明细先按订单净额汇总 / 全部订单数，含无明细订单 | orders+items→两事实→order_day |
| payment_failed_attempt_count / 失败支付尝试次数 | paid_at窗内 failed 数 | payments→payment_attempt_fact→payment_day |
| payment_failed_attempt_rate / 失败支付尝试率 | 同支付窗 failed/全部尝试，不硬取1-成功率 | 同上 |
| payment_attempted_order_count / 有支付尝试订单数 | paid_at窗内 DISTINCT order_id | payment_attempt_fact 动态，不能累加日去重 |
| multiple_payment_attempt_order_count / 多次尝试订单数 | paid_at窗内按订单 COUNT>=2 | payment_attempt_fact 动态；不等于失败重试次数 |
| payment_item_amount_mismatch_order_count / 支付商品净额差异订单数 | 下单队列；各事实按订单预聚合，captured 事件<as_of | 三事实→ads.payment_anomaly_order；严格匹配as_of |
| return_before_order_count / 早于下单退货记录数 | returned_at窗内退货时间<下单时间的记录数 | returns→return_record_fact→return_day质量列 |
| return_above_item_net_count / 超明细净额退货记录数 | returned_at窗内 refund_amount>明细净额的记录数 | 同上 |
| ordering_customer_count / 下单账户数 | 下单窗 DISTINCT customer_id，全部状态 | orders→order_fact→customer_order_day，跨期重新去重 |
| repeat_ordering_customer_count / 窗口内多次下单账户数 | 整个下单窗每账户订单数>=2，全部状态 | 同上，跨日/所选渠道先合并账户 |
| customer_repeat_order_rate / 窗口内账户复购率 | >=2单账户/有订单账户，非自然人复购 | 同上，不平均日率或相加渠道去重人数 |
| first_observed_ordering_customer_count / 首次观测下单账户数 | 全部已知订单至as_of先求账户首单，再判断首单是否在窗内 | order_fact 动态；不能先筛月份；首单同刻按order_id定渠道归因 |
| orders_per_ordering_customer / 下单账户人均订单数 | 全部状态订单数/窗口不同账户数 | customer_order_day 先按账户合并 |
| channel_order_share / 渠道订单占比 | 渠道订单/所有渠道订单，订单时间 | order_day；分母移除渠道过滤但保留授权域 |
| ordered_product_count / 有下单明细商品数 | 下单窗 DISTINCT product_id，不按正数量筛 | product_sales_day 对商品重新去重 |
| category_sales_share / 当前品类商品金额占比 | 类别商品净额/全类别商品净额，订单时间 | product_sales_day→唯一当前 product/category；分母移除商品/类别过滤 |

各指标支持哪些维度见机器目录的 supported_dimensions；这些是可受控计算的维度上限，不表示拟建汇总均包含所有维度。例：订单量按 customer_country_current 可经 customer_order_day+dim.customer 或 DWD 计算；order_day 无 customer_id，不能只 JOIN 客户表硬补分组。payment_method 只适用支付指标，不能用于商品销售额过滤而不声明另一种分析口径。

18项现为has_existing_summary=true，但仅dws_validation明确的日粒度/维度/状态/as_of可用；其余4项仍无预聚合且未验证。路由必须检查能力兼容。channel/product/category 切片优先复用核心金额指标，不另造“渠道销售额/品类销售额”同义 metric_id。原指标固定状态过滤不允许由请求覆盖；显式维度切片需完整回显，未知口径另起澄清。

## Unsupported（不产生估计SQL）

| ID | 原因 |
|---|---|
| completed_refund_amount | 无退款成功状态/完成时间/退款流水 |
| net_recognized_revenue | 缺确认收入、税费运费、币种、退款完成语义 |
| unit_return_rate | 无退货数量 |
| customer_lifetime_value | 缺完整生命周期、利润、身份和预测契约 |
| customer_entity_repeat_rate | 无可靠自然人标识，email 不唯一 |
| historical_gross_margin_amount | 当前成本不是历史成交成本 |
| product_captured_payment_amount | 支付无商品明细分摊规则，多事实JOIN不能替代分摊 |
| currency_converted_sales_amount | 缺币种及汇率 |

## 验证与数据血缘要求

原8项使用现有 ODS 标准SQL与版本化全量/月度/空窗结果，未来 DWD/DWS/ADS 按相同窗口和过滤逐组对比。金额精确相等，不允许用宽容浮点误差掩盖精度丢失。比例比较原始分子/分母再比较四舍五入8位结果。

扩展项独立写 ODS Ground Truth SQL，不能只把生产 DWS SQL 再运行一次当测试；各指标独立验收方法和边界见机器目录 ground_truth。特殊主题必须覆盖：

- 商品：明细毛额−行折扣=净额；同币种未知前提下仅为源单位恒等式；按商品/唯一当前品类上卷，合计回到核心净额。
- 复购：先合并账户整个请求窗再判>=2，构造一个账户跨两天/渠道各1单，证明日/月和渠道人数不可直接相加；有前窗首单账户不能当月“首次观测新客”。
- 支付：一个订单2明细2支付+退货，验证各金额不扇出；distinct订单和多尝试订单必须跨整个请求窗重算。
- 份额：验证分母移除指定轴过滤且保留行级授权，空分母NULL；负金额导致份额超0..1时记录限制，不裁剪。
- 退货：跨月事件、同明细重复退货、异常提前退货、截止时刻等于as_of均按原口径；队列不同as_of严禁合并。

8622下单账户/5376窗口多单账户及复购率0.62352122已在本批独立ODS/DWS对账通过，但不代表自然人或支付客户指标。其余能力严格按目录dws_validation范围，不泛化到所有旧声明维度。产品和客户属性只反映当前源快照；数据血缘必须同时记录 metric_version、model_version、source_snapshot_id、release_id、实际逻辑/物理对象、列和最终SQL哈希。
