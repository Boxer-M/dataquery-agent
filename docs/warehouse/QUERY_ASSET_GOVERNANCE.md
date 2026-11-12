# 查询资产治理与复用

“2025 年 1 月各渠道按支付尝试次数列前 3 订单”可以先走受控动态 SQL。历史中的成功记录只证明当时有一次执行。开发者从该记录提取带参数的候选，独立验证并发布后，Agent 才能用同一个分析操作复用其 SQL。

| 记录 | 用途 |
|---|---|
| Query History | 所有者可追溯一次请求的参数、结果摘要和 Trace。旧记录保留当时引用的版本。 |
| Query Asset | 保存可复用的 SQL、业务契约、参数、依赖、验证证据和生命周期。 |
| Standard Metric | 正式指标目录继续维护独立的业务口径与状态。 |

## 发布和执行

- `CANDIDATE → VALIDATED → PUBLISHED`。候选和已验证版本可拒绝，已发布版本可标记 `STALE` 或退役，过期版本可退役。替代版本发布时在同一事务退役旧版本并记录操作者。定义、SQL 和契约随版本保留。
- 首个可发布操作沿用 `warehouse/dynamic_query_contract.json` 的渠道支付尝试排行。按 `paid_at` 左闭右开筛选，所有支付状态均计数，按订单和渠道汇总。各渠道按次数降序、订单号升序取前 N。日期与 N 均绑定参数，外层结果上限使用执行器约定。
- 导入历史必须通过现有所有者检查。候选只复制已执行的参数化 SQL 和来源查询 ID。SQLGlot 消除排版差异；规范 SQL、业务契约、参数结构和依赖指纹相同时复用候选。已验证实现可使用已知等价的 CTE 别名，并生成独立 SQL 哈希。
- 验证复用动态 SQL AST、精确语义校验、DWD/DIM 权限检查、查询成本与超时、结果字段及粒度校验。4 组真实参数使用独立 ODS 查询计数，再由 Python 实现渠道内排行对账。确定性测试另外覆盖空结果、并列和非法排序。
- Standard Metric 和固定 Detail 模板继续使用原入口；已有 Detail 模板可登记为候选用于溯源，当前仍由版本化 Detail 契约与执行器管理发布和执行。新资产仅进入已识别的同一动态分析路径。无匹配资产时保留动态 SQL；已匹配资产过期或注册表不可用时返回错误。
- 运行时每次读取当前 PostgreSQL 结构。删除、重命名、字段类型、键或 JOIN 基数变化均能阻止执行。事件时间或业务契约变化需人工确认；相关模型实现或版本变化需重新验证；无关字段新增保持可复用。运行时持有操作级共享锁，发布和失效状态修改持有互斥锁。执行器在查询事务中锁定涉及的业务关系并复查权限和结构。
- 历史保留资产 ID、两个版本、SQL 哈希、元数据指纹、安全参数和 Trace。执行前发现漂移会在结果和 Trace 中记录，管理者使用 `impact --mark` 持久化 `STALE`。数据源自身的无声明语义变动仍需要发布流程或数据质量监控发现。

## CLI

首次配置或重复执行迁移。初始化后，服务部署只需提供注册表读者配置；管理员配置只供开发者的治理命令。两个本地凭据文件均被 Git 忽略且权限为 `600`。

```bash
PYTHONPATH=src .venv/bin/python scripts/provision_query_assets.py
export PYTHONPATH=src
.venv/bin/python -m dataquery_agent.query_asset_cli from-history "$QUERY_ID"
.venv/bin/python -m dataquery_agent.query_asset_cli from-detail payment_failure_then_capture
.venv/bin/python -m dataquery_agent.query_asset_cli validate attempt_rank_by_channel 1
.venv/bin/python -m dataquery_agent.query_asset_cli publish attempt_rank_by_channel 1
.venv/bin/python -m dataquery_agent.query_asset_cli impact attempt_rank_by_channel 1 --mark
.venv/bin/python -m dataquery_agent.unified_agent '2025年1月各渠道按支付尝试次数列前3订单'
```

`propose <sql_file>` 录入开发者提交的 SQL。根据返回的实际版本执行 `validate` 和 `publish`。注册表管理者可查看候选、证据和生命周期；普通业务执行角色无法读取或写入注册表。

## 复现评测

在尚未发布同一操作的测试注册表，使用 `PYTHONPATH=src .venv/bin/python tests/evaluate_query_assets.py --mode live`。用 `--mode fixed` 复现固定模型输出。演示会提交三个资产版本，建议在独立测试数据库运行。已完成演示的环境可直接执行统一 Agent 查询，或设置 `QUERY_ASSET_POSTGRES_TESTS=1` 运行资产集成测试。

[评测报告](../../reports/warehouse/query_assets_v1.json)保留历史导入和发布证据、每轮工具结果、Trace、独立 ODS 对账、首次失败及模拟字段重命名的影响。字段变化只注入测试快照，恢复阶段回到真实契约并发布重新验证的实现。评测使用真实 PostgreSQL 上的 SQL Shed 公开合成测试数据。
