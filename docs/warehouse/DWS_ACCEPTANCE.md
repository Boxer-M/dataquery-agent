# B批DWS：实体与指标验收

状态：6张DWS已在sqlshed中真实创建，正常入口重跑及只读重跑通过；没有创建ADS或修改Agent。本批从main 90fbdba的feature/ecommerce-dws实施，与ADS分开PR。

## 模型、粒度与SQL清单

| 表 | 联合主键/粒度 | 真实行数 | DDL | 加工SELECT |
|---|---|---:|---|---|
| dws.order_day | order_date, channel, status | 13913 | [sql/warehouse/ddl/dws/order_day.sql](../../sql/warehouse/ddl/dws/order_day.sql) | [sql/warehouse/transform/dws/order_day.sql](../../sql/warehouse/transform/dws/order_day.sql) |
| dws.product_sales_day | order_date, channel, product_id | 121595 | [sql/warehouse/ddl/dws/product_sales_day.sql](../../sql/warehouse/ddl/dws/product_sales_day.sql) | [sql/warehouse/transform/dws/product_sales_day.sql](../../sql/warehouse/transform/dws/product_sales_day.sql) |
| dws.payment_day | payment_date, channel, method, status | 15820 | [sql/warehouse/ddl/dws/payment_day.sql](../../sql/warehouse/ddl/dws/payment_day.sql) | [sql/warehouse/transform/dws/payment_day.sql](../../sql/warehouse/transform/dws/payment_day.sql) |
| dws.return_day | return_date, channel, product_id | 10697 | [sql/warehouse/ddl/dws/return_day.sql](../../sql/warehouse/ddl/dws/return_day.sql) | [sql/warehouse/transform/dws/return_day.sql](../../sql/warehouse/transform/dws/return_day.sql) |
| dws.item_return_cohort_day | order_date, channel, product_id, as_of | 121595 | [sql/warehouse/ddl/dws/item_return_cohort_day.sql](../../sql/warehouse/ddl/dws/item_return_cohort_day.sql) | [sql/warehouse/transform/dws/item_return_cohort_day.sql](../../sql/warehouse/transform/dws/item_return_cohort_day.sql) |
| dws.customer_order_day | order_date, customer_id, channel | 72292 | [sql/warehouse/ddl/dws/customer_order_day.sql](../../sql/warehouse/ddl/dws/customer_order_day.sql) | [sql/warehouse/transform/dws/customer_order_day.sql](../../sql/warehouse/transform/dws/customer_order_day.sql) |

每张表的类型/空值/约束/索引匹配清单，联合PK无NULL/重复，生产SELECT双向全行差异为0；六张表还分别与独立ODS参考算法双向全行比较，差异为0。日期/渠道/商品/账户关联检查及cohort分子分母界限检查通过。

规则与适用维度详见[DWS_CONTRACTS](DWS_CONTRACTS.md)，不把汇总数据行数当原始订单/明细/支付/退货行数。

## 核心金额与比率

- 订单90,000、明细215,000、支付尝试95,000、退货记录12,000。
- order_day/product_sales_day/customer_order_day净额上卷均为 **9024059.76**，与原标准SQL一致。
- captured成功支付 **8998225.89**；源退货记录金额 **498266.03**，不解释为退款到账金额；三个金额不相减命名财务收入。
- 支付尝试成功率89748/95000=**0.94471579**。
- 明细退货率在下单队列[2023-01-01,2026-01-01)、排他as_of=2026-01-01下为12000/215000=**0.05581395**。
- 2025-01下单队列仍为314/5538，不能用当月329条退货记录代替分子；事件月和购买批次分别校验。
- 保留提前退货5962、超明细净额1849的记录计数；有异常退货关联的不同明细6885，不能简单加两个标识数。cohort率仍包含源异常，不宣称有效售后率。

## 新增18项available

在自然日对齐、已验证维度/过滤以及已建观察截止范围内：

1. order_item_count：商品明细数。
2. ordered_unit_count：下单件数，全量352075。
3. item_gross_sales_amount：折扣前商品记录金额，全量9107788.58。
4. item_discount_amount：行折扣金额，全量83728.82。
5. cancelled_order_count：当前取消状态订单数，按下单日归属。
6. cancelled_order_rate：取消状态订单数/全部订单数。
7. average_order_record_amount：商品净额/全部订单数，全量100.26733067，含取消单及无明细订单，不是支付客单价。
8. payment_failed_attempt_count：失败尝试次数。
9. payment_failed_attempt_rate：失败尝试数/全部尝试数，不硬取1减成功率。
10. return_before_order_count：提前退货记录数。
11. return_above_item_net_count：超明细净额退货记录数。
12. ordering_customer_count：下单账户数，全量8622。
13. repeat_ordering_customer_count：窗口内至少2单账户数，全量5376。
14. customer_repeat_order_rate：5376/8622=0.62352122，不是自然人或支付客户复购率。
15. orders_per_ordering_customer：窗口订单数/不同下单账户数。
16. channel_order_share：渠道订单数/同授权窗所有渠道订单数，先算分母再筛渠道。
17. ordered_product_count：窗口内有明细的不同商品数，不累加日商品去重数。
18. category_sales_share：当前品类商品净额/同窗所有品类商品净额，先算分母再筛商品/类别轴。

机器目录metrics/warehouse_catalog_v1.json更新为1.1.0-dws，原metrics/ecommerce_v1.json及8项定义原字段不变；新增dws_validation明确范围，并不使旧supported_dimensions的所有组合自动获得DWS能力。Agent/查询服务尚未接入，available表示SQL及数据对账已完成。

