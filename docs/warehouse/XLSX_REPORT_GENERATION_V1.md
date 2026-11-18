# 结构化指标结果生成 XLSX（C2.1）

基线为 C1 已合并的 main `aca75ed`。本轮只提供独立生成器，不增加 Artifact、下载、前端或意图路由。

## 输入与导出边界

入口：`src/dataquery_agent/xlsx_report.py` 的 `generate_metric_xlsx(result, metrics) -> bytes`。

阶段 B 的 Unified Agent 返回外层响应，其中 `response['result']` 是原 BusinessQueryPlanner 执行后的结构化结果：

```json
{"status":"ok","plan":{"analysis_type":"summary","metric_ids":["order_count","captured_payment_amount"],
 "time_windows":[{"start_date":"2025-01-01","end_date":"2025-02-01"}],
 "comparison_type":null,"group_by":[]},
 "results":[{"bucket":"all","month":null,"metrics":{
 "order_count":{"value":"2312"},"captured_payment_amount":{"value":"230864.57"}}}]}
```

这只是展示相关字段的示意，实际对象还有原工具Trace等字段。值来自原Metric Tool校验结果，经 `business_results.normalize_rows/combine` 保持Decimal字符串。`plan.metric_ids` 是指标顺序；结果没有内嵌正式中文名，因此导出边界另接收同一次查询所用的 `manager.agent.service.metrics` Catalog快照，通过ID取 `name_zh`。不从回答正文提取名称或数值，不修改上游结构或增加指标公式。

```python
from dataquery_agent.xlsx_report import generate_metric_xlsx
content = generate_metric_xlsx(answer['result'], manager.agent.service.metrics)
# content 是 XLSX bytes；生成器不保存报告，也不接收文件路径。
```

生成器只接受已授权调用方传入的完整、单窗口、无分组summary，且只有一条all汇总行。partial、跨期比较、多窗口、分组、截断和缺失指标明确拒绝，避免两列表格悄悄丢失时间/维度语义。该内部函数校验导出形状和精度，不是新的身份或数据库授权入口。

## 工作簿与精度

单表“经营指标”，仅“指标”“数值”两列，按 `plan.metric_ids` 写入。Excel数值直接使用输入Decimal，整数格式为 `0`，原字符串有两位小数则显示为 `0.00`；不会推测币种、重算业务指标或增加公式。标签强制写为文本，防止被当作公式。

首版对15位以上数字、超过15位小数、绝对值达到10^15、NULL、非有限值和float输入拒绝导出。写入后重新打开自身生成的工作簿，逐项核对数值单元格类型、Decimal值和数字格式，序列化出现精度差异则失败，不静默四舍五入。此范围是保守的导出限制，不修改数仓数值。

openpyxl只解析生成器自己生成的bytes，不提供读取任意上传工作簿的接口。不运行上游脚本、模型代码、宏、Office或外部链接。

## 依赖决策

C1只识别了上游依赖，没有批准或安装运行环境。本轮单独补充最小生成/回读依赖：

- openpyxl **3.1.5**，MIT，Python >=3.8；在项目Python 3.12实际安装与测试。
- et_xmlfile **2.0.0**，MIT，Python >=3.8，是其必需的XML写入依赖。

依据：https://pypi.org/project/openpyxl/3.1.5/ 、https://pypi.org/project/et-xmlfile/2.0.0/ 。两者加入现有requirements及容器依赖锁定文件；pip check通过。标准库手写OOXML的替代方案会增加格式兼容维护成本，故未采用；pandas、LibreOffice和上游重算脚本均未引入。Skill注册和工具权限不变，未修改工作流、部署步骤或数据库。

## 验收

离线固定输入使用7与12.30，独立于真实业务断言，验证工作表、表头、顺序、数值类型、显示精度、输入不变、公式标签、异常精度及不支持形状。

真实PostgreSQL复用阶段B原问题、固定模型提案和已授权指标链路，直接把实际返回结果交给生成器。回读订单量 **2312**、成功支付金额 **230864.57**，类型均为 `n`，金额格式 `0.00`，与独立ODS查询一致。预期常量只用于测试断言，生成器没有这些常量。导出前后指标SQL调用均为2次，导出新增查询0次，真实Gemini调用0次。

脱敏记录：`reports/warehouse/xlsx_report_readback_v1.json`。测试全部使用内存工作簿，不提交生成的XLSX文件。

```sh
CI_NO_EXTERNAL_MODELS=1 .venv/bin/python -m pytest tests/test_xlsx_report.py -q
CI_NO_EXTERNAL_MODELS=1 KNOWLEDGE_POSTGRES_TESTS=1 .venv/bin/python -m pytest tests/test_xlsx_report.py -q
CI_NO_EXTERNAL_MODELS=1 .venv/bin/python -m pytest -q
```

实测：离线生成器19通过、1项数据库测试跳过；本地真实PostgreSQL模式20通过；Python全量231通过、81项环境门控跳过。没有调用Gemini或重新执行前端、浏览器及部署验收。下一阶段的文件存储和下载没有实现。
