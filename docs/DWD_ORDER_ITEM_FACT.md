# 第一张 DWD：订单商品明细事实表

前置已核实：指标 PR #7 合入 main（12a5332），本轮分支从该提交创建。仅实现 `sqlshed.dwd.order_item_fact`，没有创建其他 DWD、维度或 DWS 表。

## 输入、输出和调用链

输入是只读使用的 `ods.order_items` 与 `ods.orders`；输出一行对应一个源 `order_item_id`，只按 `order_id` 关联订单。先验证订单唯一性、明细唯一性和必填字段/订单关联，再执行 SQL 投影，最后在同一事务内逐行校验后提交。不连接商品现价、支付或退货。

| 字段 | PostgreSQL 类型 | 来源/含义 |
|---|---|---|
| order_item_id | INTEGER PRIMARY KEY | 源明细主键 |
| order_id, product_id, quantity | INTEGER NOT NULL | 原值保留 |
| unit_price, discount_amount | NUMERIC(12,2) NOT NULL | 明细成交单价、整行折扣，原值保留 |
| net_item_amount | NUMERIC NOT NULL | quantity × unit_price − discount_amount，折扣扣一次；不限制 12 位避免乘法溢出 |
| order_purchase_time | TIMESTAMP WITHOUT TIME ZONE NOT NULL | 严格来自 orders.ordered_at，不新增时区解释 |
| order_status | VARCHAR NOT NULL | 当前订单状态快照，不是历史状态流 |
| dq_nonpositive_quantity | BOOLEAN NOT NULL | quantity <= 0 |
| dq_negative_amount | BOOLEAN NOT NULL | unit_price < 0 或 discount_amount < 0 |
| dq_discount_exceeds_gross | BOOLEAN NOT NULL | discount_amount > quantity × unit_price |
| dq_unknown_order_status | BOOLEAN NOT NULL | 不在 cancelled/delivered/processing/returned/shipped 中 |
| dq_invalid_order_time | BOOLEAN NOT NULL | ordered_at 为非有限时间 |

所有订单状态均保留，尤其取消单；没有“非取消”过滤条件。原设计稿中的 `item_net_amount` 在本轮按任务明确要求落地命名为 `net_item_amount`，公式和指标语义不变。无币种字段，因此金额单位不作猜测；净额不等于支付额或退款后收入。

## 构建与重复执行

```sh
PYTHONPATH=src .venv/bin/python scripts/build_order_item_fact.py
PYTHONPATH=src .venv/bin/python scripts/validate_ecommerce_metrics.py --check
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

使用既有 PostgreSQL 127.0.0.1:5433，密码只从被忽略的 `.env` 读取。无新增依赖。

- SQL 定义：`sql/dwd/order_item_fact.sql`，含显式类型与单独可执行的 SELECT 投影。
- 构建器：`src/dataquery_agent/dwd.py`；最小入口：`scripts/build_order_item_fact.py`。
- 在事务快照前获取会话 advisory try-lock；已有构建占用时立即停止。连接必须 autocommit，使每次构建使用独立事务；事务内为 REPEATABLE READ。
- 目标不存在时建 Schema、建表、INSERT SELECT、校验，失败全部回滚。未添加排除业务异常的 CHECK；不创建指向 ODS 的 FK 或修改 ODS。
- 目标已存在时不 INSERT、不 UPDATE、不删除或清空，只验证结构、主键、全部字段及金额。冲突直接报错，不尝试自动修复。已有 Schema 可复用，但已有同名不兼容对象不会覆盖。
- `EXCEPT ALL` 双向比较所有字段和质量标识，结合行数、主键及金额校验，发现 JOIN 丢失、放大或字段错配均阻止验收。仅比较金额合计不足以证明逐行正确。
- 脚本自动执行两次，第二次必须只校验且结果一致；报告记录 first_action / repeat_action。后续重跑会重新写本地报告，不更改数据库已有表。

当前是固定 SQL Shed 快照的首次建模，不提供增量刷新。源行数、金额或字段发生变化应先审核快照与指标基线，再单独设计版本迁移，不能用本脚本覆盖现有事实。业务异常按原值加工并打标；主键/订单关联不完整、必填字段缺失或非有限源金额则阻断事务且不篡改源数据。本轮不能给明细打“退货早于订单”等跨退货事实标识，因为那需要另一张事实。

## 实际验收

真实结果见 `reports/dwd_order_item_fact.json`：

- 215000 行，唯一主键 215000，NULL 主键 0；双向全字段差异 0，验证关联、时间、状态、原始字段、净额和标识。
- DWD 净额、ODS 明细净额、v1 标准 SQL 及已确认基线均为 **9024059.76**。
- 取消订单明细 **12829 行**，净额 **544649.08**，全部保留。
- 5 类质量标识数量均为 **0**；不是删除异常后得到的零。内存合成测试确认零数量、负金额、超额折扣、未知状态、无限时间原样保留并正确打标。
- 首次真实执行 `created`，第二次 `verified_existing`，数量和全部校验结果一致。
- 故障实验在 INSERT 后、提交前注入 ValueError，证明建表/插入事务一起回滚，目标表恢复为不存在，源明细仍为 215000 行、9024059.76。实验只在目标表尚不存在时进行，不删除已有表。
- 已存在表的冲突实验只读改变“期望投影”的净额公式（每行 +1），确认 full-row mismatch 被拒绝，再以正确投影验证成功；未更改已有数据。
- 安全实验结果见 `reports/dwd_order_item_fact_safety.json`。上一轮指标基线复核通过，Olist 订单数仍为 99441。
- 全部 **36 项测试通过**（新增 7 项），涵盖精确小数、大额派生、取消单保留、质量标识、关联范围、源键/必填关系拒绝和并发构建拒绝。

## 关键代码与复盘

`validate_source` 是防止 JOIN 放大/丢失的入口门槛；SQL 投影定义唯一加工规则；`validate_target` 同时比较结构、逐行内容和独立标准 SQL；`build_order_item_fact` 负责事务和已有目标保护。异常标记描述原事实，不改变金额或业务状态。结构完整性异常阻断接入，业务异常保留供后续分析，两者不能混淆。

面试可讲：先用明细主键锁定粒度，订单唯一关联带来下单时间与状态；金额以 NUMERIC 精确派生，不用浮点数，也不连接支付和退货导致扇出。首次建模具备事务回滚，重跑以全字段校验实现幂等，源变化时拒绝静默覆盖。

本次建模 SQL、事务实现、异常测试和真实报告作为一个可验收里程碑交付，超过通常 200 行的原因是需要同时证明加工、完整性、回滚和幂等。增量刷新、订单状态历史和其他事实表仍未实现。

下一步最小任务：基于该事实表设计并验证下单日商品净额汇总，不把订单数直接当作明细数。
