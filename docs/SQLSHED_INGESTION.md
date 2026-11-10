# SQL Shed 完整数据接入

来源：https://sqlshed.com/practice-datasets/ecommerce-orders/ 。官方 CC0 合成练习数据，不是真实企业交易数据；使用完整下载而非网页 playground 样本。

## 目标与调用链

标准库下载器 → SHA-256 清单 → DuckDB 读取原始 Parquet 并检查 → psycopg COPY → PostgreSQL `sqlshed.ods` → 全量逐行比对。目标数据库独立于 `olist`，7 张表保留原始粒度和字段，金额为 NUMERIC(12,2)，时间不附加时区，NULL 原样保留。

唯一新增依赖为 duckdb==2.0.0，已在项目 Python 3.12 环境验证；用于直接读取 Parquet，不需要 Pandas/PyArrow。替代方案 PyArrow 会增加本轮不需要的依赖。

```sh
.venv/bin/python -u scripts/download_sqlshed.py
.venv/bin/python scripts/ingest_sqlshed.py
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

数据库连接使用忽略的 `.env` 中 POSTGRES_USER/POSTGRES_PASSWORD，地址 127.0.0.1:5433。脚本仅在库不存在时创建 sqlshed；已有 ods 时只核验，内容或类型不匹配即报错，不覆盖。已有非 ods 业务表则拒绝首次导入。建库独立于表事务，首次表导入失败可能留下空库，表和行会回滚。并发导入用事务 advisory lock 串行化。

下载按文件写入 `.part`，完成后原子更名；清单记录字节数、下载时间及 SHA-256。已有文件必须与清单一致才能复用。不覆盖已存在但无法验证的文件。SHA-256 是本地快照完整性校验，不是发布者提供的独立签名。超时按网络读操作生效，不是整个下载的总时限。

## 已执行结果

七表行数：categories 24、customers 12000、products 2000、orders 90000、order_items 215000、payments 95000、returns 12000，总计 426024。

原始与目标按主键排序逐行比较全部字段，含 Decimal、日期、时间和 NULL。主键 NULL/重复及 7 条外键关系孤儿数均为 0；日期时间类型有效且无非有限值；所有金额均为两位精度、无 NULL 和负数。数据库建表沿用官方主外键与 NOT NULL 约束；分类自关联按官方方式审计而不声明约束。

业务异常保留，不更改：

- 5962 笔退货时间早于对应订单时间。
- 1849 笔退款超过明细净额 `quantity * unit_price - discount_amount`。
- 250 个订单的成功支付额与明细净额不一致，整体差额 -25833.87；明细和成功支付分别按订单聚合后比较，避免多表连接放大。
- 支付 captured 89748、failed 5252；成功支付总额 8998225.89，失败尝试记录金额 517197.15，后者不能当作收入。
- 订单 cancelled 5407、delivered 64897、processing 5362、returned 4399、shipped 9935。
- 发货早于下单、支付早于下单、非正数量、折扣超过明细毛额及同明细多次退货均为 0。
- 发货时间有 10769 个 NULL，会员等级有 5296 个 NULL；这些原始可空值原样保留。

逐字段类型、NULL、金额范围与时间范围见运行后本地 `reports/sqlshed_ingestion.json`。该报告只含汇总，不含客户明细；文件为运行产物，不提交。Olist 订单数复核为 99441。现有 SQL Guard 和 Gemini 调用代码没有修改。

## 关键代码与面试说明

工程验证：单元测试共 23 项通过，包含现有 NL2SQL/SQL Guard 离线回归及新增下载、重复文件复用、损坏文件拒绝、超时清理、错误行数、重复/NULL 主键和孤儿外键测试。首次导入返回 `imported`，第二次返回 `verified_existing`，两次均完成 426024 行全部字段比对，目标类型检查通过。事务回滚未做故障注入测试。

`source_connection` 验证文件清单；`audit` 区分阻断接入的结构错误与仅记录的业务异常；`load` 将建表、COPY 和逐行核验放在同一事务；`verify_target` 同时用于首次验收和重复运行。无需临时 CSV：COPY 文本流本身就是 PostgreSQL 可导入格式，可避免 CSV 对 NULL 和空字符串的歧义。

面试可说明：重点是保证来源可追溯、粒度不变、金额不经过浮点数，以及事务完整性和重复执行安全。业务口径异常应交给后续分析，不能在 ODS 静默清洗。

本次为一个完整接入里程碑，超过通常 200 行是因为需同时交付下载、校验、事务导入、DDL、测试和复现说明。未验证真实 Gemini 网络调用和模型回答质量，本轮回归使用现有离线测试。
