# B批：DWS独立建设契约

DWS与ADS分开开发、分开PR。本批只落地以下6张日级DWS；不创建ADS、不改Agent。原8项定义与ODS标准SQL原文件不变，日汇总的适用范围不扩张为任意时刻窗口。

| 表 | 唯一粒度 / 联合PK | 事实来源与事件时间 | 可查询维度及度量 |
|---|---|---|---|
| dws.order_day | order_date, channel, status | order_fact独立计单、order_item_fact独立按同维度汇总后唯一键连接；ordered_at日期 | 日期、渠道、当前订单状态；单数、明细数、件数、毛额、行折扣、净额 |
| dws.product_sales_day | order_date, channel, product_id | 明细→唯一订单；ordered_at日期 | 日期、渠道、商品；经唯一dim.product关联当前品类；明细数、件数、毛额、折扣、净额 |
| dws.payment_day | payment_date, channel, method, status | 支付尝试→唯一订单获取渠道；paid_at日期 | 支付日期、渠道、方式、状态；尝试次数、源尝试金额、提前支付/负额标识计数 |
| dws.return_day | return_date, channel, product_id | 退货事实→唯一订单；returned_at日期 | 退货日期、渠道、商品/当前品类；记录数、记录金额、提前/超额/负额计数 |
| dws.item_return_cohort_day | order_date, channel, product_id, as_of | 全部明细归属下单日，退货先按明细和截止归并再关联 | 下单日、渠道、商品/当前品类、排他观察截止；批次明细数、有关联退货的明细数、异常退货明细去重数 |
| dws.customer_order_day | order_date, customer_id, channel | 订单/明细分别按账户日期渠道聚合后唯一键连接；ordered_at日期 | 日期、账户、渠道；经dim.customer取得当前country/loyalty；单数、明细数、净额 |

显式DDL、类型/空值、联合主键与必要索引见`warehouse/dws_models.json`和`sql/warehouse/ddl/dws/`；业务SELECT在`sql/warehouse/transform/dws/`。跨层关联为校验过的逻辑关系，不对既有ODS/DWD/DIM增删数据或扩列。复用共享执行器并增加联合主键校验，不复制6套Python流程。

## 已澄清的边界

- 所有业务日期从无时区源TIMESTAMP直接取DATE；不假定UTC或北京时间。日汇总只接受自然日对齐的[start,end)，部分日窗口报错，应以后走受控DWD；不改变原8项可用事件时间定义。
- as_of由模型清单显式配置，首版2026-01-01 00:00:00，必须覆盖源下单日范围。此值绑定已存在cohort表；改观察截止会产生内容不一致并拒绝覆盖，不能近似取“最近快照”。它不是历史入库可知时间，也不证明真实售后观察已成熟。
- cohort分母包含取消单明细，分子保留提前退货等源异常；同明细多退货只贡献一次。异常明细数使用该截止前任一退货的时间/超额/负额标识OR，不能加三个记录计数替代明细去重。
- 所有金额分别保留：折扣后商品记录金额、captured成功支付金额、源退货记录金额；不推导退款到账或财务净收入。
- 计数存BIGINT，金额存不限精度NUMERIC；汇总SUM(bigint)在PostgreSQL返回NUMERIC时按精确数值对账，不拿JSON整数与十进制字符串表示差异当业务差异。
- 比率不存百分比，查询时SUM分子/SUM分母并ROUND到8位，零分母NULL。客户复购先把整个请求窗、所选渠道集合按customer_id合并，再判断>=2；不能SUM日复购人数或AVG日率。
- 日表只存有事实的组。全局无数据计数/金额返回0、率NULL；维度无数据返回0行，不默认生成日期×商品×客户笛卡尔网格。captured金额按WHERE先筛captured，失败单独存在的渠道不伪造成功分组。成功/失败率的已验证分组不使用支付状态轴，以保留全部尝试分母。
- cohort中有购买明细但没有退货的组仍须保留，分子为0、分母为实际购买明细数；这与无事实的维度笛卡尔补零不同。无明细订单的订单组也保留订单数和0商品金额。
- 品类/客户属性是当前快照，账户不按email合并，商品当前成本不参与历史销售或毛利计算。

## 指标支持范围

原8项由DWS精确计算、独立ODS SQL及原批准SQL共同核验。新增18项仅在实际验证的DWS维度/过滤/日对齐窗口内标为available，详见`metrics/warehouse_catalog_v1.json`的dws_validation。原supported_dimensions若宽于DWS物理粒度，不等于该汇总已支持全部组合，其他路径仍须受控DWD实现/验收；没有修改Agent路由。

18项为：商品明细数、下单件数、折扣前金额、行折扣金额、取消状态单数/占比、每单商品记录金额、失败支付次数/率、提前退货记录数、超净额退货记录数、下单账户数、窗口多单账户数、窗口账户复购率、账户人均单数、渠道订单占比、有下单明细商品数、当前品类销售占比。

4项保留defined_unverified/not_available_in_dws：有支付尝试订单数、多次尝试订单数、支付商品金额差异订单数、首次观测下单账户数。其定义和来源已明确，不能谎标needs_definition或unsupported；本批未保留支付order_id和首单历史归因等明细粒度，待受控DWD或后续ADS路径独立验证。8项原unsupported逐项保持不变。

## 执行与验证

`PYTHONPATH=src .venv/bin/python scripts/build_dws.py --plan`仅列依赖顺序。正常构建复用A批事务/advisory try-lock；6表创建、填充、ANALYZE、逐行独立ODS比较、指标验收一起提交。已有表只校验；缺依赖、结构/内容冲突、金额或粒度错误立即回滚，不DROP/TRUNCATE/覆盖。`--check-only`强制只读。日表主键即主要日期索引，另按商品、客户与cohort观察截止建立必要索引。

独立模型GT：`sql/warehouse/ground_truth/dws/models.sql`；26项ODS与DWS指标查询：`sql/warehouse/dws_metrics/{ods,dws}.sql`；共同登记：`warehouse/dws_metrics.json`。指标模板的bucket/group_by只由代码白名单生成，值使用绑定参数；这些不是Agent自由SQL入口。

维度验证覆盖渠道、商品、当前品类、支付方式/状态、账户/当前国家/会员等级中各指标真正支持的轴；输出用组数及结果SHA记录，不提交账户分组明细。份额先算授权范围内分母，再移除或应用指定轴过滤；支付方式不能用来直接分摊商品金额。

数据验收完成后，显式`--grant-reader-select`仅补sqlshed_reader对DWS schema的USAGE与这6表SELECT；不改角色属性、默认授权、写权限或ADS权限。TCP读取6表和零行写入拒绝再验证。密码仍只在本机被忽略的600配置文件。

本批完整实现与独立测试超过通常200行的原因是需同时交付6模型、26项指标及联合粒度/事务验收，不能按单表分PR。真实行数、指标值和测试结果见DWS_ACCEPTANCE.md及版本化脱敏报告，不把实施计划当执行证据。
