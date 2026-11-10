# C批ADS应用视图契约

三个普通PostgreSQL视图位于sqlshed.ads；模型版本1.0.0。固定排他观察截止为源无时区的`2026-01-01 00:00:00`，与DWS一致。对象名及原字段沿用TABLE_DESIGN，不建设新事实表或物化视图，不改Agent。

## 应用、粒度与来源

| 视图 | 唯一粒度 | 业务用途和来源 |
|---|---|---|
| business_overview_day | event_date, channel | 每个自然日/渠道并列展示经营、支付和退货事件。order_day、payment_day、return_day各自先汇总到日渠道，再FULL JOIN。支持跨日加总；不把三个金额相减命名净收入。 |
| payment_anomaly_order | order_id | 查看截止前下单订单的支付尝试、失败及商品金额差异。order_fact关联独立按订单聚合的order_item_fact和payment_attempt_fact，关联均1:1。保留无明细/无支付订单；不只保留异常订单。 |
| product_return_cohort | order_date, channel, product_id, as_of | 按购买批次查看退货明细比例和异常明细。直接复用item_return_cohort_day，关联唯一dim.product取得当前category_id；不拼接退货事件日金额。 |

可查询维度：概览为自然日/月和渠道；支付为下单时间、渠道、订单状态、客户账户与订单ID（不是商品维度或支付事件日专题）；商品退货为下单日/月、渠道、商品、当前品类及固定as_of。月份需要重新汇总基础度量后计算比例。

## 字段与计算

完整列、顺序与类型见[ads_models.json](../../warehouse/ads_models.json)，SQL文件为字段来源的权威定义。

概览沿用订单数、商品净额、尝试数、成功尝试数、成功支付金额、退货记录数和退货记录金额；扩展两个必要质量计数：dq_return_before_order_count和dq_refund_above_item_net_count。前者是退货时间早于下单时间，后者是每条退货金额超过其原明细净额。来自return_day，均按退货事件日归属；同一记录可以同时命中，不应相加作为不同异常记录数。

支付沿用设计列，并增加captured_attempt_count、payment_failed_attempt_rate、has_multiple_attempts：

- 尝试次数不按订单去重；成功仅status=captured，失败仅status=failed。失败率=失败尝试数/全部尝试数，保留两者，零分母NULL；跨订单/时间窗用SUM(失败数)/SUM(尝试数)，不平均行率。
- has_multiple_attempts表示截止前同一订单尝试数>1，可COUNT得到多次尝试订单数；不保证“前次失败后再次支付”，也不推断发起原因。不是成功支付订单数，不宣称总计数减订单数就是重试订单数。
- 商品净额按quantity*unit_price-discount_amount，保留取消单；成功支付金额按captured尝试记录相加，不抵扣退货。
- captured_minus_item_net=成功支付记录金额-商品明细净额；has_amount_mismatch使用NUMERIC精确不等，不增加容差。差异不是欠款或支付损失结论。
- 订单ordered_at<as_of，尝试paid_at<as_of。first/last_payment_at包括失败尝试，只是源paid_at最小/最大值，不是首次/末次成功时间；恰好等于截止的事件排除。提前支付异常仍保留。
- 无尝试时次数和金额0，首次/末次时间及失败率NULL；无明细时商品金额0。无尝试且无明细的订单差异为0，不意味着已付款。

商品退货率=returned_item_count/NULLIF(cohort_item_count,0)，ROUND到8位。分子是as_of之前有过退货的不同原明细；重复退货一次计明细，异常明细亦去重。anomalous_return_item_count为提前、超额或负金额任一标识关联的不同明细数，不能解释为单一异常类别。不同as_of不能求和；本版只有一个观察截止，其他截止交给后续受控DWD查询。

## 时间、NULL和空窗口

概览同一event_date下，订单和销售用ordered_at，支付用paid_at，退货用returned_at，三列来自不同订单集合。支付异常是下单队列与固定观察截止；商品退货是购买批次，不能拿同月退货事件数替代分子。refund_amount仅为退货记录金额，未取得退款到账时间或状态。没有币种/时区，不标人民币/美元，不转换为UTC或北京时间。

概览采用事实实际出现过的日渠道键的并集，某来源缺组时该来源度量补0；整个窗口无事实时0行，不做日历×渠道笛卡尔补零。另两视图没有符合条件的订单/明细时亦0行。应用标量聚合COUNT=0、金额SUM需COALESCE为0、比率NULL。品类和账户属性为当前快照，不是历史SCD2。

## 执行与安全边界

DDL在sql/warehouse/ddl/ads，SELECT在sql/warehouse/ads；Python仅编排与验证。没有CREATE OR REPLACE/DROP/TRUNCATE刷新。首次3视图创建、版本注释、全行/窗口对账在一个REPEATABLE READ事务中完成；共享既有批次锁。现有目标必须为相同列/版本、相同定义指纹和security_barrier选项的普通视图，否则拒绝替换。没有物理主键，联合粒度由验收查询检查NULL和唯一性。

视图注释保存渲染SQL和PostgreSQL规范定义SHA，支持检测代码变化及数据库中被替换的定义；业务SQL全部版本化。普通视图反映当前来源内容，不是冻结的历史快照；查询服务上线前必须绑定同源发布并验证版本，不能把as_of当源数据入库可知时间。

入口先只读复核DWS全部26项指标和依赖，再创建ADS；构建前后核对7ODS、4DWD、5DIM、6DWS的22个完整内容指纹。读取账号只在验收后显式获ads USAGE及这3个视图SELECT，无默认授权、写入或角色属性变更。

指标目录继续保留26项已验证、4项待验证、8项unsupported。ADS字段有专门数据验收不等于4项待验证指标已经具备任意窗口/维度的服务能力，本版不升级它们。

## 后续查询服务接口（仅设计）

请求应包含application/metric_id、时间范围及其time_basis、授权维度与过滤、as_of、release_id。服务按契约路由，优先已验收DWS/ADS；部分日、不同as_of或缺预聚合的已定义指标走受控DWD/DIM，口径不明先澄清，unsupported明确拒绝。支付失败原因按授权的尝试明细另查，不能在本订单视图按支付方式任意拆分商品金额。

响应分别返回经营事件块、支付订单队列块、商品退货队列块；每块携带口径、源对象、时间语义、观察截止、分子分母、NULL说明、异常告警、来源版本和Trace。无论页面是否并列展示，不能把块之间的日期标签当同一订单集合直接相减或JOIN。Agent只读，仍无构建权限。本轮未实现服务、API、会话状态或Agent集成。
