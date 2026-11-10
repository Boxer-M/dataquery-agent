# 表清单与字段级设计

下列为逻辑名称；A批4张DWD/5张DIM及B批6张DWS已实现，C批3个ADS普通视图已实现。所有表属于独立sqlshed库。PK、NN、? 分别表示主键、非空、可空；未标?的字段均NN。整数源键INTEGER，计数BIGINT，源金额M=NUMERIC(12,2)，派生/汇总金额N=不限精度NUMERIC，事件T=TIMESTAMP WITHOUT TIME ZONE，技术时间TZ=TIMESTAMPTZ。不存在币种字段，不添加猜测值。实际列/约束以warehouse/models.json及SQL为准，验收见BATCH_A_ACCEPTANCE。

发布级 source_snapshot_id、release_id、code_commit、model_version、metric_version、构建时间与对象映射放发布清单，适用于整个不可变批次，不在每张表重复填一套无业务意义字段。清单不包含密码、源客户明细或绝对个人目录。

## ODS：原样保留 7 表

字段、类型、空值与约束的权威版本为 `sql/load_sqlshed_ods.sql` 和 `reports/ecommerce_metrics_v1.json`，本设计不改变它们。

| 表 | 源 PK | 当前行数 | 关键关系 |
|---|---|---:|---|
| ods.categories | category_id | 24 | parent_category_id?→自身 |
| ods.customers | customer_id | 12000 | 账户，不等于自然人 |
| ods.products | product_id | 2000 | category_id→categories |
| ods.orders | order_id | 90000 | customer_id→customers |
| ods.order_items | order_item_id | 215000 | order_id→orders，product_id→products |
| ods.payments | payment_id | 95000 | order_id→orders |
| ods.returns | return_id | 12000 | order_item_id→order_items |

## DIM：5 张共享当前维度

| 表 / PK / 粒度 | 全部字段与类型 | 来源、关联与规则 |
|---|---|---|
| dim.customer / customer_id / 账户 | customer_id INTEGER PK；city VARCHAR；country VARCHAR；signup_date DATE；loyalty_tier VARCHAR?；marketing_opt_in BOOLEAN | ods.customers 同名字段；保留未入会 NULL；first_name/last_name/email 留在 ODS，不复制到通用查询维度；不按 email 去重 |
| dim.product / product_id / 商品 | product_id INTEGER PK；sku VARCHAR；product_name VARCHAR；category_id INTEGER；current_unit_price M；current_unit_cost M；discontinued_at DATE? | ods.products.unit_price/unit_cost 仅重命名明确“当前”；category_id→dim.category；不可用当前成本算历史毛利 |
| dim.category / category_id / 品类 | category_id INTEGER PK；category_name VARCHAR；parent_category_id INTEGER? | ods.categories，父类→自身；根 NULL 合法；v1不提前存多套类别路径，递归分析需循环检测 |
| dim.date / date_value / 自然日 | date_value DATE PK；calendar_year INTEGER；calendar_month INTEGER；month_start DATE；iso_week_year INTEGER；iso_week INTEGER；iso_weekday INTEGER | 从所有源有效 DATE/T 时间最小日到最大日逐日生成，含 shipped_at 超出下单范围的日期；ISO 周年与日历年分列；无来源的节假日/交易日不生成 |
| dim.channel / channel_code / 渠道编码 | channel_code VARCHAR PK | SELECT DISTINCT orders.channel，当前 web/mobile/partner/phone；不编造中文官方渠道名；未来新编码保留并提示审查 |

DIM 仅当前快照，不伪装 SCD2；同一发布内键唯一。order_fact.customer_id→dim.customer，order_fact.channel→dim.channel；order_item_fact.product_id→dim.product→dim.category；支付/退货经唯一订单取得客户与渠道。事实事件日期 CAST(... AS DATE)→dim.date，未知合法维度属性按 NULL 分组，不因内连接属性表丢事实。

## DWD：4 张独立事实

### dwd.order_fact（已实现，一订单，PK order_id）

| 字段 | 类型 | 映射 |
|---|---|---|
| order_id | INTEGER PK | ods.orders.order_id |
| customer_id | INTEGER | 原值→dim.customer |
| ordered_at | T | 原下单时间 |
| shipped_at | T? | 原发货时间，未发货/取消可空 |
| status, channel, shipping_country | 各 VARCHAR | 同名原值；channel→dim.channel |
| coupon_code | VARCHAR? | 同名原值 |
| dq_shipping_before_order | BOOLEAN | shipped_at < ordered_at；NULL发货返回 FALSE，不改原时间 |
| dq_unknown_order_status | BOOLEAN | 非五种已知源状态 |

