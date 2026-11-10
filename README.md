# DataQuery Agent

SQL Shed 七表完整数据接入与质量检查见 [接入说明](docs/SQLSHED_INGESTION.md)。这是 CC0 合成练习数据，独立存放于 `sqlshed.ods`。

第一版 8 项电商指标、标准 SQL 与数仓模型建议见 [指标口径](docs/ECOMMERCE_METRICS.md)；指标已只读验证，DWD/DWS 尚未建表。

DataQuery Agent（智能问数系统）是一个用于学习和求职展示的独立 Python 项目，目标是基于可合法使用的公开数据，逐步实现自然语言查询、SQL 生成与只读执行、结果分析和可视化。

## 当前状态

当前已完成：

- Python 3.12 独立虚拟环境和项目骨架；
- 从 Kaggle 官方来源下载 Olist Brazilian E-Commerce Public Dataset；
- 核验发布者、CC BY-NC-SA 4.0 许可证和官方版本元数据；
- 对 9 个原始 CSV 运行可复现的字段、数据质量、候选键和关系审计；
- 生成机器可读报告并为核心审计逻辑添加标准库单元测试；
- 使用 PostgreSQL 16 建立独立 `olist` 数据库和 `ods` Schema；
- 将 9 张 ODS 表无损迁移到 `olist.ods`，全量内容校验与旧库一致；
- 建立 Gemini SQL 生成、SQL 安全校验和只读执行的首个 NL2SQL 闭环。

尚未完成指标语义层、RAG、LangGraph Agent、API、可视化或系统评测。

## Python 版本

- Python 3.12
- 项目虚拟环境：`.venv`

## 激活虚拟环境

在项目根目录执行：

```bash
source .venv/bin/activate
python --version
```

退出虚拟环境：

```bash
deactivate
```

## 复现 D1 审计

原始数据下载方法见 `data/README.md`。数据准备完成后运行：

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
PYTHONPATH=src .venv/bin/python -m dataquery_agent.data_audit \
  --raw-dir data/raw \
  --output reports/d1_data_audit.json
```

审计脚本只读 `data/raw/`，不会自动删除、去重、补值或覆盖源文件。

## D1 文档

- 数据来源、字段、关系及能力边界：`docs/DATA_SOURCE.md`
- 机器可读审计结果：`reports/d1_data_audit.json`
- 当前计划与验收状态：`PROJECT_PLAN.md`

## PostgreSQL ODS

本地数据库使用 Docker Compose 管理的 PostgreSQL 16，只映射到 `127.0.0.1:5433`。本地密码保存在不进入 Git 的 `.env`。

| ODS 表 | 记录数 |
|---|---:|
| `ods.customers` | 99,441 |
| `ods.geolocation` | 1,000,163 |
| `ods.order_items` | 112,650 |
| `ods.order_payments` | 103,886 |
| `ods.order_reviews` | 99,224 |
| `ods.orders` | 99,441 |
| `ods.products` | 32,951 |
| `ods.sellers` | 3,095 |
| `ods.product_category_name_translation` | 71 |

在已创建 `ods` Schema 的空 `olist` 数据库中复现建表和导入：

```bash
docker compose up -d db
scripts/import_olist_ods.sh
```

导入脚本发现任一目标表已存在时会停止，不会删除、清空或重复追加。只读验证命令：

```bash
docker compose exec -T db sh -c \
  'PGPASSWORD="$POSTGRES_PASSWORD" psql -h 127.0.0.1 -U "$POSTGRES_USER" -d olist -w -f -' \
  < sql/validate_olist_ods.sql
```
