# Golden Dataset Expansion v1

基线为合并 Benchmark Infrastructure v1 的 `e0b18e8`。本次沿用现有 Golden Schema，扩展数据发布版本为 `benchmark-golden-expansion-v1`。旧 101 条 Development 记录、来源和运行入口保持原样。

| 类别 | Validation | Holdout | 待审 | 合计 |
| --- | ---: | ---: | ---: | ---: |
| Intent / Entity | 7 | 7 | 1 | 15 |
| Business Planning | 5 | 4 | 6 | 15 |
| SQL、时间、维度 | 8 | 7 | 0 | 15 |
| 多轮与澄清 | 0 | 0 | 10 | 10 |
| RAG 与引用 | 8 | 7 | 0 | 15 |
| PDF / OCR | 0 | 0 | 6 | 6 |
| SQL + PDF | 0 | 0 | 4 | 4 |
| **合计** | **28** | **25** | **27** | **80** |

`reviewed` 表示已有独立证据的技术验收，`human_signoff` 仍为空，供后续业务审核。待审记录存放在 Schema 兼容的 Development 区，`needs_review` 记录的 Golden 字段全部为空，不进入任何正式评分分母。无合适证据的 PDF 或会话用例也不通过历史模型回答补标签。

## 真值和标签来源

- **正式目录**。比对当前 Metric Catalog 的 ID、定义、公式、版本和内容指纹。未登记 ID 的期望是无正式定义证据，不能用相似指标替代。已知但不可用的指标使用现有目录的限制原因。
- **SQL 与计划**。以人工标注的窗口、指标、分组和筛选构建参数；用确定性 Plan Validator 核对原文能生成相同请求。先用现有独立 ODS SQL 生成真值，再使用现有只读 Metric Query Service 对账。24 条用例，33 次查询。全部在前后相同的 ODS 内容快照上完成。结果保存完整桶、精确数值和比率分子分母。时间窗口右开，各事实使用目录对应的事件时间。
- **RAG**。逐条选定能定位到原文的片段。记录文档 SHA-256、解析器版本、段落索引、行号、标题路径、段落 Hash 和来源定位 ID。片段来自仓库正式技术文档，全量文档语料为离线评分夹具，运行检索评测前需在隔离环境显式配置。当前生产索引只包含此前授权的选段，不能把这 15 条标签当作已上线索引或真实召回结果。引用证据定位与答案语义支持分别评分。全文片段没有发送给外部模型。
- **旧 7 条**。三条受控明细和三条动态 SQL 均重新运行独立 ODS 参考 SQL，对照历史实测的行数及截断状态。查询资产的第七条复用该排行数值真值，生命周期要另行审核发布、过期、恢复和审计用例。本轮没有重放发布写操作。报告中的哈希覆盖完整返回行集，旧 101 条发布物不改写。

## 分组及封存

同一指标的正式定义和标量检验可放在同一个语义大类。业务操作有差别的组合分析独立编号。文档类按文档整体分配 Split，避免同文档答案片段进入 Validation 和 Holdout。业务模板和语义族按问题及证据要求人工判读，再检查 ID、规范化原文和去日期后的近似文本。与历史用例的比对包括近似改写，但字符相似度只能辅助发现漏标，新增样本仍须审核语义关系。

公开仓库中只保存 Holdout 的摘要、不可读的 ID 和分布。封存明文仅保存于本地被忽略的 `data/staging/golden-expansion/`。不要向 Prompt 调优人员传递封存问题或逐案答案。更换版本或加入相近用例时，封存保管人须重新做跨 Split 审查。清单比对可在无需解封的情况下发现登记 ID 冲突，不能仅凭清单重新判断文本语义。

## 人工审核清单

1. 用当前 Catalog 复核别名、排除条件、时间来源、指标定义状态。SQL 精确对账时确认底层快照、右开时间、分组及比率组成。计划标签核对工具顺序、允许的粒度和两指标事件时间。
2. 根据来源定位读取完整段落，确认它直接支持待回答命题。涉及多条事实时逐条核验，不能仅以关键词命中作为引用支持。将来源定位 ID 映射到本次隔离索引实际返回的 Chunk ID 再计算检索召回。
3. 多轮用例需要已保存会话的可验状态与允许的继承策略。PDF 用例需要审核文本、页码、当前版本、权限与索引证据。案例原因逐条记录在公开数据集的 `review.reason`。
4. 提升待审状态前重审语义族与模板，填独立复核人和证据。更新文档源、快照、条件或样本时发布新版本。

## 复现

在仓库根目录执行，使用项目 Python 3.12。

```bash
PYTHONPATH=src:tests .venv/bin/python -m benchmark.runner --migration
PYTHONPATH=src:tests .venv/bin/python -m benchmark.expansion
PYTHONPATH=src:tests .venv/bin/python -m unittest discover -s tests -p 'test_benchmark*.py'
```

封存保管人可执行只读对账并复现所有标签。需要现有 SQL Shed ODS 读者、Metric Query Service 的专用只读账号和与报告一致的数据快照。

```bash
PYTHONPATH=src:tests .venv/bin/python -m benchmark.expansion --holdout-dir data/staging/golden-expansion
PYTHONPATH=src:tests .venv/bin/python -m benchmark.expansion --reproduce --holdout-dir data/staging/golden-expansion
```

结构化报告列出可评分样本数和未执行的性能指标。没有模型或检索观察值时，分母为 0，结果为 N/A。本轮验证标签真值和当前只读查询能力，模型准确率、检索召回率及引用语义支持率留给独立评测。

## 本次验收

- 封存后的只读复现成功，公开和封存数据、ODS 证据及汇总报告均与初次生成一致。
- 11 项 Benchmark 专项测试通过。Python 全量运行 208 项，207 项通过，真实 Gemini 可选测试跳过 1 项。本机 PostgreSQL、pgvector 和 Apple Vision 集成测试已启用。
- 真实 Gemini 调用为 0。未运行浏览器联调，本轮变更限于评测数据、工具和文档。
