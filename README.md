# DataQuery Agent

DataQuery Agent 是面向电商经营分析的自然语言查询项目。它将中文问题映射为经过验证的指标请求，使用只读查询执行，并返回带有业务口径、数据来源和 Trace 的结果。

SQL Shed 是采用 CC0 许可的公开合成测试数据集，独立存放于 `sqlshed.ods`。数据用于数仓构建、指标对账和查询回归。另有 Olist 数据接入和审计测试。详见[数据接入说明](docs/SQLSHED_INGESTION.md)和[数据来源](docs/DATA_SOURCE.md)。

## 当前能力

| 能力 | 说明 |
|---|---|
| 电商数仓 | 7 张 ODS、4 张 DWD、5 张 DIM、6 张 DWS、3 个 ADS 视图。[数仓设计与验收](docs/warehouse/README.md) |
| 指标目录与只读服务 | 38 项指标中，26 项已验证、4 项待验证、8 项不支持。已验证指标可通过 Python 或 CLI 查询。[指标目录](metrics/warehouse_catalog_v1.json) · [查询服务](docs/warehouse/METRIC_QUERY_SERVICE.md) |
| 自然语言指标解析 | 提取指标、时间和分组，遇到多个合理口径时返回澄清选项。[自然语言指标解析](docs/warehouse/INTENT_SEMANTIC_RESOLVER.md) |
| 经营查询规划 | 支持多指标查询、跨期比较、排行和分组阈值筛选。[经营查询规划](docs/warehouse/BUSINESS_QUERY_PLANNING.md) |
| 元数据与能力判断 | 校验数据库结构、关联键和权限，返回标准指标路径、明细候选或拒绝理由。明细候选仅用于规划。[元数据与能力判断](docs/warehouse/METADATA_CAPABILITY.md) |
| 统一 Agent | 从中文问题选择单指标、多指标、受控明细或澄清路径，并关联模型、能力和查询 Trace。[统一查询入口](docs/warehouse/UNIFIED_AGENT.md) |
| 受控明细查询 | 固定模板执行同单支付顺序与订单商品净额查询。[受控明细](docs/warehouse/CONTROLLED_DETAIL_EXECUTION.md) |
| 动态 SQL | 对已批准的新分析生成候选 SQL，经 AST 与业务口径验证后只读执行。[动态 SQL](docs/warehouse/DYNAMIC_SQL_ENGINE.md) |

## 测试与验证

项目使用单元测试、版本化业务用例、真实 PostgreSQL 对账和 Gemini 端到端测试验证查询流程。各模块的复现命令和覆盖范围见对应文档。[统一 Agent 端到端报告](reports/warehouse/unified_agent_v1.json)

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

## 运行测试与复现数据审计

运行默认测试套件：

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

按[数据下载说明](data/README.md)准备 Olist 原始文件后，运行只读审计：

```bash
PYTHONPATH=src .venv/bin/python -m dataquery_agent.data_audit \
  --raw-dir data/raw \
  --output reports/d1_data_audit.json
```

审计脚本只读 `data/raw/`，不会自动删除、去重、补值或覆盖源文件。

## 数据审计资料

- 数据来源、字段、关系及能力边界：`docs/DATA_SOURCE.md`
- 机器可读审计结果：`reports/d1_data_audit.json`

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
