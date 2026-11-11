# 受控明细查询执行

统一 Agent 在能力解析返回 `DETAIL_QUERY` 后，可执行具有已批准模板的明细分析。未登记模板的能力仍返回规划结论。标准指标和经营分析沿用 Metric Query Service。

```sh
PYTHONPATH=src .venv/bin/python scripts/provision_detail_reader.py
PYTHONPATH=src .venv/bin/python -m dataquery_agent.unified_agent \
  '2025年1月有多少订单先支付失败，后来成功支付？'
```

配置脚本一次性创建 `sqlshed_detail_reader`，授予四个 DWD/DIM 模型的必要列 SELECT。凭据保存在权限为 0600、受 Git 忽略的 `.env.detail-query`。业务执行使用专用账号。CLI 默认启用执行器，Python 可将 `DetailQueryExecutor.local()` 注入 `UnifiedAgent(service, capability, detail_executor)`。

## 业务规则与模板

[执行契约](../../warehouse/detail_query_contract.json)固定模板 ID、版本、文件摘要、分组、来源和返回 Schema。粒度、事件时间、合法关联与质量约束引用[能力契约](../../warehouse/capability_contract.json)，执行前核对对应业务规则的摘要。规则变动须重新核准模板。统一入口中的明细结果包含时间范围、口径、粒度、约束、数据质量提示、来源、模板版本和执行 Trace。

| 分析 | 规则 |
|---|---|
| `payment_failure_then_capture` | 两个支付事件都须落在同一半开窗口。对同一订单，存在 `failed` 事件且有严格较晚的 `captured` 事件时计入。同刻不推断顺序，多次事件仍按订单去重。按渠道时先去重，再多对一关联订单。总量 0 是合法结果，按渠道无命中返回空分组。 |
| `sales_by_order` | 按订单汇总商品明细净额，采用下单时间。可增加渠道或当前品类分组，关联方向均为事实到唯一目标键。保留所有订单状态。金额以十进制字符串返回，超过 100 行时保留排序前缀并标记截断。 |

模板位于[版本化 SQL 目录](../../sql/warehouse/detail)。本轮提供支付总量、支付按渠道，以及订单净额的四种分组变体。日期由程序绑定，分组只用于选择固定模板。当前只登记日期和维度，不接收额外明细过滤或任意 SQL。

## 安全执行

1. [模板注册表](../../src/dataquery_agent/detail_templates.py)验证 SQL 文件摘要、绑定参数、SQLGlot 结构、函数和准确的物理来源集合。明细专用白名单仅包含对应 DWD/DIM 模型。原 ODS 查询入口沿用原白名单。
2. [明细执行器](../../src/dataquery_agent/detail_executor.py)刷新元数据并重新解析能力，核对参数、版本、来源、字段、关联基数和粒度。专用角色在只读事务中重新验证身份与权限。以空结果 SELECT 核验必要列并持有关系锁，再对照当前列及约束。
3. [只读执行底座](../../src/dataquery_agent/sql_executor.py)设置 5 秒连接和语句超时，通过不执行数据查询的 EXPLAIN 检查估算成本，超过 100,000 个 PostgreSQL 规划成本单位拒绝执行。结果用服务器游标读取至多 101 行，用第 101 行判断是否截断。输出检查字段、类型、有限 Decimal 和粒度唯一性。
4. Agent 根据已验证结果生成中文文本。Trace 连接 Agent、能力判定、模板及执行，记录安全的错误码和耗时。明细执行结果不自动进入正式指标目录。

成本估算与实际运行时间可能不同，语句超时独立生效。现有逻辑关联依赖数仓发布验收，元数据检查不替代数据质量检查。后续若接入生成式 SQL，应在复用执行底座前单独验证 AST、字段、业务语义、关联和成本。

## 验证与复现

```sh
DETAIL_POSTGRES_TESTS=1 CAPABILITY_POSTGRES_TESTS=1 METRIC_QUERY_POSTGRES_TESTS=1 \
  WAREHOUSE_POSTGRES_TESTS=1 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
PYTHONPATH=src .venv/bin/python tests/evaluate_controlled_detail.py
```

测试覆盖六个模板的独立 ODS 对账、非法字段和来源、写操作、角色权限、模板版本、结构漂移、估算成本、实际 PostgreSQL 超时和 Agent 路由。合成事件覆盖同刻、反序、重复失败与成功、两个渠道及窗口边界，实际执行模板后得到 2 笔，web 和 mobile 各 1 笔。测试侧使用独立只读账号读取 ODS 对照，Agent 业务执行角色没有 ODS Schema 使用权。

真实结果中，2025 年 1 月及 2023–2025 年验证窗的先失败后成功订单均为 0。2025 年 1 月订单净额查询返回按订单排序的前 100 行，与独立 ODS 参考结果一致，并标记截断。第三轮真实 Gemini 明细评测 3/3，首轮 1/3、第二轮 2/3 的输出及 Trace 一并保留。详见[版本化用例](../../tests/fixtures/controlled_detail_v1.json)与[验收报告](../../reports/warehouse/controlled_detail_v1.json)。

稳定回归发现 118 项，117 项通过，1 项既有可选 Gemini 方法跳过。单独开启该方法的两轮完整尝试中，既有语义入口的销售额澄清与渠道用例出现模型不可用或分组提取偏差，均记录在报告。新增受控明细的第三轮真实 Gemini 测试与上述既有可选方法分别统计。
