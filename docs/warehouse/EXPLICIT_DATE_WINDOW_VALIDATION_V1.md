# 显式日期窗口等价校验 v1

## 基线与复现

分支 `codex/explicit-date-window-validation-v1` 基于远端 main `fc269bd`（PR #66 合并，包含 `77f1cbf`）。生产改动不涉及 Catalog、SQL、权限、模型或 Prompt。

原始问题保持不变：

> 从2025-04-01至2025-06-30按品类同时列折扣前商品记录金额、商品行折扣金额；分别保留两列。

固定历史提案与修复前真实 Gemini 均返回 `time_windows=["2025-04-01至2025-06-30"]`。Validator 从原文提取 `["从2025-04-01至2025-06-30"]`，原先比较两者字符串集合，触发 `window_mismatch`，指标 SQL 0 次。真实修复前调用 1 次，965 Token，模型耗时 8222 ms。报告保留原始提案，未将固定提案当作模型输出。

## 最小实现与调用链

`api.chat → ConversationManager → UnifiedAgent.business → BusinessQueryPlanner.prepare → normalize_explicit_windows → 原 PlanValidator → 原指标执行器 → History`

- `src/dataquery_agent/intent_time.py`：仅对一个完整 ISO 日历日期区间证明等价；复用 `date_window`，显式日期的末日按现有规则包含，转换成右开窗口。日期相同且包含规则相同才把提案日期表达替换为原文表达，再交给未修改的 Validator。
- `src/dataquery_agent/business_query_planner.py`：在解析模型结构后、Validator 前执行证明。失败直接澄清，不进入指标 SQL。
- `src/dataquery_agent/unified_agent.py`：复用 `trace.routing.date_window_validation` 保存原文、原提案、双方边界、规范化状态及原因；历史接口 Trace 与当前响应一致。
- `scripts/ci/run.py`：将新增专项纳入既有无模型费用的 PostgreSQL 集成作业。

原文与模型提案不会被原地修改；不推断缺少的日期端点，不修复错误日期，不处理月份或相对日期的等价改写。包含/排除首末日、开闭区间等显式修饰不能按默认规则吞掉，返回 `unsupported_date_boundary`。没有通用自然语言边界推理能力。

## 实际验收

| 路径 | 实际结果 |
|---|---|
| 固定省略“从”提案 + 真实 PostgreSQL | completed；24 品类 × 2 指标，48/48 金额精确一致 |
| 修复后真实 Gemini 原问题 | completed；仍省略“从”，规范化成功；2 次指标 SQL，24 品类两列保持独立 |
| 修复后真实 Gemini SQL + PDF | completed；订单量 2312 与 ODS 一致；PDF 第 2 页原文 `Order count: 48` 校验通过；两者分别陈述 |
| 起始日偏一天、末日偏一天、缺起点、缺终点 | 各被拦截；每项新增指标 SQL 0 次 |
| 模型附排除末日、半开区间，用户显式排除末日 | 各被拦截；每项新增指标 SQL 0 次 |
| 离线附加首日排除、末日排除、非法品类/指标/额外窗口 | 被拦截；不进入工具执行 |

原始样本的数据流：原文 `从2025-04-01至2025-06-30` → 模型 `2025-04-01至2025-06-30` → 双方均解析为 `[2025-04-01,2025-07-01)` → 品类分组和两个 Metric ID 继续经原 Validator → 两次受控指标查询 → 独立 ODS 联表逐品类核对 `sum(quantity*unit_price)` 与 `sum(discount_amount)`。例如品类 1 两列分别为 `48698.90`、`486.30`。

真实模型使用现有 `models/gemini-3.5-flash-lite`，SDK 返回 `gemini-3.5-flash-lite`；Prompt 标识 `business-plan-v1`、`knowledge-extractive-v1` 均未改。修复后两个 Planner 样本 2/2 完成，生成调用共 3 次（Planner 2、PDF 摘录 1），输入 1955、输出 379、合计 2334 Token，无失败和重试。原问题端到端 2365 ms，联合问题 8013 ms；样本过少，不报告性能提升或泛化准确率。

Embedding `gemini-embedding-001`，沿用 768 维：文档批次 1 次（2 个输入，1892 ms），查询批次 1 次（1 个输入，2273 ms）；Token API 未返回，记 N/A。仅发送既有授权合成 PDF 的必要片段，验收后撤下测试版本。PDF 的 48 是合成证据，不是实际订单量。

数据核对使用只读 ODS；现场计数 orders=90000、order_items=215000，完整逐品类预期值保存在报告中。计数与核对值不是不可变数据库备份。未读取 Holdout。

## 测试与复跑

- Python 离线全量：245 passed，86 skipped（外部服务/平台/显式数据库开关）。
- 真实 PostgreSQL 日期专项：5 passed；覆盖正例、反例、两列对账与 History 一致性。
- 真实 PostgreSQL 原路径专项：36 passed（含日期专项、Business Planner、Conversation、DAG、specialists、Catalog Recovery、Capability Trace、API、HITL）。不与离线数量相加当作独立用例总数。
- 原有混合固定提案：9 场景，40/40 适用检查，路由 9/9；真实模型调用 0。
- 前端：11/11 测试；TypeScript 与生产构建通过，仍有既存 chunk >500 kB 提醒。未执行新的 Chrome 验收，本轮未改前端。

```sh
CI_NO_EXTERNAL_MODELS=1 .venv/bin/python -m pytest -q
CONVERSATION_POSTGRES_TESTS=1 CI_NO_EXTERNAL_MODELS=1 .venv/bin/python -m pytest tests/test_explicit_date_windows.py -q
PYTHONPATH=src:tests .venv/bin/python tests/evaluate_date_windows.py --mode fixed --phase after --out /tmp/date-fixed.json
# 明确调用真实模型及授权测试 PDF 索引：
PYTHONPATH=src:tests .venv/bin/python tests/evaluate_date_windows.py --mode live --phase after --out /tmp/date-live.json
PYTHONPATH=src:tests CI_NO_EXTERNAL_MODELS=1 .venv/bin/python tests/evaluate_hybrid.py --mode fixed --out /tmp/hybrid-fixed.json
cd frontend && npm test && npm run build
```

固定模式无 Gemini 调用，但评测器和数据库专项需要项目已有 PostgreSQL/测试角色；真实模式还需要既有模型配置、PDF 样例及 RAG 索引环境。API验收通过 ASGI 真实入口执行，不代表已重启后台常驻服务。

## 证据与限制

- `tests/date_window_cases_v1.json`：冻结原问题、历史提案与反例；不是新造 Golden。
- `tests/test_explicit_date_windows.py`：离线和 PostgreSQL 验收。
- `tests/evaluate_date_windows.py`：固定/真实执行分开，原始提案、模型用量、工具与 History 记录。
- `reports/warehouse/date_window_before_fixed_v1.json`、`date_window_before_live_v1.json`：修复前证据。
- `reports/warehouse/date_window_after_fixed_pg_v1.json`、`date_window_after_live_v1.json`：正反例与真实链路证据。
- `reports/warehouse/date_window_validation_summary_v1.json`：源文件 Hash、版本基线与回归摘要。

两条真实模型样本只证明本次链路，不证明所有自然语言日期表达可靠。生产补丁约 60 行；为保存固定复现、独立 SQL、真实 Trace 和可复跑报告，本次完整批次总提交超过通常 200 行约束。未增加依赖，未修改 ODS/DWD/DWS 或公开指标定义。下一步仅需审查本分支并手动创建 PR。
