# CI/CD Engineering v1

本轮从 `main@119b997` 创建 `codex/ci-cd-engineering-v1`。原仓库使用 unittest 和本机 opt-in 集成测试，没有 GitHub Workflow、pytest 配置或应用 Dockerfile。pytest 可以收集既有 unittest，新增依赖仅为测试工具 `pytest==8.4.2` 和 Workflow 检查所需 `PyYAML==6.0.3`。业务代码沿用 main；日期空格修复保存在独立本地分支 `codex/demo-calendar-fix@fb34dda`。

## 自动 PR 检查

[ci.yml](../../.github/workflows/ci.yml)在 pull_request、main 推送及当前 CI 分支推送时运行。三个任务分别执行 Python、前端、隔离集成与启动冒烟。仅授予 contents:read，取消同分支旧检查，限制任务时间；失败保持非零退出。JUnit、前端构建、集成和 HTTP 冒烟证明保留 7 天，上传范围排除凭据、数据库导出及原始数据。

Python 3.12 安装现有 runtime、PDF、LangGraph、Redis 与 CI 依赖后执行 pip check、公开文件检查和 pytest。前端 Node 24 使用 npm ci、原有 5 项测试和生产构建。测试钩子在 `CI_NO_EXTERNAL_MODELS=1` 时拒绝创建 Gemini 客户端，Workflow 不引用模型 Secret。固定提案和固定向量仅验证执行及权限链路，报告标明其来源。

集成环境复用 `compose.yaml`、`compose.rag.yaml`、`compose.redis.yaml`，用 [compose.ci.yaml](../../compose.ci.yaml)关闭宿主端口映射，并让测试容器共享其专属数据库网络。现有客户端的 127.0.0.1:5433/6381 在该隔离网络内可用。Python 镜像以非 root 用户运行。数据库临时管理员仅用于初始化、建模与隔离测试，查询角色继续由现有脚本生成最小权限。

[bootstrap.py](../../scripts/ci/bootstrap.py)仅允许在新 `/app` 容器且不存在本地配置时执行。下载完整 SQL Shed，复用 Parquet 审计、事务导入及数仓构建，初始化服务角色、Catalog 固定向量和 Checkpoint。导入 CLI 的旧 Olist 验证被保留在产品代码中；CI 使用其 SQL Shed 函数，不构造 Olist 订单。下载及数据库初始化失败会阻止后续验收。

[run.py](../../scripts/ci/run.py)显式开启既有 PostgreSQL、HITL、Redis 和知识权限测试，检查关键套件实际执行，防止误以 skipped 通过。随后启动真实环回 Uvicorn，用固定意图发送 HTTP 订单量查询，验证 2312 与保存的 History/Trace。测试数据库和卷在 job 末尾清理。Linux 跳过 OCR/PDF 扫描集成；原生 PDF、文件限制和非扫描审核测试在无密钥测试中保留。

## 本地临时环境

下面的测试凭据仅用于新建的无宿主端口临时环境。Compose 项目名必须与已有环境独立。

```sh
export POSTGRES_USER=dataquery_ci_admin POSTGRES_PASSWORD=ci-only-not-production POSTGRES_DB=olist
export COMPOSE_PROJECT_NAME=dataquery-ci-review
export COMPOSE_FILE=compose.yaml:compose.rag.yaml:compose.redis.yaml:compose.ci.yaml
mkdir -p artifacts && chmod 777 artifacts
docker compose up --build --abort-on-container-exit --exit-code-from check check
# 核对 artifacts/integration.xml 与 smoke.json 后清理此专属项目
docker compose down --volumes --remove-orphans
```

应用 runtime 镜像可单独构建 `docker build --target runtime -t dataquery-agent:local .`。原始 PDF、凭据、个人文档与原始数据均从构建上下文排除，只有已授权合成 PDF 样例进入镜像。已有工作台前端仍由 Vite 本地代理运行。没有公网部署步骤或生产 CD 声明。

## 手动 Gemini 评测

[agent-evaluation.yml](../../.github/workflows/agent-evaluation.yml)仅支持 workflow_dispatch、main 和显式授权复选框。仓库维护者在 `agent-evaluation` Environment 配置 `GEMINI_API_KEY`，建议配置环境审批。Workflow 合入默认分支后才能在 Actions 显示手动触发入口。[GitHub 手动触发规则](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)

[live.py](../../scripts/ci/live.py)复用现有 13 条 Golden、真实 Gemini 适配器、safe_generate、Budget、ODS 数值核对和原评分器，不读取封存 Holdout。允许外发范围为正式 Catalog/原规划上下文及已授权合成退款规则 Markdown。PDF/OCR 和新增技术文档排除在这个低成本子集中。上限为 40 次生成尝试、10 次 Embedding、400 万预留 Token、5 美元历史价格预留及 30 分钟 job 超时。费用估计存在模型价格变化风险，调用数与时间上限另行生效。

只有合成退款规则建立真实 Embedding 索引，正式 Catalog 固定向量只服务精确身份定位。每次外部调用、重试、错误、Token、耗时及最终 Router/Tool/SQL/引用/Task 分数保留在 `live-report.json`。N/A 和需要人工核对的语义支持不计通过。逐题对照既有 13 条历史报告，同时标记代码、模型及快照条件不同，差异用于诊断。未达到 100% 不会把随机模型结果直接变成 CI 阻断；初始化或运行器故障仍失败，部分报告尽可能保留。

## 安全与验证记录

