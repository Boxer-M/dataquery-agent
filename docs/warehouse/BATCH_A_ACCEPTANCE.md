# A批 DWD + DIM 实施与真实验收

本文记录A批验收时点：当时仅DWD/DIM完成，DWS/ADS及22项扩展尚未实施。之后B批DWS已独立验收，最新状态见[DWS_ACCEPTANCE](DWS_ACCEPTANCE.md)；本文件与原报告保留原48项测试和当时指纹证据，不改写历史结果。

## 单一入口与可复现命令

```sh
PYTHONPATH=src .venv/bin/python scripts/build_warehouse.py --plan
PYTHONPATH=src .venv/bin/python scripts/build_warehouse.py
PYTHONPATH=src .venv/bin/python scripts/build_warehouse.py --check-only
WAREHOUSE_POSTGRES_TESTS=1 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

本机PostgreSQL为127.0.0.1:5433，连接凭据只读取被忽略的.env。`--plan`不连接数据库；默认构建后自动再执行一次只读全批核验；`--check-only`两次均为只读，缺表/不一致即失败，不负责修复。检查前后也读取Olist订单数和全字段内容SHA，不向Olist发写SQL。

关键代码：

- `warehouse/models.json`：9对象有序依赖、字段、空值、主键/外键、必要索引、DDL与投影路径。`reuse_only`禁止A批创建或重建已有明细表。
- `sql/warehouse/ddl/{dim,dwd}/`与`transform/{dim,dwd}/`：显式建表与单独SELECT。原`sql/dwd/order_item_fact.sql`保留；新明细SELECT与原SELECT的语法树一致。
- `sql/warehouse/quality/`：relation通用结构/索引/全行校验，batch_a关系/日期/退货独立检查，metrics为原8指标的DWD实现，legacy_item承接旧入口SQL。
- `src/dataquery_agent/dwd.py`：共享事务、建新表、既有表保护、精确结构/约束与双向EXCEPT ALL校验。旧单表函数/脚本仅兼容保留，今后正常运行统一入口。
- `src/dataquery_agent/warehouse.py`：源预检、拓扑依赖检查、逐模型执行、原8项多窗口GT、全源和原明细指纹；没有每张表各复制一套Python构建器。
- `scripts/build_warehouse.py`：统一命令、重跑核验、Olist保护、脱敏报告与本地发布指针。没有DWS/ADS隐式构建。

锁覆盖整个批次，与旧入口使用同一advisory try-lock。锁先于事务快照；新8表建表/INSERT/约束/验收同一REPEATABLE READ事务，失败回滚；已存在表只验证，不INSERT/UPDATE/DELETE/ALTER。既有明细14列不变，无重建或替换。

新支付事实按order_id、退货事实按order_item_id建立关联索引，并仅对新表ANALYZE；已有表和只读核验不收集/修改统计信息。首次全量验收触发60秒查询超时并回滚，补充新表索引/统计信息后在同一超时限制下完整通过；成功重试仍为8张新表created，无半批残留。

日期范围必须来自有效源日期，36600天为防止异常范围无限生成的技术保护上限；超限先报错审查，不截断或制造日期默认值。

## 表、金额与质量

| 对象 | 真实行数 | 处理 |
|---|---:|---|
| dwd.order_fact | 90000 | 源订单1:1，保留取消、无明细订单与可空发货时间 |
| dwd.order_item_fact | 215000 | 已有表只读复用；原始及派生字段全行一致 |
| dwd.payment_attempt_fact | 95000 | 含失败和重试，paid_at不改成下单时间 |
| dwd.return_record_fact | 12000 | 含时序/金额异常，不按明细去重退货记录 |
| dim.customer | 12000 | 当前账户，不按email合并；通用维度不复制姓名/email |
| dim.product | 2000 | 当前价格/成本明确命名，非历史成本 |
| dim.category | 24 | 根父类NULL合法；父链无孤儿/循环 |
| dim.date | 1100 | 从真实源日期2023-01-01到2026-01-04，含较晚发货日 |
| dim.channel | 4 | web/mobile/partner/phone原编码，无编造显示名称 |

四事实与相应ODS记录数一致；所有模型主键唯一、NULL键0、精确类型/空值/约束匹配、双向全字段差异0，事实→维度/日期孤儿0。支付/退货通过唯一订单/明细关联，不做多事实扇出。

商品净额 **9024059.76**，成功支付 **8998225.89**，退货记录金额 **498266.03**；三者分别保留，不相减命名为财务净收入。原8项在全量、2025-01、空窗口上与ODS标准SQL及已批准基线一致，含比率分子分母；不宣称22项扩展指标已经验证。

取消单明细12829行、净额544649.08全部保留；失败支付5252条、源尝试金额517197.15保留（不是成功支付额）。退货时间早于下单5962条，退款超过明细净额1849条，分别保存`dq_return_before_order`与`dq_refund_above_item_net`；可按return_id→order_item_id→order_id追溯。两类可重叠，不把标识数量相加当异常记录去重数。当前其他已建质量标识均0；不是清洗后为0。

## 幂等、回滚与测试

同源快照/同模型只校验已有内容；自动只读重跑和再次执行正常批量入口均为9对象全部verified_existing；七ODS全字段有序SHA及原明细SHA前后相同，Olist订单99441且订单内容SHA前后相同。不存在自动清空、追加或覆盖。

隔离PostgreSQL8项集成测试使用新建随机`dataquery_test_a_<uuid>`数据库与显式合成SQL夹具，清理仅限本次创建的该命名空间，不操作sqlshed/olist：

1. 完整9模型的行数、精确金额、异常标识、ISO周年/闰日/范围及重跑。
2. 新支付事实已插入后注入故障，验证本批全部新表回滚、原明细和ODS保留。
3. 既有目标字段不兼容时拒绝，不覆盖原对象。
4. 既有目标内容不一致时拒绝，不静默修复。
5. 品类父链循环在新模型创建前阻断。
6. 源孤儿被拒绝，不因JOIN悄悄丢行。
7. 给测试已有明细加禁止写入触发器，批量构建及只读校验仍成功，证明不写原表。
8. 并发锁拒绝和既有明细依赖缺失拒绝，原测试事实保持。

全部 **48项测试通过、0失败/错误/跳过**：原36项、4项模型契约、8项真实隔离PostgreSQL集成测试。不设置WAREHOUSE_POSTGRES_TESTS=1时集成组会明确跳过，不能把这种离线运行称为全套48项已执行。

## 发布证据与边界

真实构建报告：[本次不可变报告](../../reports/warehouse/batch_a_811b1be2327536114749f2d95b5551fe3442f45402ce2d31e8d47fba0b2eec36.json)。
测试摘要：[batch_a_tests.json](../../reports/warehouse/batch_a_tests.json)。

报告记录模型版本、原1.0.0指标定义/SQL哈希、源数据库指纹、源文件清单哈希、每模型结果、动作、已批准GT和被测试源码/SQL哈希。`tested_base_commit`是开发起点提交，不冒充包含本次代码的提交；精确源码由tested_artifact_sha256标识。内容构建键报告只写一次，已有相同键报告不覆盖；本地`reports/warehouse/current.json`原子指向成功报告并被Git忽略。DB已提交而报告/指针未写入时，不宣称已发布，重跑只读核验后可恢复报告。

报告不含业务明细或凭据；故障摘要和临时文件在被忽略的data/staging下。源文件清单哈希是溯源引用，数据库有序行SHA是此次构建输入指纹，不把“清单哈希存在”冒充本轮重新比较全部Parquet内容。

当前仍是固定快照全量首建/校验复用；没有CDC、增量更新、SCD2、DWS/ADS、查询服务/Agent新权限。新快照与已有对象不一致时停止，不能通过改验收阈值或重建原明细让构建通过。8项unsupported保持不可用。

本次按完整A批交付，SQL、模型元数据、共享执行器和隔离测试超过通常200行的原因是必须同时验收4事实+5维度，不能拆成缺少关联与回滚证明的单表PR。

面试可讲：用源主键锁定四种独立事实粒度，用公共当前维度解释客户/商品/渠道；金额和时间不混用，异常保留打标。通过同事务建模、全字段多重集比对、独立指标GT、不可变源指纹和幂等重跑证明可验证性；一批失败不会发布半套数仓。

下一轮最小可验收任务：按既定B批一起实施主题DWS/ADS及相应指标GT，不进入Agent改造。
