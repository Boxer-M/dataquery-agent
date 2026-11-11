# C批ADS应用层验收

从已合并DWS的main `8cfce71`创建feature/ecommerce-ads。3个普通视图真实创建、正常重跑与只读重跑均通过；未修改Agent或DWS口径。

| 视图 | 唯一粒度 | 真实行数 | DDL | SELECT |
|---|---|---:|---|---|
| ads.business_overview_day | event_date, channel | 4316 | [DDL](../../sql/warehouse/ddl/ads/business_overview_day.sql) | [SELECT](../../sql/warehouse/ads/business_overview_day.sql) |
| ads.payment_anomaly_order | order_id | 90000 | [DDL](../../sql/warehouse/ddl/ads/payment_anomaly_order.sql) | [SELECT](../../sql/warehouse/ads/payment_anomaly_order.sql) |
| ads.product_return_cohort | order_date, channel, product_id, as_of | 121595 | [DDL](../../sql/warehouse/ddl/ads/product_return_cohort.sql) | [SELECT](../../sql/warehouse/ads/product_return_cohort.sql) |

全部字段类型、唯一键、NULL键、视图类型及版本定义检查通过；每个视图与独立ODS参考算法双向EXCEPT ALL差异为0。普通视图不设置物理主键/索引，索引沿用来源表；唯一性由查询验收保证。[详细契约](ADS_CONTRACTS.md)列出维度、时钟、分子分母、补零和异常展示规则。

## Ground Truth

全量窗口[2023-01-01,2026-01-01)，固定排他观察截止2026-01-01：

- 概览：订单90000，商品净额9024059.76；支付尝试95000，captured尝试89748，成功支付8998225.89；退货记录12000，记录金额498266.03。
- 支付订单队列：90000订单；成功/失败尝试89748/5252；失败率5252/95000=0.05528421。多次尝试订单5230，金额不等订单250，无尝试订单230。
- 商品购买批次：分母215000明细，分子12000不同退货明细，比例0.05581395；关联异常的不同明细6885。
- 提前退货5962条，超明细净额1849条，作为事件日质量计数分别保留；不截断金额或修正时间。
- 2025-01下单队列分子314、分母5538、比例0.05669917；同月退货事件329条，不能替代队列分子。概览销售231920.18，支付230864.57，退货12833.47分别标明事件时钟，不能相减声称同批收入。

共24组ADS窗口/渠道比较：全量、2025-01、边界日、空窗，各3个应用各含无过滤和渠道过滤。另有三个视图完整逐键/逐字段ODS比较，以及原26项DWS指标312组回归。商品/品类的完整逐键关系在全行比较中覆盖，不宣称支持支付按商品分摊。

独立GT：[models.sql](../../sql/warehouse/ground_truth/ads/models.sql)。概览把原始独立事件UNION ALL再汇总；支付将源明细和源支付度量组成ledger再按订单加总；退货先按源明细聚合多条退货再按批次计数。未复用生产视图SELECT作为唯一断言。

## 测试与幂等

实际命令：

```sh
WAREHOUSE_POSTGRES_TESTS=1 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

**69项全部通过，无跳过**：原59项、新增2项契约和8项隔离PostgreSQL集成测试。覆盖：多明细×多支付×重复退货不放大；独立事件时钟；无尝试的NULL时间及NULL失败率；空窗口标量0/比率NULL；退货率显式零分母NULL；截止等值排除；无订单的退货事件日保留；正常/只读重跑；建第二个视图后故障全批回滚；已有表/视图定义冲突拒绝；22个输入写保护和内容指纹；缺依赖停止。

最初权限探针遇到聚合视图不可更新错误（PostgreSQL先检查视图可更新性）；改为同时验证视图写入拒绝、ACL无写授权，以及底层表零行UPDATE因权限被拒绝。未因此更改业务SQL。完整回归中的定义冲突测试补充保留security_barrier，避免先命中选项检查而未覆盖定义哈希检查。

当前正常入口3视图全部verified_existing，后续只读重跑结果一致。没有CREATE OR REPLACE/DROP刷新生产对象。源码SHA和PostgreSQL规范定义SHA保存于版本注释及报告；篡改定义、列、选项或代码变更均拒绝替换。

## 运行与查看

统一入口：[scripts/build_ads.py](../../scripts/build_ads.py)；编排：[ads.py](../../src/dataquery_agent/ads.py)；模型清单：[ads_models.json](../../warehouse/ads_models.json)。DDL的`{projection}`及SELECT的`{as_of}`由受控入口渲染，不能把模板未替换直接执行。

```sh
PYTHONPATH=src .venv/bin/python scripts/build_ads.py --plan
PYTHONPATH=src .venv/bin/python scripts/build_ads.py
PYTHONPATH=src .venv/bin/python scripts/build_ads.py --check-only
```

SQL客户端可直接查询已创建视图：

```sql
SELECT * FROM ads.business_overview_day
WHERE event_date >= DATE '2025-01-01' AND event_date < DATE '2025-02-01'
ORDER BY event_date,channel;

SELECT order_id,payment_attempt_count,captured_attempt_count,failed_attempt_count,
       item_net_amount,captured_payment_amount,captured_minus_item_net
FROM ads.payment_anomaly_order WHERE has_amount_mismatch ORDER BY order_id LIMIT 100;

SELECT date_trunc('month',order_date)::date AS purchase_month,
       sum(returned_item_count) AS numerator,sum(cohort_item_count) AS denominator,
       round(sum(returned_item_count)::numeric/nullif(sum(cohort_item_count),0),8) AS item_return_rate
FROM ads.product_return_cohort WHERE as_of=TIMESTAMP '2026-01-01'
GROUP BY 1 ORDER BY 1;
```

## 数据、权限与指标边界

7ODS、4DWD、5DIM、6DWS的22个完整内容指纹前后一致。Olist订单99441，完整订单SHA前后相同。没有新依赖，NL2SQL与SQL Guard未修改。随机隔离测试库的清理不触及个人数据库对象。

sqlshed_reader仅新增ads USAGE及3视图SELECT，真实TCP读取成功；视图DML、源表零行UPDATE、CREATE及TEMP探针拒绝。检查全部业务表/视图的表级及列级写权限均为否，角色不拥有业务对象、无成员关系、角色属性不变；不需要实际DROP对象验证。凭据仅在被忽略的本地600文件中。

指标目录仅更新全局版本/ADS可用性说明，每个指标对象与DWS版完全一致：26已验证、4待验证、8unsupported。支付视图字段的固定截止验收不冒充待验证指标任意窗口/维度能力；首次下单归因不在本视图中。普通视图不是历史数据快照，不承诺新源的增量更新。

机器证据：[ads_acceptance.json](../../reports/warehouse/ads_acceptance.json)、[ads_tests.json](../../reports/warehouse/ads_tests.json)。本批整体超过200行是为3个完整应用、独立GT和完整测试一次验收，不分视图拆PR。

关键代码讲解：入口先验证已有DWS，然后调用同一事务包装创建/核验ADS；各事实先聚合到共同唯一键再关联，这是防止金额重复的核心。面试可用“一单2明细和2支付直接JOIN产生4行”解释风险，再说明独立聚合后每单只剩1行，并用独立原始数据算法对账证明正确。

下一步最小任务：实现ADS_CONTRACTS中只读查询服务请求/响应与Trace契约，先不接Agent生成SQL。
