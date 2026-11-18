# 项目最终缺口审计

审计基线为远端 `main` 的 `8435257`。本轮仅检查源码、配置、Git 历史及既有报告，并完成本机配置、PostgreSQL 监听及知识索引的只读检查。没有重新执行真实模型、浏览器或压测，也没有改动产品代码。

## 版本核对

| 能力 | 已进入 main 的合并提交 | 代码入口 |
| --- | --- | --- |
| Runtime Identity & Trace | `197c348`，#49 | [runtime_trace.py](../../src/dataquery_agent/runtime_trace.py) |
| 受控工具执行接口 | `93e78ae`，#50 | [metric_tool.py](../../src/dataquery_agent/metric_tool.py)、[specialists.py](../../src/dataquery_agent/specialists.py) |
| LangGraph Multi-Agent | `c7e6ad8`，#51 | [supervisor_graph.py](../../src/dataquery_agent/supervisor_graph.py) |
| HITL 持久化澄清 | `4335666`，#52 | [hitl.py](../../src/dataquery_agent/hitl.py) |
| Redis 性能控制 | `590a8ef`，#53 | [performance.py](../../src/dataquery_agent/performance.py) |
| Redis Benchmark v2 与阶段计时 | `8435257`，#54 | [benchmark_redis.py](../../scripts/benchmark_redis.py)、[REDIS_PERFORMANCE_V2.md](REDIS_PERFORMANCE_V2.md) |

按提交可达性核查所抓取的远端分支，唯一仍有未进入 main 提交的远端分支为 `origin/feature/business-planner-reliability-v3`（`4cb2227`）。另外，本地 `codex/planner-v3-ablation`（`f46769b`）和旧 `feat/gemini-sql-generation`（`eaa9cb3`）各有分叉提交。这三者的未合并改动均排除在产品能力与简历结论之外。另有七份既有未跟踪审计文件保留在工作区。

## A–F 场景

| 场景 | 当前状态与证据 | 演示边界 |
| --- | --- | --- |
| A 正式指标与缓存 | 已实现。指标工具进行当前授权复核，再走 Redis 或固定 SQL。见 [metric_tool.py](../../src/dataquery_agent/metric_tool.py) 和 [performance.py](../../src/dataquery_agent/performance.py)。 | Redis 与数据版本均启用，同一 Owner、参数、版本且在 TTL 内复用结果。缓存命中仍需 ACL。 |
| B 多指标趋势与 ECharts | 部分实现。同维度多指标计划可返回表格，现有图表契约批准订单量的月度和渠道两种单指标分组。见 [business_results.py](../../src/dataquery_agent/business_results.py) 和 [visualization.py](../../src/dataquery_agent/visualization.py)。 | 可分别展示多指标表格和单指标折线图，合并多指标趋势图未实现。 |
| C SQL + PDF | 已实现，一条正式指标及一个 PDF 证据任务，两个子结果独立保留，引用再校验。见 [business_query_planner.py](../../src/dataquery_agent/business_query_planner.py) 和 [rag/routing.py](../../src/dataquery_agent/rag/routing.py)。 | PDF 文档口径和数值独立展示。无有效证据时显示 partial 或失败。 |
| D 真实 Supervisor 委派 | 已实现，启用后可在受支持计划上调用 Gemini 协调，再由专业角色决策，最终复用原执行门。见 [supervisor_graph.py](../../src/dataquery_agent/supervisor_graph.py)。 | 简单指标、正式 Catalog 等仍可走原路径。专业工具选择本身为受控代码。 |
| E HITL 跨重启 | 已实现，限定可验证的单指标口径澄清。原 Run 的 PostgreSQL Checkpoint 与 History 共同支持恢复。见 [hitl.py](../../src/dataquery_agent/hitl.py)。 | PDF 审核使用独立业务流程。通用 Graph 暂停恢复和崩溃后自动重放未实现。 |
| F 经营变化、维度贡献与解释 | 部分实现。跨期差值及变化率、维度排行、证据陈述分别存在。Planner 对归因与贡献请求有能力限制，Insight 汇总证据与可用性。见 [business_plan.py](../../src/dataquery_agent/business_plan.py)、[business_results.py](../../src/dataquery_agent/business_results.py) 和 [supervisor_graph.py](../../src/dataquery_agent/supervisor_graph.py)。 | 缺少在相同两期和同一维度上对总增减额做贡献对账、再添加有效 PDF 背景的端到端能力。没有因果解释。 |