四项继续defined_unverified/not_available_in_dws：payment_attempted_order_count、multiple_payment_attempt_order_count、payment_item_amount_mismatch_order_count、first_observed_ordering_customer_count。它们有来源和定义，但缺明细粒度的汇总路径；待受控DWD或后续ADS独立实施，不假称缺定义，也不升级为unsupported。原8项unsupported对象逐项保持原样。

## Ground Truth与测试证据

共 **312组指标比较**，覆盖26项（原8+新增18）的全量、2025-01、边界日、空窗、渠道/商品/当前品类/支付方式/状态/账户/当前国家/会员等级中各自支持的轴，以及渠道/品类过滤。支持范围取自warehouse/dws_metrics.json；不把数学上可算但业务不支持的分组放入目录。

参考路径：

- 独立模型GT：[models.sql](../../sql/warehouse/ground_truth/dws/models.sql)。订单先按订单汇总明细，cohort用源ID去重，退货标识从源时间/金额重新计算，与生产存储标识独立。
- 指标源SQL：[ods.sql](../../sql/warehouse/dws_metrics/ods.sql)；DWS SQL：[dws.sql](../../sql/warehouse/dws_metrics/dws.sql)。均由白名单bucket/group_by与绑定参数渲染；不是自由SQL接口。
- 真值与指纹报告：[dws_acceptance.json](../../reports/warehouse/dws_acceptance.json)；测试摘要：[dws_tests.json](../../reports/warehouse/dws_tests.json)。账户分组只保存组数与结果SHA，不提交分组明细。
- 全量命令：`WAREHOUSE_POSTGRES_TESTS=1 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v`。

**59项全部通过，无跳过**：原48项、4项DWS契约/精确数值语义测试、7项新隔离PostgreSQL集成测试。新增覆盖同账户跨天/渠道、重复退货、等于as_of排除、源层写保护、全部新表回滚、已有内容不覆盖、依赖缺失、日期边界、零分母与不伪造captured分组。测试仅创建并清理随机隔离数据库，不删除个人数仓表。

首次真实构建的基线断言发现SUM(bigint)返回Decimal与JSON整数类型表示不同，事务按预期回滚；修正为精确Decimal数值比较，未改SQL公式或已批准基线。随后真实创建6表，并再次正常入口重跑6表全部verified_existing；最新报告记录该重跑结果。

## 可复现执行与只读查看

```sh
PYTHONPATH=src .venv/bin/python scripts/build_dws.py --plan
PYTHONPATH=src .venv/bin/python scripts/build_dws.py
PYTHONPATH=src .venv/bin/python scripts/build_dws.py --check-only
```

统一入口：[build_dws.py](../../scripts/build_dws.py)；批次编排：[dws.py](../../src/dataquery_agent/dws.py)；共用事务/关系校验保留在dwd.py。事务覆盖6模型，目标已存在时只核验；缺依赖、目标冲突、真值差异全部拒绝，不做破坏性替换。as_of改变也不能覆盖原cohort表。

其他5个加工SELECT可在SQL客户端只读运行；cohort SELECT中的`{as_of}`须显式替换为`TIMESTAMP '2026-01-01 00:00:00'`。指标模板另有绑定参数/白名单分组，需经代码渲染；不要把未替换的花括号或参数原样当客户端SQL执行。

读者可直接核对：

```sql
SELECT sum(order_count) AS orders,sum(item_net_sales_amount) AS item_net
FROM dws.order_day;

SELECT status,sum(payment_attempt_count) AS attempts,sum(attempt_amount) AS record_amount
FROM dws.payment_day GROUP BY status;

SELECT sum(returned_item_count) AS numerator,sum(cohort_item_count) AS denominator,
       round(sum(returned_item_count)::numeric/nullif(sum(cohort_item_count),0),8) AS item_return_rate
FROM dws.item_return_cohort_day WHERE as_of=TIMESTAMP '2026-01-01 00:00:00';
```

所有业务时间仍无时区，不偷偷转UTC/北京时间；币种缺失，不标人民币或美元。月指标优先从日基础度量重聚合；账户去重/复购必须跨窗口重新聚合，不按每日结果简单相加。空维度组保持0行，不做巨量笛卡尔补零。

## 数据、权限与Git边界

七ODS+四DWD+五DIM全字段指纹前后不变；ADS对象列表不变（无新ADS）。Olist订单99441且完整订单内容SHA前后相同。没有新增依赖，没有改NL2SQL或SQL Guard。

DWS验收后显式补充sqlshed_reader对dws的USAGE和6张现有表SELECT，TCP逐表读取通过；关闭测试会话默认只读保护后零行INSERT/UPDATE/DELETE仍拒绝，TRUNCATE等写权限为否，仅核查ACL。角色属性、成员关系、CREATE/TEMP限制不变，不做默认SELECT授权或ADS授权。密码与学习说明只留Mac本地忽略文件，不进入Git。

本批提交SQL、共享执行扩展、指标目录、测试及脱敏报告；整体超过200行是为一次验收6张表及26项指标，不按单表拆PR。未自动Merge。下一轮单独建设ADS应用数据集，优先澄清多事件时间展示、支付订单差异/多次尝试和退货异常解释；缺粒度的4项需受控DWD或有业务用途的ADS路径，不能硬塞进这6张汇总。
