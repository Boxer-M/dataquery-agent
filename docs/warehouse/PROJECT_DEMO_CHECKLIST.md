# 项目演示核对清单

面向本地单人演示。数字引用历史实测基线。本轮只读预检通过，下面的浏览器流程是现场复现清单。

## 启动与安全

1. 在仓库根目录按[本地安装手册](LOCAL_RELEASE.md)准备 Python 3.12 `.venv`、Node 22.12+、PostgreSQL/pgvector、SQL Shed 数仓及服务只读角色。扫描 PDF 需要 macOS Apple Vision 和 Xcode Command Line Tools。文本 PDF 可单独演示。
2. 运行 `.venv/bin/python scripts/local_workspace.py --check`。基础预检只检查本地配置、前端依赖及数据库监听。浏览器入口 [127.0.0.1:5173](http://127.0.0.1:5173/)；API 保持环回绑定，Bearer 由 Vite 服务端代理注入。
3. 只配置必要功能。演示 D/E 时安装 `requirements-langgraph.txt`，运行 `PYTHONPATH=src .venv/bin/python scripts/provision_hitl.py`，并设置 `DATAQUERY_SUPERVISOR=1 DATAQUERY_HITL=1`。演示 A 的缓存时按 [Redis 手册](REDIS_PERFORMANCE_V2.md)安装依赖、启动 Compose Redis，并设置 `DATAQUERY_REDIS_URL=redis://127.0.0.1:6381/0`、`DATAQUERY_DATA_VERSION` 为当前数据版本、`DATAQUERY_PERF_HEADERS=1`。
4. 带所需开关运行 `.venv/bin/python scripts/local_workspace.py`。先问订单量确认指标服务，再确认 PDF 审核和索引配置。外部调用只使用获授权的合成样例与上下文，录屏隐藏配置、凭据、Owner 标识和私有内容。

## 场景与通过标准

| 场景 | 输入和复现 | 现场核对 |
| --- | --- | --- |
| A 正式指标及缓存 | 同一 Owner 在 TTL 60 秒内，以两个新消息提交相同的「2025年1月订单量是多少？」 | 两次结果均为 **2312**，与 `ods.orders` 一致。第二次缓存命中，保留当前 ACL 复核并省去指标 SQL。用[压测脚本](../../scripts/benchmark_redis.py)或本地性能头核实，消息回执重复不能作为缓存证据。 |
| B 表格与图表 | 分别问「2025年订单量按月拆分」「2025年1月按渠道统计订单量」。再展示支持共同维度的多指标结果表。 | 12 个月点和 4 个渠道柱与原始结果一致。1 月 mobile **685**、partner **207**、phone **83**、web **1337**。图表与表格切换保持 Query ID。多指标保留表格。[图表契约](CHART_DATA_CONTRACT_V1.md) |
| C SQL 与 PDF | 按[发布演示](RELEASE_DEMOS.md)上传 [授权合成 PDF](../../output/pdf/document-extraction-sample.pdf)，审核并发布扫描页。问「2025年1月订单量是多少，并引用PDF扫描页的 Order count 原文。」 | SQL **2312**，PDF 第 2 页原文 `Order count: 48`。在 History 查独立 SQL/PDF 节点、汇总节点、Capability、页码、版本及 Hash。PDF 不可用时保留合法 SQL 结果并提示 partial。 |
| D 真实 Supervisor | 启用 Supervisor，优先复现[验收记录](LANGGRAPH_MULTI_AGENT_V1.md)中已明确授权 PDF 第 2 页的联合题。对照同次会话的单指标 Fast Path。 | Trace 区分模型计划、Supervisor 委派、专业角色模型决策和受控工具；既有记录也包含联合题 partial。新运行以实际 Trace 为准。 |
| E HITL 跨重启 | 启用两项开关。问「2025年1月销售额」，暂停后记录 Run，重启同配置服务并刷新，在原 Run 选择「折扣后商品销售金额」。 | 同一 Run 返回 **231920.18**，暂停前 0 次工具、恢复后 1 次指标工具，原 History 连续可读。跨 Owner 和过期检查用[专项测试](HITL_MVP_V1.md)复验。 |
| F 经营变化 | 展示 1 月 **2312** 和 2 月 **2049** 的订单量跨期比较，再独立查看渠道排行或已审核 PDF。 | 窗口与口径一致时可核对差值 **-263**。保留各证据的来源。维度贡献对账和文档因果归因当前没有完整产品流程。 |

演示后按[文档清理步骤](RELEASE_DEMOS.md)仅撤下本次自己发布的合成文档。使用发布回执中的知识文档 ID。演示无法调用外部服务时可展示历史脱敏 Trace，并明确标示它所属的运行版本。