## README 与遗留入口

- [README](../../README.md) 的本地工作台、PDF 和 SQL+PDF 描述与代码契合。专业工具选择的确定性描述适用于执行门。首页未覆盖已合并的可选 Supervisor、HITL、Redis 和对应预检，演示者只按基本启动步骤会漏掉这三条路径。
- [local_workspace.py](../../scripts/local_workspace.py) 检查基本配置、前端依赖和 PostgreSQL 监听。[API](../../src/dataquery_agent/api.py) 的健康接口确认进程可达。可选依赖、Checkpoint Schema、Redis、Gemini 和实际查询应按[演示清单](PROJECT_DEMO_CHECKLIST.md)逐条确认。本轮本机基础预检通过。
- [配置示例](../../.env.example) 的 `APP_ENV`、`LLM_API_KEY` 在 `src` 与 `scripts` 内未发现读取点。旧 `AGENT_DB_USER/PASSWORD` 仍服务 Olist 路径，SQL Shed 正式指标使用自己的只读身份。[Unified Agent](../../src/dataquery_agent/unified_agent.py) 的 `state` 入参属于保留接口，传值会拒绝，会话状态由 Manager 管理。
- Planner 的 `task_query` 兼容入口与当前统一工具入口并存，专业角色的 `execute` 与 `dispatch` 也有相邻职责。现有 CLI 与测试通过这些接口保留旧调用约定，缺乏可证明的删除收益。Supervisor、HITL、Redis 的显式开关均有实际运行路径。

## 优先级与最小改动

| 优先级 | 证据及缺口 | 最小方案与涉及文件 | 回归风险与投递判断 |
| --- | --- | --- | --- |
| P0 演示与简历口径 | 首页省略可选依赖，且历史评测与当前版本不同。见 [README](../../README.md)、[本地手册](LOCAL_RELEASE.md)、[证据表](RESUME_EVIDENCE.md)。 | 后续把 README 链接到可选启动与实测报告，录制前按清单检查空白环境中的必需组件。本轮已交付替代清单。 | 低，优先补文档和做演示复验即可。 |
| P1 经营变化分析 | F 无贡献对账，现有跨期比较只能给出各指标变化。见 [Planner](../../src/dataquery_agent/business_plan.py) 与 [聚合输出](../../src/dataquery_agent/business_results.py)。 | 如需要一个更完整的演示，限定一个 Catalog 已支持的可加金额、一个维度和两个等口径窗口。由现有 Planner/Validator 绑定计划，Decimal 计算每组本期减基期、独立总额对账，PDF 作为可选背景。涉及这两处和 [Insight](../../src/dataquery_agent/supervisor_graph.py)。 | 中。先验证缺组补零、NULL、金额可加性、组覆盖、总差为零和截断；证据缺失保留 partial。不阻塞投递。 |
| P2 多指标图表与运维扩展 | [图表契约](CHART_DATA_CONTRACT_V1.md) 单指标，现有本地安全模型和 Checkpoint 恢复范围有限。 | 保留表格和单指标图，继续使用本地权限模型和现有恢复流程。 | 现阶段保留已有范围。 |

**投递判断：可以开始投递。** 以本地企业风格演示展示正式指标、受控 SQL、PDF 可信引用、真实 Supervisor、持久化澄清和有边界的 Redis 压测。投递前的最小工作是核对启动与演示步骤、引用实测分母。若面试需要展示完整的经营变化解释，再追加一条受控的维度贡献场景即可。
