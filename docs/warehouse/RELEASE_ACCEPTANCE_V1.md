# 本地发布准备验收 v1

验收环境是 macOS 本地的 Python 3.12.13、Node 25.8.1、Vite 8.3.8、Chrome 和 Docker PostgreSQL 16 + pgvector。模型响应标识为 `gemini-3.5-flash-lite`，索引使用现有 768 维 Gemini Embedding。准备脚本在现有 SQL Shed 快照成功完成 DWD、DIM、DWS 和 ADS 校验。一键启动、代理健康检查、Ctrl-C 清理与端口释放通过。全新空白机器从下载到角色初始化的完整部署流程尚未单独验收。

## 综合场景与演示

| 真实模型综合评测 | 既有基线 | 发布准备复跑 |
| --- | ---: | ---: |
| 场景通过 | 23/23 | 23/23 |
| 独立 ODS 数值检查 | 10/10 | 10/10 |
| Catalog / PDF 引用 | 4/4、3/3 | 4/4、3/3 |
| History 与实时 Trace 一致 | 23/23 | 23/23 |
| Gemini 生成尝试 | 14 | 15 |
| Gemini Token（输入 + 输出） | 7258 | 7300 |
| 查询 Embedding | 7 | 7 |
| P50 / P95 | 2.34 秒 / 4.93 秒 | 2.38 秒 / 10.02 秒 |

沿用 23 个用例、SQL Shed 快照、授权合成 PDF 和既有模型。基线来自 SQL 与 PDF 联合分析验收，本轮代码基于合入 Document UI 的 `be196dd`。一次 Gemini 知识生成收到 HTTP 503，现有运行时重试后通过；无最终失败用例。两次结果说明该样本的延迟会随外部服务变化，未作为吞吐或服务等级指标。固定模型测试、浏览器受控中断及模拟索引超时单独记录。

真实 Chrome 同次完成三组演示及回归。订单量 2312 和 2049 与查询结果一致，月度与渠道图表点逐行核对。已审核 PDF 第 2 页的人工修订引文通过真实检索返回。联合查询显示 2312 和独立 PDF 原文，包含指标、PDF、汇总三个任务。撤下该 PDF 后，重查保留数值并显示 `partial`，汇总节点因依赖不可用而跳过。测试文档已撤下。

[原综合基线](../../reports/warehouse/sql_document_hybrid_v1.json) · [本轮真实综合报告](../../reports/warehouse/project_release_e2e_v1.json) · [Chrome 演示与 Trace](../../reports/warehouse/project_release_browser_v1.json)

## 安全与恢复核查

| 场景 | 已验证行为与边界 |
| --- | --- |
| 上传回执丢失 | 浏览器演练只发出一次创建请求。已知 Document ID 时可以读取服务端状态。首次上传若没有拿到 ID，当前无法区分“未创建”和“已创建但回执丢失”，应人工核对；本轮保留该限制 |
| 刷新及跨 Owner | Chrome 按已知暂存 UUID 重开并读取最新版本。权限走原 GET API。全量测试覆盖他人读取、替换、审核和发布返回 404 |
| 导入、审核、发布版本 | 工作台分开显示当前导入版本、审核版本及已发布知识版本。替换后旧审核回执返回 409；旧知识版本在新版本成功发布前仍可参与检索 |
| 撤下和引用失效 | 使用发布回执中的知识文档 ID 执行现有撤下命令。新检索不能引用撤下或失效的版本。History 保存当次执行快照，工作台的发布回执也保存当次索引结果，可与当前检索状态不同 |
| 本地访问与文件限制 | 沿用两种环回 Host、同源 Origin、方法和路径白名单、限长请求与超时。签名绑定当前 Owner、原件 Hash、导入与审阅版本 |
| 交付资料 | 核查跟踪文件与本次修改、生产构建、脱敏报告和合成 PDF 截图。截图留在本机忽略目录。发布材料仅使用授权合成样本、聚合指标及测试 Trace |

## 支持范围

现有能力覆盖标准指标、澄清、追问、图表、受控 SQL、带版本与页码的 PDF 问答，以及单指标与 PDF 的顺序联合执行。未知上传 ID 的恢复、当前在线发布指针查询、完整文档管理和按 Document ID 指定检索范围仍是缺口。OCR 支持范围为 macOS。本轮验收范围为本地单人使用与公开合成快照。

## 全量回归

Python 196 项通过，1 项可选 Gemini 测试跳过。前端 5 项测试、TypeScript 检查、生产构建和 Chrome 通过。生产构建仍提示已按需加载的 ECharts 包超过 500 kB。跟踪文件、新增文件、构建和发布截图扫描未匹配本机已配置的令牌、密钥和数据库密码。Chrome 观察的浏览器请求未携带 Bearer。

```sh
npm --prefix frontend test
npm --prefix frontend run build
DOCUMENT_RAG_POSTGRES_TESTS=1 DOCUMENT_OCR_TESTS=1 API_POSTGRES_TESTS=1 \
KNOWLEDGE_POSTGRES_TESTS=1 QUERY_ASSET_POSTGRES_TESTS=1 CONVERSATION_POSTGRES_TESTS=1 \
DYNAMIC_POSTGRES_TESTS=1 DETAIL_POSTGRES_TESTS=1 CAPABILITY_POSTGRES_TESTS=1 \
METRIC_QUERY_POSTGRES_TESTS=1 WAREHOUSE_POSTGRES_TESTS=1 \
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```
