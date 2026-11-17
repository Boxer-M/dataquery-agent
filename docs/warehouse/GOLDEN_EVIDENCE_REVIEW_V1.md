# Golden Evidence Review v1

## 审核结果

以 Golden Dataset Expansion 提交 `444771a` 为父提交。审核版本为 `benchmark-golden-evidence-review-v1`。

| 口径 | 审核前 | 审核后 |
| --- | ---: | ---: |
| 已有独立证据、可进入正式评分 | 53 | 60 |
| 待审 | 27 | 20 |
| 封存 Holdout | 25 | 25 |

新增 7 条可评分负例，均在原有 Development 位置。其中 4 条验证 PDF 证据边界，3 条验证指标能力边界。审核者为 Codex 技术复核，人工业务签核仍为空。原 53 条有证据用例、历史 101 条和封存文件保持原样。逐案审核、来源授权、待补项和可评分标签见[独立证据报告](../../reports/benchmark/golden_evidence_review_v1.json)。

这次用一份版本化审核覆盖记录保存新结论。导出时先核对父数据和 Holdout 清单摘要，再按 case ID 更新待审记录，最后复用原 Split 校验。历史 53 条的标签、文本、Split 和模板保持一致。审核工具全程只读取封存清单，Holdout 明文未打开。原 Benchmark Runner 仍可读取既有发布物。本次导出可用于后续评测适配，尚未运行新用例的答案准确率评分。

## PDF 与 OCR

以仓库已有授权合成 PDF 为原件，在临时 Owner 范围内运行真实 pypdf、Apple Vision OCR 和 PostgreSQL pgvector。使用固定本地向量，Gemini 生成和 Embedding 调用均为 0。报告保留原件 SHA-256、实际 Document/Chunk ID、文档和审核版本、引用页码、审核行范围、摘录及内容 Hash。以下结果只适用于该合成夹具。

- 第 1 页由原生文本提取，已发布原文中可引用 `Order count: 24`。
- 第 2 页由 OCR 提取。为验证旧证据失效，测试先发布含人工植入错字 `4B` 的合成转录，再按原件修订为 `48`。修订后引用定位至真实第 2 页、审核文本第 1–2 行。原 OCR 置信度仅用于检查提取质量，审核文本行范围不能转换成修订字符的原图像素坐标。
- 第 3 页为空，当前已发布版本无该页 Chunk。
- 无签名审核的提取结果被拒绝。其他 Owner 无法检索或复核引用。旧版替换后不能作为当前证据，撤下后当前引用也失效。报告中的引用 ID 记录的是当次验收，复现时会产生新的临时版本和 Chunk。

新增的 4 条 PDF 负例分别覆盖置信度权威性、空页、过期版本和修订坐标。当前页码和原文仅支持已验证的文档命题。评分适配应按复现后的 Chunk 位置核对相应证据，并对伪造坐标、旧版或空页引文判错。

## SQL＋PDF

使用独立 ODS SQL 核算 4 个左闭右开窗口，并与只读 Metric Query Service 对账。报告保存查询版本 Hash、数据快照、参数和精确结果。

| SQL 子任务 | 窗口 | ODS 真值 | PDF 独立待证事实 |
| --- | --- | ---: | --- |
| 折扣前商品记录金额 | 2025 年 4 月 | 217934.96 | 折扣披露格式 |
| 下单账户数 | 2025 年 5 月 | 882 | 账户与自然人归属规则 |
| 窗口内账户复购率 | 2025 年 8 月 | 0.23421355，204 / 871 | 重复下单定义及窗口 |
| 有下单明细的商品数 | 2025 年 7 月 | 884 | 商品 ID 与 SKU 关系 |

授权样例的文本只支持样例自身，缺少这四项业务规则和与 SQL 的关联证据。四条联合用例继续待审。若 SQL 已成功而 PDF 规则缺失，应只陈述 SQL 数值并报告部分成功。若 PDF 范围未明确，应澄清文档或主题。若 SQL 失败，有权展示的 PDF 证据仍可独立陈述，不能据此推导查询指标数值。

## 剩余待审

| 原因 | 数量 |
| --- | ---: |
| 缺少会话状态或参数继承回执 | 10 |
| 缺少业务定义、窗口或粒度约定 | 4 |
| 缺少授权的 PDF 规则及与 SQL 的关联 | 4 |
| 缺少表格原件或同名跨 Owner 对照夹具 | 2 |

三条能力负例来自已核对的 ODS 对象、Catalog 和指标服务契约，涵盖无浏览事件、无历史成本及部门归属，以及未支持的逐明细相对观察窗口。报告代码是审核分类标签，用于比对语义行为，具体 Agent 错误码仍以运行契约为准。

## 复现

```bash
PYTHONPATH=src:tests .venv/bin/python -m benchmark.evidence_review
PYTHONPATH=src:tests .venv/bin/python -m benchmark.evidence_review --export /tmp/golden-reviewed-public.json
PYTHONPATH=src:tests .venv/bin/python -m benchmark.evidence_review --collect /tmp/golden-evidence-new-run.json
PYTHONPATH=src:tests .venv/bin/python -m unittest discover -s tests -p 'test_benchmark*.py'
```

`--collect` 需要现有本机知识库审核及管理配置、macOS Apple Vision、ODS 与指标服务只读账号。每次生成独立验收证据，结束时撤下临时发布。收集入口只使用仓库合成 PDF 和公开 SQL Shed 统计。签名与凭据留在本地配置。

本次 Python 全量运行 212 项，211 项通过，跳过 1 项真实 Gemini 可选测试。另运行 15 项 Benchmark 专项检查，全部通过。