源→目标 1:1，保留全部 90000 订单，不能因没有明细、支付、退货而漏单。没有订单金额字段，不从多事实宽连接复制金额。

### dwd.order_item_fact（已存在，一明细，PK order_item_id）

原 14 列及类型保持不变：order_item_id INTEGER PK；order_id/product_id/quantity 各 INTEGER；unit_price/discount_amount 各 M；net_item_amount N；order_purchase_time T；order_status VARCHAR；dq_nonpositive_quantity/dq_negative_amount/dq_discount_exceeds_gross/dq_unknown_order_status/dq_invalid_order_time 各 BOOLEAN。

除 PK 外均 NN。净额 quantity*unit_price-discount_amount；order_purchase_time/order_status 来自源订单。order_id 逻辑关联 order_fact；product_id→dim.product。首批只读验证既有表，不 ALTER、不重命名、不扩列、不覆盖。需要 customer/channel 时通过唯一 order_fact 取得。新发布如沿用该模型也保持同样列名与公式。

### dwd.payment_attempt_fact（已实现，一尝试，PK payment_id）

| 字段 | 类型 | 映射 |
|---|---|---|
| payment_id | INTEGER PK | ods.payments.payment_id |
| order_id | INTEGER | 原值→order_fact |
| method | VARCHAR | 原支付方式 |
| amount | M | 原尝试金额，failed 不当作成功支付 |
| paid_at | T | 原事件时间，失败尝试也保留该字段名 |
| status | VARCHAR | 原 captured/failed，不推导银行结算状态 |
| failure_reason | VARCHAR? | 原值；成功时 NULL 合法 |
| dq_payment_before_order | BOOLEAN | paid_at < 对应订单 ordered_at |
| dq_negative_amount | BOOLEAN | amount < 0 |
| dq_unknown_payment_status | BOOLEAN | status 不在 captured/failed |

唯一订单关联只用于验证/标记，不按 order_id 去重支付尝试，不跨商品分摊，保留 95000 行。没有退款完成字段，不添加虚构时间。

### dwd.return_record_fact（已实现，一退货记录，PK return_id）

| 字段 | 类型 | 映射 |
|---|---|---|
| return_id | INTEGER PK | ods.returns.return_id |
| order_item_id | INTEGER | 原值→order_item_fact |
| returned_at | T | 原事件时间 |
| reason | VARCHAR | 原退货原因 |
| refund_amount | M | 源退货记录金额，不截断 |
| restocked | BOOLEAN | 原值，不当作退款成功状态 |
| order_id, product_id | 各 INTEGER | 从唯一源明细继承，分别→order_fact/dim.product |
| dq_return_before_order | BOOLEAN | returned_at < 对应订单时间 |
| dq_refund_above_item_net | BOOLEAN | refund_amount > 对应明细 quantity*unit_price-discount_amount |
| dq_negative_amount | BOOLEAN | refund_amount < 0 |

保留 12000 行；不能声明 order_item_id UNIQUE 来固化当前“每明细最多一退货”的偶然分布。无退货数量字段，不推导 unit_return_rate。当前 5962/1849 个前两类异常标记必须保留。

## DWS：6 张主题表（已实现，仅日级）

计数与金额均 NN；不存预先平均后的率。下列日期为 DATE、channel 为 VARCHAR、product_id/customer_id 为 INTEGER、status/method 为 VARCHAR；金额 N、计数 BIGINT。字段列表即全量业务列。

