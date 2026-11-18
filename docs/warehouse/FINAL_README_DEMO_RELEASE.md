# README 与演示交付

2026-11-18，产品基线为远端 `main@8435257`。本轮更新 GitHub 入口、架构图、可选启动及五组演示，复用现有服务和测试。上一轮四份审计已推送到 `codex/final-gap-audit`，可[比较并人工合并](https://github.com/Boxer-M/dataquery-agent/compare/main...codex/final-gap-audit)。本分支从 main 单独建立，审计文档可先合并。

## 中文架构讲解

“这个项目把经营问数和文档证据放在同一个工作台。简单正式指标走 Fast Path，明确的指标定义直接查 Catalog。复杂请求由 Gemini 形成候选计划，代码校验指标、维度和时间口径。启用 LangGraph 后，Supervisor 决定委派数据或知识角色，两个角色分别判断自己的任务是否适合执行。工具仍经过 Capability、Owner 和 SQL/引用校验。既有 DAG 保存每个 Task 和 Attempt，History 关联同一次 Run。歧义指标可以通过真实 interrupt 暂停，服务重启后依靠 PostgreSQL Checkpoint 恢复。Redis 缓存确定性指标结果，命中仍检查当前数据库权限。”

[README 架构图](../../README.md#当前架构)展示外层协调与既有执行状态的关系。PDF 从原生文本/OCR 到人工修订、审核、授权发布后才进入 pgvector。Insight 保留数据库数值和文档原文各自的适用范围，未实现业务原因归因。

## 演示准备

按[本地手册](LOCAL_RELEASE.md)完成基础初始化。数据库 Compose 使用 `compose.yaml` 与 `compose.rag.yaml`，API 与 Vite 在宿主机运行。HITL 需要额外依赖、Checkpoint 初始化及两项开关；Redis 需要可选依赖、独立 Compose 和版本参数。服务密钥保存在被忽略的本地文件。PDF 索引及模型调用使用明确授权的合成样例。

## 五组现场脚本

1. **简单指标，约一分钟**。输入「2025年1月订单量是多少？」，核对 2312、事件时间与来源，追问「2月呢？」核对 2049。打开 History，查看 Run、Task、Attempt 与有效 Capability 关联。缓存另以同一 Owner、同参数的新消息在 TTL 内测试，消息幂等回放单独核对。
2. **多指标趋势与图表，约两分钟**。输入「查询2025年按月统计的订单量和成功支付金额」。合法计划返回共同月份下的结果表，检查订单时间与支付事件时间分别存在；模型提案若不合规则显示实际拒绝。再输入「查询2025年订单量按月拆分」，展示 12 个真实点并切换原始表格。输入「2025年1月订单量按渠道拆分」，核对 mobile 685、partner 207、phone 83、web 1337。多指标趋势表与单指标图各自独立，图表切换保持原 Query ID。
3. **SQL + PDF，约三分钟**。按[原演示步骤](RELEASE_DEMOS.md)上传授权三页合成样例，审核发布第 2 页。输入「2025年1月订单量是多少，并引用PDF扫描页的 Order count 原文。」核对 SQL 2312、PDF 第 2 页 `Order count: 48`、Hash 及审核文本行号，解释两者统计来源独立。查看 SQL、PDF、汇总任务；证据不可用时应保留合法数值并显示 partial。
4. **Supervisor，约两分钟**。在服务启动前设置 `DATAQUERY_SUPERVISOR=1`。沿用第 3 组已发布 PDF，明确标题、第 2 页和所需原文。打开实际 `orchestration` 与模型事件，检查 supervisor 委派、data 与 knowledge_agent 的独立 execute/defer 决策、受控工具和 insight。分别展示模型尝试与工具调用数；记录 partial 或拒绝的真实原因。[历史验收](LANGGRAPH_MULTI_AGENT_V1.md)使用真实 Gemini 生成及固定合成向量，并保留初次失败和收紧题目后的复测。
5. **HITL，约三分钟**。启用 Supervisor、HITL 和已初始化 Checkpoint。输入「2025年1月销售额」，待暂停后记下原 Run，重启自己启动的工作台服务并刷新原标签页。选择「折扣后商品销售金额」，核对原 Run 返回 231920.18。History 保留暂停与恢复，暂停前工具 0 次、恢复后 1 次。[历史浏览器记录](../../reports/benchmark/hitl_mvp_v1_browser.json)支持这条路径。重复、过期及跨 Owner 恢复由现有专项回归覆盖。

演示后只撤下自己新发布的合成文档，参照发布回执中的知识文档 ID。历史验收数字与现场新运行分别标示，外部服务不可用时展示对应历史版本记录。

## 本轮验证

- 前端 5/5 自动测试通过，涵盖代理边界与图表数据契约；TypeScript 和生产构建通过。构建仍提示图表 chunk 517.07 kB 超过 500 kB 警戒线，保持现有懒加载。
- Python 基础回归共 229 项，151 通过、78 个可选测试跳过；随后显式启用本地 PostgreSQL、OCR、Redis、HITL 等既有集成套件，完整集成回归共 229 项，227 通过、2 个可选真实模型测试跳过，用时 63.672 秒。实际 PostgreSQL 指标/ODS 对账、HTTP 固定提案、PDF/OCR、Redis、HITL、History 和 Catalog 恢复均在此次既有套件内执行。
- 本地网络权限下 `scripts/local_workspace.py --check` 通过。现有 PostgreSQL 容器运行中；`127.0.0.1:5173`、`localhost:5173` 代理健康接口与 `127.0.0.1:8000/api/health` 均返回 HTTP 200、`status=ok`。健康接口只证明 HTTP 可达。
- 一键启动实际触发端口占用保护：已有 API 与 Vite 正在监听，脚本在创建子进程前退出。保留现有服务。本轮未完成空闲端口下的新进程启动演示。
- 本轮真实 Gemini 生成及 Embedding 调用为 0；Supervisor、PDF 与跨重启浏览器演示引用已存的验收，固定提案集成测试单独统计。未重跑性能实验。

## 技术成果的使用条件

Redis v2 的对比是并发 5、每种模式三轮合计 360 请求；跨月品类关联聚合的热缓存相较关闭缓存，P95 140.34→118.08 ms、完成 QPS 43.71→52.79、指标 SQL 2481→0，并保留 360 次 ACL。请求使用固定意图，成本不包含真实 Gemini 推理。环境为 Apple M4 10 核/16 GB、macOS 15.7.7、Python 3.12.13、本机 Docker PostgreSQL 16.15/Redis 7.4.7。[完整条件与逐组结果](REDIS_PERFORMANCE_V2.md)

源码核对范围包括 `unified_agent.py`、`supervisor_graph.py`、`hitl.py`、`performance.py`、`visualization.py`、工作台启动脚本及 Compose。Planner v3 候选未合入。已知限制包括单指标图表范围、有限指标澄清恢复、手动数据版本失效、文档 ID 丢失后的找回和复杂经营归因。项目适合本地单人求职演示。


## 本轮回归复现

```sh
WAREHOUSE_POSTGRES_TESTS=1 KNOWLEDGE_POSTGRES_TESTS=1 API_POSTGRES_TESTS=1 \
CONVERSATION_POSTGRES_TESTS=1 DYNAMIC_POSTGRES_TESTS=1 DETAIL_POSTGRES_TESTS=1 \
DOCUMENT_OCR_TESTS=1 DOCUMENT_RAG_POSTGRES_TESTS=1 HITL_POSTGRES_TESTS=1 \
METRIC_QUERY_POSTGRES_TESTS=1 CAPABILITY_POSTGRES_TESTS=1 QUERY_ASSET_POSTGRES_TESTS=1 \
REDIS_POSTGRES_TESTS=1 PYTHONPATH=src:tests .venv/bin/python -m unittest discover -s tests
npm --prefix frontend test
npm --prefix frontend run build
```

公开文本检查未发现当前本地配置中的密钥、令牌及完整 DSN 值，Git 未跟踪原始数据、凭据配置及本地个人文档。该检查覆盖当前已知值，历史脱敏报告沿用已有审核范围。