公开文件检查拒绝被跟踪的 .env、个人文档、原始数据、备份、大文件和可识别密钥。专项测试向检查器注入私有路径与私钥标记，确认抛出错误；还核对默认 PR Workflow 无 Secret、无 continue-on-error，手动模型流程受到授权和分支约束。规则式扫描不能证明不存在所有类型的秘密。[GitHub Secret 使用边界](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets)

本地及远端实际验证结果在本节追加。真实模型 Workflow 本轮不自动运行，也不读取或上传本机 Gemini 密钥。

本地无密钥 pytest 为 153 通过、78 跳过；前端 5 项通过、TypeScript 与生产构建通过；pip check、Compose 合并及公开文件检查通过。受控失败样例确认 pytest 返回退出码 1。首次实际推送运行 [44233887977](https://github.com/Boxer-M/dataquery-agent/actions/runs/44233887977) 的 Python 与前端任务通过，集成任务失败，保留为初始化诊断记录。

本地同源隔离环境复现了导入后缺少统计信息导致的 ODS 明细退货率核对超时。bootstrap 在 COPY 后执行 ANALYZE，保留原有 60 秒建模核对超时；复测已通过 DWD/DIM、DWS、ADS 和角色初始化，继续执行原有集成用例。

隔离 Linux 同源镜像复测结果为 **120/120** 现有集成测试通过（25.35 秒），真实 Uvicorn HTTP 冒烟确认订单量 **2312**、历史 Trace 一致、实际外部模型调用 **0**。同时修正冒烟脚本创建会话时的 POST 方法。该次使用已冷构建的依赖镜像和只读源码挂载，最终无挂载镜像及远端结果单独记录。


### 已通过的验收与当前限制

- 当前完整 Dockerfile 的无源码挂载隔离运行通过 **120/120** 集成测试（24.35 秒），真实 HTTP 冒烟返回订单量 **2312**，History 与响应 Trace 一致。镜像以非 root 用户执行，两个本轮临时 Compose 项目及其数据卷均已清理。
- 修正提交 `bd3fb37` 的真实 [GitHub Actions 复跑 44235342712](https://github.com/Boxer-M/dataquery-agent/actions/runs/44235342712)中，Python、前端、integration-smoke 三个任务均为 **success**。保留首次失败与修正后成功两条运行记录。
- 真实自动触发已验证为 push。pull_request 事件经过 Workflow 契约检查，本轮未创建 PR，因此实际 PR 事件留待用户建 PR 后确认。
- 手动 Gemini Workflow 及预算、Artifact 路径已配置；本轮没有配置仓库 Secret、触发收费评测或声称生产部署。该流程需合入 main、配置 Environment Secret 并由维护者显式触发后才能完成真实模型验收。
- 本地 pytest 的总分母和隔离集成分母有重叠，应分别引用。原有图表 chunk 大于 500 kB 的构建警告仍存在。


### Catalog 恢复波动的复现与隔离

用户提供的 04:14 运行日志确认 [44235944726](https://github.com/Boxer-M/dataquery-agent/actions/runs/44235944726)为 **119 通过、1 失败**，失败位于 `test_catalog_recovery` 的恢复检查点断言。两个指标 SQL 都成功，缺失 Catalog 也返回预期 partial，但没有生成 recovery。随后文档提交对应的 [44237170739](https://github.com/Boxer-M/dataquery-agent/actions/runs/44237170739)又通过，说明原环境存在波动，单次绿色不足以确认解决。

源码在查询前后同时比较源数据/合同指纹和 PostgreSQL 全局 MVCC snapshot。受控 PostgreSQL 实验在第一次指标查询后提交一个仅调用 `pg_current_xact_id()` 的管理事务，得到快照 `876:876:` → `877:877:`；数据与合同指纹一致，但 recovery 被拒绝。这证明独立事务可以触发该保守保护。远端历史日志没有两次 proof，也没有具体后台 writer 记录，无法回溯证明当时一定是 autovacuum。

修正范围为一次性 CI 数据库：`compose.ci.yaml` 设置 `autovacuum=off`，bootstrap 校验该条件，并继续在导入后显式 ANALYZE。生产 Compose 与全部 Agent、安全、Owner、SQL 和恢复逻辑保持现有实现。这样消除已知的后台维护事务源，正常恢复用例仍保留原断言。

新增真实 PostgreSQL 回归主动提交一次事务，验证两个 SQL 成功、源数据/合同指纹不变、快照发生变化时，结果仍 partial 且 History 无 recovery。它证明测试环境隔离没有绕过保护。真实服务遇到无法确认一致性的状态，仍可能保守地不提供恢复入口，面向来源的版本证明可作为独立后续设计。

本次镜像测试依赖下载曾遇到一次 PyPI 读取超时，测试尚未启动；原构建日志保留，之后一次有界重试完成下载。费用模型评测继续未触发。PR 实际触发待用户创建 PR 后验证。

本轮本地无密钥 pytest 为 **153 通过、79 跳过**。完整无源码挂载镜像在新测试卷中 **121/121** 集成通过（26.56 秒），真实 HTTP 冒烟通过，随后 10 轮 Catalog 恢复专项各 3 项全部通过。另一个同版本依赖镜像加只读测试源码的独立环境也 **121/121** 通过（26.43 秒）。重复轮次用于稳定性检查，不能加总为新增独立用例。新增主动事务拒绝用例在本机真实 PostgreSQL 单独验证通过。上述验证未调用 Gemini。远端结果以本次修正提交关联的 Actions 为准。