| 表 / 联合 PK 与粒度 | 全部度量字段 | 加工和可加性 |
|---|---|---|
| dws.order_day / (order_date,channel,status) | order_count、order_item_count、ordered_unit_count、item_gross_amount、item_discount_amount、item_net_sales_amount | order_fact 先独立计订单；明细经唯一订单取日期/渠道/状态，独立汇总再按同键合并。无明细订单金额 0。月度可加；不是支付或退货日期 |
| dws.product_sales_day / (order_date,channel,product_id) | order_item_count、ordered_unit_count、item_gross_amount、item_discount_amount、item_net_sales_amount | 明细→订单→当前商品；按 product 聚合，品类由同发布唯一 product.category_id 上卷。不存可被错误跨商品求和的 distinct order_count |
| dws.payment_day / (payment_date,channel,method,status) | payment_attempt_count、attempt_amount、dq_payment_before_order_count、dq_negative_amount_count | 尝试→唯一订单，仅用 paid_at 日期；captured 行 amount 才能路由成功金额，failed 的 attempt_amount 为失败记录金额而非支出/损失。distinct订单数仍动态计算 |
| dws.return_day / (return_date,channel,product_id) | return_record_count、return_record_amount、dq_return_before_order_count、dq_refund_above_item_net_count、dq_negative_amount_count | 退货→唯一明细→唯一订单；returned_at 日期。金额和记录数可加，不把记录数当明细退货率分子 |
| dws.item_return_cohort_day / (order_date,channel,product_id,as_of) | cohort_item_count、returned_item_count、anomalous_return_item_count | as_of 为 T、排他截止；分母该下单日全部明细；分子 EXISTS(returned_at<as_of)，异常明细数亦用 EXISTS 防重复；同一 as_of 下跨日/商品可加，跨 as_of 不可相加 |
| dws.customer_order_day / (order_date,customer_id,channel) | order_count、order_item_count、item_net_sales_amount | 订单与明细按同键独立聚合；保留无明细订单。跨期/渠道按 customer_id 重新聚合，再判 >=2，不能加每日复购人数 |

所有 channel→dim.channel；日期→dim.date；商品→dim.product→dim.category；客户→dim.customer。源订单状态属于当前源快照，汇总不宣称“当日时点历史状态”。customer_order_day 没有支付日期或“支付买家”语义。各表仅在有事实的组产生行，空日期补零由查询层/ADS 日历连接完成，率的 0 分母仍 NULL。

cohort 首次只预聚合已批准 as_of=2026-01-01 的样本窗口；未来请求不同 as_of 时由受控 DWD 计算，不近似取最近快照。不为任意未来日期预建大量重复 cohort 分区。

## ADS：C批3个普通视图（已实现）

| 视图 / 粒度 | 全部业务字段及类型 | 逻辑依赖与使用边界 |
|---|---|---|
| ads.business_overview_day / (event_date DATE,channel VARCHAR) | order_count BIGINT、item_net_sales_amount N、payment_attempt_count BIGINT、captured_attempt_count BIGINT、captured_payment_amount N、return_record_count BIGINT、return_record_amount N、dq_return_before_order_count BIGINT、dq_refund_above_item_net_count BIGINT | order_day/payment_day/return_day 各自先归并到日渠道后 FULL JOIN；列分别表示下单日、支付日、退货日。独立列不能解释同批订单收支；不存“净收入”或日 distinct 买家用于月累计 |
| ads.payment_anomaly_order / order_id INTEGER | ordered_at T、customer_id INTEGER、channel VARCHAR、order_status VARCHAR、item_net_amount N、payment_attempt_count BIGINT、failed_attempt_count BIGINT、captured_payment_amount N、captured_minus_item_net N、has_amount_mismatch BOOLEAN、first_payment_at T?、last_payment_at T?、as_of T、captured_attempt_count BIGINT、payment_failed_attempt_rate NUMERIC?、has_multiple_attempts BOOLEAN | order_fact LEFT JOIN 按订单预聚合的明细和尝试，都是1:1；as_of 固定为该发布观察截止，过滤尝试 paid_at<as_of、订单 ordered_at<as_of；无记录金额/次数按0。仅用于当前快照订单队列差异，非“支付失败损失/未付欠款”结论；不同截止走动态SQL |
| ads.product_return_cohort / (order_date DATE,channel VARCHAR,product_id INTEGER,as_of T) | category_id INTEGER、cohort_item_count BIGINT、returned_item_count BIGINT、anomalous_return_item_count BIGINT、item_return_rate NUMERIC? | cohort DWS→唯一当前 product；率 ROUND(num/NULLIF(den,0),8)。退货事件金额另查 return_day，接口分结果块返回，禁止把事件月退货金额塞进同月下单队列当净额 |

ADS 结果中的 as_of 与来源清单一起输出，不把视图当前结果视作历史快照。支付异常仅限账户ID，失败原因按授权的 payment_attempt_fact 明细聚合另查；不在 ADS 暴露姓名/email。商品退货队列不依赖支付事实。

C批三个新增支付字段与两个概览退货质量计数的业务语义、空窗及安全边界见[ADS_CONTRACTS](ADS_CONTRACTS.md)。视图无物理PK，以上为验收唯一键；DDL和SELECT均版本化。
