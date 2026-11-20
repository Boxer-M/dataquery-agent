# Neo4j 指标血缘 v1：一个字段的影响闭环

基线 `origin/main=4a06a49`，包含跨会话偏好 PR #69。分支 `codex/neo4j-metric-lineage-v1`。不修改加工 SQL、指标公式、PostgreSQL 业务数据或原有 Agent 执行器。

## 范围与 SQL 取证

规范源字段：`ods.order_items.discount_amount`。首版只审核三项指标 `item_net_sales_amount`、`item_discount_amount`、`order_item_count`，不是 26 项指标的完整影响集合。图中中间字段也不是新的可查询根字段，其他已存在字段返回 `field_not_indexed`，避免将局部路径误当完整分析。

| 影响 | 逐跳证据 |
|---|---|
| VALUE：折扣值影响净额 | `sql/warehouse/transform/dwd/order_item_fact.sql:3` 的 `i.quantity * i.unit_price - i.discount_amount AS net_item_amount` → `sql/warehouse/transform/dws/product_sales_day.sql:3` 的 `sum(i.net_item_amount) AS item_net_sales_amount` → `sql/warehouse/dws_metrics/dws.sql:7–11` 的 `sum(item_net_sales_amount)` |
| VALUE：折扣值影响折扣合计 | DWD 原样保留 `i.discount_amount` → DWS `sum(i.discount_amount)` → 正式指标合计 `item_discount_amount` |
| 无 VALUE：订单商品明细数 | DWS 使用 `count(*) AS order_item_count`，正式查询合计该计数。Catalog.source_fields 虽列出 discount_amount，但不是数值因果证据 |
| DELETE：三项的后续重建依赖 | 源折扣列被删除后，DWD 整体 SELECT 无法解析；DWS 的版本化模型与实际 FROM/JOIN 依赖该 DWD，三项指标所需汇总无法按当前加工 SQL 重建 |

VALUE 指合法值变化、行集/连接键/筛选条件不变时的计算依赖；不涵盖非法类型、NULL 或质量验收失败引起的加工阻断。DELETE 是字段删除后的加工任务依赖，不是执行 Neo4j DELETE，也不表示已物化的旧指标查询立刻失效。本轮未真正删除或修改 ODS 字段。

## 图模型、版本与幂等

`DQLineage` 节点由 `(version,id)` 唯一标识，kind 分为 field/task/metric。`IMPACTS` 边使用确定性 id，impact_type 为 VALUE 或 DELETE。查询不混走两种类型。

- 11 节点：6 个字段、2 个加工任务、3 个指标。
- 12 关系：7 条数值计算边、5 条删除/重建依赖边。
- 当前图版本：`discount-lineage-v1:2fc1697ab3445fe5`。
- 每条边保存来源文件、起止行、表达式、文件 SHA-256、审核快照提交和图版本。
- `warehouse/lineage_discount_v1.json` 锁定审核输入 Hash。SQL、Catalog 或模型元数据改变后拒绝旧证据，须人工复核并更新审核清单后导入新版本。
- 导入用唯一约束与 MERGE；所有节点和边在同一数据事务导入。不删除旧版本，读取仅匹配当前审核版本。
- 查询后再次核对每跳与已审核 SQL 证据；缺路径、重复路径、Hash/版本不符都拒绝，不回退到本地路径冒充 Neo4j 查询结果。

## 接入位置与安全边界

`POST /api/chat → 原 Owner/ConversationManager → UnifiedAgent → 字段影响 Fast Path → 固定 Cypher → Neo4j → SQL 证据校验 → 原 Trace/Query History`。

- `src/dataquery_agent/lineage_evidence.py`：复用 SQLGlot、真实模型清单与 Catalog，读取实际投影和正式指标 SQL；Catalog 只提供指标身份及候选信息，不把 source_fields 直接当因果边。
- `src/dataquery_agent/metric_lineage.py`：规范字段白名单、元数据存在性/可读权限、只读工具调用及结果验证。
- `cypher/lineage_impact.cypher`：固定、参数化字段/类型/版本，最大 5 跳、20 行。用户和 LLM 均不能提交 Cypher。
- `cypher/lineage_import.cypher`、`lineage_constraint.cypher`、`lineage_counts.cypher`：仅受信本地 CLI 使用导入入口；Agent 不暴露导入能力。
- `src/dataquery_agent/unified_agent.py`：输入安全检查之后接入受控字段路由。显式 PDF/文档请求保持原知识路径，不静默用图谱代替文档证据。
- `scripts/metric_lineage.py`：初始化本地配置、幂等导入及查询。没有新增 Planner、Agent、DAG 或 Python 依赖。

