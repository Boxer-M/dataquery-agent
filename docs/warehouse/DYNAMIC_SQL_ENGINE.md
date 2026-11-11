# 语义驱动动态 SQL

动态路径用于现有标准指标和明细模板之外的三类已批准分析。Unified Agent 保留正式指标和明细模板的既有路径。动态需求必须匹配完整的业务表达和明确日期，已有工具失败不会触发动态回退。

```sh
PYTHONPATH=src .venv/bin/python scripts/provision_dynamic_reader.py
PYTHONPATH=src .venv/bin/python -m dataquery_agent.dynamic_sql_engine \
  '2025年1月各渠道按支付尝试次数列前3订单'
```

CLI 与 Unified Agent 共用路由。Python 接口为 `DynamicSqlEngine(dsn, snapshot).execute(question, requirement, runtime)`。调用方提供结构化 `DynamicRequirement` 和 `AgentRuntime`，或直接通过 `UnifiedAgent(..., dynamic_engine=DynamicSqlEngine.local())` 处理自然语言。专用登录配置保存在 Git 忽略的 `.env.dynamic-query`，文件权限为 0600。业务角色只有批准列的 SELECT。

| 分析 | 示例 | 业务粒度与约束 |
|---|---|---|
| 按订单筛选尝试次数 | 2025年1月按订单统计支付尝试次数，保留大于1次的订单 | 支付事件时间，按订单计数尝试。允许显式限定失败或成功状态，HAVING 严格大于阈值 |
| 渠道内订单 Top N | 2025年1月各渠道按支付尝试次数列前3订单 | 支付事实多对一关联订单，先聚合到渠道与订单，再用 ROW_NUMBER 在各渠道内排名。次数同值按订单号升序 |
| 退货订单按当前品类 | 2025年1月按当前品类统计有退货记录的订单数 | 退货事件时间，多对一关联当前商品品类，各品类内去重订单。跨品类不可相加 |

## 实现边界

1. [需求解析与能力检查](../../src/dataquery_agent/dynamic_requirements.py)核对日期、参数、已登记事件时间、粒度和关联键。Capability Resolver 核验当前模型、字段和批准的多对一关联。额外维度、支付分摊或不同业务时钟没有通用动态回退。
2. [Context Builder](../../src/dataquery_agent/dynamic_context.py)提供本次所需的列和类型、主键、粒度、关联、状态含义、质量限制、参数与元数据版本。上下文只包含选择操作所需的 SQL 子句约束。Gemini 根据这些约束生成候选 SELECT，返回参数、来源、输出字段、步骤及未解决事项。模型的步骤说明不作为执行依据。
3. [AST 安全校验](../../src/dataquery_agent/dynamic_validation.py)复用 SQLGlot Guard，限制为单条 SELECT 及批准的 CTE、模型和函数。字段必须经当前列白名单解析。候选禁止 SQL 字面量，日期、阈值、状态和行数使用绑定参数，模型返回的参数必须与程序验证的参数完全一致。
4. 语义校验器从批准的操作独立编译参考 AST，核对候选的窗口、关联、去重、聚合、分组、排序、排名和行数限制。执行的是经过验证的规范化 AST。当前只接受可确定验证的结构，对其他等价写法可能保守拒绝。
5. [执行引擎](../../src/dataquery_agent/dynamic_sql_engine.py)在生成后刷新能力版本，再通过既有只读执行底座检查数据库角色、列权限、事务只读、结构漂移、估算成本和超时。查询最多读取 101 行，用第 101 行标记截断。返回结果核对类型、粒度、次数与名次，中文说明来自真实结果。Trace 记录候选、拒绝原因、执行参数、SQL、模型与 Prompt 版本及数据来源。

仅 JSON 格式、SQL 语法和字段解析错误可以修复一次。权限不足、语义无法证明、未解决条件和不支持能力不会通过重试绕过。本批采用小范围分析语法与严格 AST 校验，避免用模型的 SQL 自评替代业务证明。

## 评测

```sh
PYTHONPATH=src .venv/bin/python tests/evaluate_dynamic_sql.py --mode fixed
PYTHONPATH=src .venv/bin/python tests/evaluate_dynamic_sql.py --mode live
DYNAMIC_POSTGRES_TESTS=1 DETAIL_POSTGRES_TESTS=1 CAPABILITY_POSTGRES_TESTS=1 \
  INTENT_GEMINI_TESTS=1 METRIC_QUERY_POSTGRES_TESTS=1 WAREHOUSE_POSTGRES_TESTS=1 \
  PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

[版本化用例](../../tests/fixtures/dynamic_sql_v1.json)涵盖 HAVING、窗口函数、按订单去重、关联放大、半开窗口、状态、空结果、权限、超时和截断等边界。独立 ODS 参考侧使用只读账号，分别采用按订单计数、Python 渠道内排序和品类内订单集合。合成用例验证起点、终点、状态和重复尝试。用户查询的执行角色不能访问 ODS。

两轮真实 Gemini 均为 3/3，与 ODS 逐行一致。2025 年 1 月按订单筛选支付尝试数返回排序后的前 100 行并标记截断，渠道内 Top 3 返回 12 行，当前品类退货订单数返回 24 行。所有尝试、实际 SQL、参数、结果和耗时见[验收报告](../../reports/warehouse/dynamic_sql_v1.json)。既有可选 Gemini 用例历史上出现过模型不可用和分组提取偏差，保留原测试与独立记录。
