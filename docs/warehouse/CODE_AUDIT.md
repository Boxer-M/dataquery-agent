# 9d979c9 代码与数据库核查

2026-11-10：工作区核查起点为 feature/ecommerce-dwd，HEAD 9d979c9，与 origin/feature/ecommerce-dwd 一致且初始干净。本轮不把“已推送分支”当成“已合入 main”；不依赖创建 PR 才能审查代码。

## 已存在并受 Git 管理的路径

| 用途 | 真实文件与位置 | 内容 |
|---|---|---|
| CREATE TABLE | `sql/dwd/order_item_fact.sql:2` | 显式字段、类型、NOT NULL、明细主键 |
| ODS→DWD 投影 | `sql/dwd/order_item_fact.sql:20` | `-- check: projection` 下 SELECT，仅关联 ods.orders |
| 执行入口 | `scripts/build_order_item_fact.py:11` | main，调用两次构建并比较，写汇总报告 |
| 构建编排 | `src/dataquery_agent/dwd.py:72` | advisory try-lock、REPEATABLE READ、建表、INSERT、校验、提交/回滚 |
| 源检查 SQL | `src/dataquery_agent/dwd.py:15` | 主键、行数、订单孤儿、必填缺失与非有限金额检查 |
| 目标检查 SQL | `src/dataquery_agent/dwd.py:31` | 结构、主键、双向 EXCEPT ALL、金额、取消单和质量标识检查 |
| 指标标准 SQL | `sql/metrics/ecommerce_v1.sql` | 8 项命名 SELECT；DWD 构建器复用 item_net_sales_amount |
| 全源质量 SQL | `sql/metrics/ecommerce_profile.sql` | 字段、约束、时间、金额、状态、关系、扇出检查 |
| 单元测试 | `tests/test_dwd_order_item_fact.py` | 7 项内存合成/Mock 测试，并非 PostgreSQL 全量集成测试 |
| 其他回归测试 | `tests/test_ecommerce_metrics.py` 等现有 tests 文件 | 指标语义、窗口、SQL 与问数链路等 |
| 实测报告 | `reports/dwd_order_item_fact.json`、`reports/dwd_order_item_fact_safety.json` | 首次/重跑、全量校验和故障实验摘要 |

**SQL 确实部分嵌在 Python 中**：`CREATE SCHEMA IF NOT EXISTS dwd`、`INSERT INTO dwd.order_item_fact + projection` 在 dwd.py:88–90；所有事务/锁及多数质量查询也在该文件。当前没有独立的 DWD validate.sql 文件，不能凭空声称已有。下一批将加工、DDL、质量 SQL 分开纳管，Python 仅保留编排，先做兼容性回归，不在本设计轮修改它们。

## 数据库现状与版本化边界

本次使用数据库强制 READ ONLY + REPEATABLE READ 检查 `pg_class/pg_constraint/pg_trigger/pg_proc/information_schema.columns`，并直接调用 validate_target（不调用 build）。

- 应用表：ods.categories/customers/products/orders/order_items/payments/returns，及 dwd.order_item_fact；其余所见对象为这些表的主键索引。
- 用户触发器 0，非系统函数/过程 0，默认/生成列表达式 0；未见额外业务视图或物化视图。
- DWD 只有已版本化的 PRIMARY KEY(order_item_id)；ODS 主外键与 sql/load_sqlshed_ods.sql 一致。分类自关联未声明 FK，关系由版本化审计查询检查。
- DWD 215000 行，唯一键 215000、NULL 键 0、双向全字段差异 0；源/目标/标准 SQL 金额均 9024059.76；取消单明细 12829 行、544649.08；五类标识均 0。
- 未发现**仅保存在数据库中的持久加工逻辑**。该结论针对本地 sqlshed、当前查询可见对象，不是对其他实例或历史交互 SQL 的全局保证。

需补足的复现边界：上一轮故障注入与错误投影实验通过 Mac 临时脚本执行，报告已纳管，临时实验脚本没有进入仓库；它们不是数据库内持久逻辑，但目前不能把全部故障实验称为已具备版本化的 PostgreSQL 集成测试。第一批必须将此类实验纳入隔离环境的正式测试，禁止为重测删除现有表。

## 设计所用真实来源

SQL Shed 官方 Schema 的本地适配为 `sql/load_sqlshed_ods.sql`；完整字段元数据/质量与全量/月度/空窗指标证据为 `reports/ecommerce_metrics_v1.json`；批准定义为 `metrics/ecommerce_v1.json`。

本轮补充只读客户剖面：12000 个账户，8622 个有订单的账户，5376 个至少两单的账户（全源、全部订单状态）；是账户记录统计，不是自然人识别或支付客户统计。扩展指标的分组/时间窗标准 SQL 尚未实施或验证，不能把这些剖面值当作新增指标全套已验收。
