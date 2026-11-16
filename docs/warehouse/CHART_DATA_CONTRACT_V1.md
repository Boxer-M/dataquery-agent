# Chart Data Contract v1

`POST /api/chat` 可在现有结果旁返回 `visualization`。API 保留原有 `rows` 的形状、顺序和十进制字符串，以及 `row_count` 和 `rows_truncated` 的语义。接口文档仍由本机 `GET /openapi.json` 提供。

| 字段 | 含义 |
| --- | --- |
| `version` | 契约版本 `"1"` |
| `metric` | 正式指标 ID、目录名称、单位、单位类型和本次已校验的来源 |
| `dimension` | 行内字段、维度类型、业务语义及排序规则 |
| `window` | 本次已验证查询的起止日期，`end_exclusive: true` |
| `value_path` | 从单行读取数值的键路径，如 `["value"]` 或 `["metrics", "order_count", "value"]` |
| `row_order` | 从零开始的原始行索引，依次读取即可得到已核对的维度顺序 |
| `visualization_unavailable_reason` | 未生成 `visualization` 时的回退原因，供客户端保留现有展示 |

## 元数据来源与适用范围

- 首版只登记订单量，单位为“笔”，类型为 `count`。正式口径是时间窗内订单记录数，独立 ODS SQL 按 `order_id` 粒度计数，服务从 `dws.order_day` 汇总。单位登记放在现有指标目录顶层，保持已索引指标定义对象的内容和版本。
- 分组、时间窗口及来源取自成功的受控指标调用。查询计划须只包含一次成功指标调用，且无跨期比较、条件筛选、显式排行或计划行数限制。只有已验证的 `group_by: ["channel"]` 才能将 `bucket` 标为渠道。
- 月度顺序由指标服务共用的日历分窗生成，并核对行与月份及窗口的对应关系。渠道按已校验的渠道代码排序。两者都通过独立的索引数组引用原始行。
- 只有完成、完整且至少两行的订单量结果可用。其他指标等待正式单位登记。历史和幂等回执可只保存摘要，返回 `summary_only`。截断、缺少单位、维度不支持和跨指标场景分别返回相应回退原因。合法数值保持字符串，未来绘图客户端须单独判断 JavaScript 数值转换能否满足精度要求。

## 真实 PostgreSQL 对账

以真实 FastAPI 会话采集下列两份完整响应。对账通过独立的只读 ODS 查询，使用下单时间、右开窗口及原订单记录粒度。

| 查询 | 行数 | 对账结果 | 实际响应 |
| --- | ---: | --- | --- |
| 2025 年订单量，随后追问“按月拆分” | 12 | 12/12 同月数值一致，1 月 2,312，12 月 4,128 | [月度 JSON](../../reports/warehouse/chart_contract_month_response.json) |
| 2025 年 1 月按渠道统计订单量 | 4 | mobile 685、partner 207、phone 83、web 1,337，4/4 一致 | [渠道 JSON](../../reports/warehouse/chart_contract_channel_response.json) |

核验两份响应的指标、来源、分组、窗口和排序索引。FastAPI 专项覆盖乱序月份、超安全整数的原值、计划结果路径、缺少单位、异常来源、截断、缺失值与空结果。原有多指标联合查询、正式引用、澄清、Trace 和 Owner 隔离测试通过。Python 全量回归 186 项通过，1 项可选 Gemini 测试跳过。前端 TypeScript 检查通过。
