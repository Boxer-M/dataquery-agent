# DataQuery Agent 项目计划

最后更新：2026-11-10

## 进度原则

本文件只记录真实完成且有代码、报告、测试或 Git 证据的进度。规划中的功能不能写成已实现。SQL Copilot 属于另一个独立项目，不在本仓库实现。

## D1：公开数据下载与原始字段审计

状态：已完成并通过本地验收。

已完成证据：

- 使用 Kaggle 官方页面和官方 API 核验 Olist 发布者、许可证及版本元数据。
- 从 Kaggle 官方下载端点取得压缩包，保留压缩包、官方元数据和 9 个原始 CSV 于 `data/raw/`。
- `.gitignore` 明确忽略 `data/raw/`，原始数据不进入 Git。
- `src/dataquery_agent/data_audit.py` 只读审计字段、行数、空值、候选键、日期、金额、状态、关键关联和业务字段边界。
- `reports/d1_data_audit.json` 保存机器可读的真实审计结果、文件大小和 SHA-256。
- `tests/test_data_audit.py` 覆盖空值、重复键、非法日期和金额解析异常。
- 真实运行单元测试和 9 文件完整审计均通过。

## D1 已确认限制

- 数据集没有真实退款金额、退款完成时间或独立支付时间字段。
- `order_approved_at` 是订单审批时间，不能直接等同于支付时间。
- `customer_unique_id` 会跨多个 `customer_id`，适合识别复购客户但不是客户表唯一行键。
- `review_id` 单列存在重复；`review_id + order_id` 在当前快照中唯一。
- 地理位置邮编前缀不唯一，不能直接作为地理表主键。
- `shipping_limit_date` 最大值到 2020-04-09，晚于主要订单购买时间范围，D2 必须保留并调查，不能静默删除。

## PostgreSQL ODS 数据接入

状态：已完成并通过本地验收。

已完成证据：

- 数据库选型更新为 Docker Compose 管理的 PostgreSQL 16，宿主机端口为 `127.0.0.1:5433`。
- `sql/load_olist_ods.sql` 在同一事务中创建 9 张 `ods_` 表并使用 `\copy` 导入。
- `scripts/import_olist_ods.sh` 检查 9 个源文件并执行可重复导入；目标表已存在时拒绝覆盖。
- 所有 ODS 字段保留原始字段名，不添加主键、外键、唯一或非空约束。
- 9 张表的数据库行数与 CSV 审计行数全部一致，共 1,550,922 行。
- 金额字段验证为 `NUMERIC(12,2)`，日期字段验证为 `TIMESTAMP WITHOUT TIME ZONE`。
- `sql/validate_olist_ods.sql` 可重复执行行数、样例和字段类型检查。

## 尚未完成

- ODS 之上的清洗层、业务主外键、索引和数据字典。
- Agent 专用只读数据库账号和权限。
- 指标语义层、RAG、Text-to-SQL、Agent、API、可视化和评测。

## SQL Shed 完整 ODS 接入

2026-11-10：已下载官方七表完整 Parquet 和 Schema SQL；426024 行已事务导入独立 sqlshed.ods，逐行与源文件一致。主外键和字段类型检查通过；业务异常保留并记录于 docs/SQLSHED_INGESTION.md。Olist 订单数仍为 99441。SQL Shed 是官方合成练习数据。重复执行与最终测试结果见接入说明；不表示已完成跨数据集 NL2SQL 或语义层。

## 电商业务指标规格

2026-11-10：已定义 v1.0.0 的 8 项指标（metrics/ecommerce_v1.json），完成 PostgreSQL 全量/月度/空窗只读验证，标准 SQL 与汇总基线已版本化。按支付尝试计算成功率；明细退货率使用下单批次与固定观察截止，保留源业务异常并标注限制。docs/ECOMMERCE_METRICS.md 提出四张 DWD 事实与四张 DWS 汇总及必要维度；这些模型仍为建议，未创建实体表。尚未接入 Agent 指标检索、自动评测或财务净收入口径；币种、时区及退货异常处理待业务确认。

## 第一张 DWD 订单明细事实表

2026-11-10：在指标 PR #7 合入 main 后，实现 sqlshed.dwd.order_item_fact。215000 行与 ODS/订单关联投影全字段一致，net_item_amount 合计 9024059.76 与标准 SQL 基线相符；12829 条取消订单明细保留。五类质量标识均为 0。首次建表/加工/校验同事务，真实故障注入确认回滚；重复执行只核验且不追加。全部 36 项测试通过，既有指标基线复核通过，Olist 订单数仍为 99441。详情及真实报告见 docs/DWD_ORDER_ITEM_FACT.md。其他事实、维度、DWS、增量刷新与状态历史仍未实现。