Python 标准库调用官方 [Neo4j Query API v2](https://neo4j.com/docs/query-api/current/query/)，不是已弃用的 transaction HTTP API。仅允许 `http://127.0.0.1:7474`，关闭环境代理和重定向；超时 8 秒、响应上限 1 MiB。固定读取请求设置 `accessMode=Read`；这是请求模式，不冒充数据库角色权限隔离。

本地 Community 开发实例使用服务端管理凭据，未建立独立 Neo4j reader 角色。真正的约束是现有 API 身份、字段元数据可读校验、固定查询和不可调用写工具；不宣称已完成企业多租户 Neo4j RBAC。图谱只含审核过的公共项目元数据，不含客户记录、Owner token、PDF 或密码。

## 本地启动与复跑

Neo4j 已实际运行在独立 `dataquery-lineage-v1` Compose 项目；版本 5.26.33，镜像通过实际拉取的 digest 固定，1 GiB 容器上限、256 MiB 最大堆、64 MiB page cache、仅暴露环回 HTTP。未变更原 PostgreSQL/Redis 容器。

```sh
# 只在没有配置时执行；拒绝覆盖已有文件，随机密码不打印，权限 600。
PYTHONPATH=src .venv/bin/python scripts/metric_lineage.py init-config
# 已有 .env.neo4j 时直接启动；等日志出现 Started 后导入。
docker compose --env-file .env.neo4j -f compose.neo4j.yaml up -d
docker compose --env-file .env.neo4j -f compose.neo4j.yaml logs --tail 10 neo4j
PYTHONPATH=src .venv/bin/python scripts/metric_lineage.py import
PYTHONPATH=src .venv/bin/python scripts/metric_lineage.py import
PYTHONPATH=src .venv/bin/python scripts/metric_lineage.py query --field ods.order_items.discount_amount
# 沿原 Agent/会话入口发送：折扣字段变更会影响哪些指标
CI_NO_EXTERNAL_MODELS=1 .venv/bin/python -m pytest tests/test_metric_lineage.py -q
CI_NO_EXTERNAL_MODELS=1 NEO4J_INTEGRATION_TESTS=1 LINEAGE_REPORT=/tmp/lineage.json .venv/bin/python -m pytest tests/test_metric_lineage.py -q
CI_NO_EXTERNAL_MODELS=1 .venv/bin/python -m pytest -q
# 停止本轮容器并保留图数据；不要使用 --volumes。
docker compose --env-file .env.neo4j -f compose.neo4j.yaml down
```

CLI 查询不需要 PostgreSQL；完整 Agent/History 测试需要已有 PostgreSQL、管理角色和元数据只读配置。默认 CI 离线测试不启动 Neo4j，也不需要凭据。真实图验收通过显式环境变量开启，不改变 CI/CD 的默认服务和发布行为。

## 实际返回与验收

问题：`折扣字段变更会影响哪些指标`。未明确变化类型，返回两组：

- 字段值变化：`item_net_sales_amount`、`item_discount_amount`。
- 字段删除/重建受阻：上述两项及 `order_item_count`。
- 每项包含实际图路径和逐跳文件行号；完整表达式、Hash、版本在 `trace.routing.lineage.impacts`，工具事件为 `neo4j_field_impact`。

实际 Neo4j 导入两次均为 11 节点、12 关系；查询返回 5 条影响路径。净额的 VALUE 路径逐跳与上述加工 SQL 一致。明细计数只有 DELETE 路径，未因 Catalog 候选字段被误判为 VALUE。

字段不存在时返回 `field_not_found`；已存在但不在首版覆盖范围返回 `field_not_indexed`；不可读字段返回 `lineage_access_denied`；当前图缺失/不完整或源证据变更明确失败，不伪造路径。

真实断连验收只停止本轮新建 Neo4j 容器：Agent 返回 failed / `lineage_unavailable`，History Trace 一致，指标 SQL 和模型调用均为 0；随后恢复同一容器，原图和全部 5 条路径仍在。另覆盖真实 HTTP 认证失败。最初组合命令权限审核超时、未执行；拆分命令重试成功，不隐藏为产品失败或模型重试。

- Neo4j 专项：5 passed（4 个离线方法、1 个真实 Neo4j + PostgreSQL 闭环方法）。
- 综合相关回归：54 passed，包含 Neo4j、偏好记忆、日期、Planner、DAG、History、权限、HITL、Catalog恢复；没有把固定测试算作真实模型能力。
- Python 全量离线：253 passed、88 skipped，真实服务/平台测试按原开关跳过。
- 前端：11 passed，类型检查和生产构建通过；保留既有 >500 kB chunk 提醒。本轮无 Chrome 测试、无前端改动。
- 真实 Gemini / Embedding 调用 0，业务指标 SQL 0；PostgreSQL 仍执行身份元数据读取和随机测试 Owner 的 History 管理操作。

完整脱敏证据：`reports/warehouse/neo4j_metric_lineage_v1.json`。随机测试 Owner 的会话记录已清理，图中真实项目 SQL 元数据保留用于复跑。此完整批次包含图服务、固定查询、SQL 取证、真实验收与报告，超过通常 200 行提交约束；未拆成多次不可独立验收的提交。

下一步仅审查本分支并手动创建 PR。本轮不建设全目录血缘、任意字段自动解析或跨知识源联合规划。
