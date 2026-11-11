# Metric Query Service

服务接受结构化请求，按已验证的指标目录选择固定 DWS SQL，通过专用登录执行只读查询。响应包含统计时间、源粒度、分组、模型、定义版本和 Trace ID。金额、比率以及 PostgreSQL NUMERIC 的计数以十进制字符串返回。调用方可用 Decimal 读取并保留精度。

## 运行

已有 SQL Shed 数仓的本地开发环境执行一次：

```sh
PYTHONPATH=src .venv/bin/python scripts/provision_metric_reader.py
```

该命令用本地数据库管理凭据创建 `sqlshed_metric_service`，将随机密码写入被 Git 忽略的 `.env.metric-query`，文件权限为 0600。运行期仅使用该专用登录。数据库角色有六张 DWS 表的 SELECT 权限，及商品、客户维度中查询实际使用的列权限。部署时传入专用登录的 DSN，并沿用同样的权限边界。

```sh
PYTHONPATH=src .venv/bin/python -m dataquery_agent.metric_query_service \
  '{"metric_id":"item_net_sales_amount","start_date":"2025-01-01","end_date":"2025-02-01","group_by":["month","channel"]}'
```

Python 调用入口为 `MetricQueryService(dsn).query(request)`，接收与 CLI 同样的字典。CLI 将 JSON 响应写入标准输出，将 Trace 日志写入标准错误，业务失败退出码为 1。Python 集成可配置 `dataquery_agent.metric_query_service` 日志。日志包含指标、窗口、来源和 Trace ID，错误日志记录错误码。

## 时间与维度

日期格式为 YYYY-MM-DD，时间窗右开，最大 1827 天。省略 `group_by` 表示整个窗口，仅指定 `month` 表示按自然月计算。允许一个业务维度，可与 `month` 组合。各月按请求实际交集重新聚合，包括客户去重及比率分母。带业务维度的空窗返回空数组。无维度的空窗返回计数或金额 0，分母为 0 的比率返回 NULL。月序列的无维度空月遵循同一规则。

`channel` 过滤接受渠道编码，`category` 过滤接受正整数品类 ID。未知但格式合法的编码匹配空数据。份额指标必须显式选择各自的业务维度，渠道或品类过滤仍沿用原模板的份额分母口径。查询支持范围以 [DWS 注册表](../../warehouse/dws_metrics.json) 为准。

| 指标 | 可用业务维度 | 过滤 |
|---|---|---|
| order_count、cancelled_order_count、cancelled_order_rate、average_order_record_amount | channel | channel |
| item_net_sales_amount、order_item_count、ordered_unit_count、item_gross_sales_amount、item_discount_amount、ordered_product_count | channel、category、product | channel、category |
| payment_attempt_count、captured_payment_amount、payment_failed_attempt_count | channel、method、status | channel |
| payment_attempt_success_rate、payment_failed_attempt_rate | channel、method | channel |
| return_record_count、return_record_amount、return_before_order_count、return_above_item_net_count、item_return_rate | channel、category、product | channel、category |
| ordering_customer_count、repeat_ordering_customer_count、customer_repeat_order_rate、orders_per_ordering_customer | channel、country、loyalty、customer | channel |
| channel_order_share | channel（必选） | channel |
| category_sales_share | category（必选） | channel、category |

`item_return_rate` 必须传 `observation_cutoff: "2026-01-01"`，且请求结束日不晚于该截止。退货率按下单批次取数，退货金额按退货事件日取数。当前 26 项指标均有 DWS 路径。订单级支付异常等 ADS 应用字段需另行设计服务契约。4 项待验证指标返回 `metric_unverified`，8 项不支持指标返回 `unsupported`，目录若出现待定义指标则返回 `needs_definition`。

## 执行边界与验收

服务使用绑定参数、白名单标识符和固定 SQL 模板。读取发生在可重复读的只读事务中，每个请求的指标执行共用 5 秒预算。连接另设 5 秒超时。结果超过 1,000 行返回错误，不提供截断的结果。非法维度组合、截止、过滤和任意 SQL 字段均在请求校验时拒绝。数据库错误返回安全提示，凭据和原始数据库错误详情不进入响应。

```sh
METRIC_QUERY_POSTGRES_TESTS=1 WAREHOUSE_POSTGRES_TESTS=1 PYTHONPATH=src \
  .venv/bin/python -m unittest discover -s tests -v
```

80 项测试通过，包括原 69 项和新增 11 项。通过服务账户对 26 个指标逐项核对全量、2025 年 1 月的 web 渠道以及空窗，共 78 组独立 ODS 对照。覆盖自然月边界、重复客户重算、金额精度、分子分母、错误输入、真实查询超时、结果上限及写权限拒绝。服务用例与真值对照分别使用专用角色与测试侧只读参考查询。

给后续 Agent 的接入点是请求字典和结构化响应。Agent 负责把业务问题映射为指标 ID、统计窗口及合法维度，必要时向用户澄清时间口径；服务负责确定性执行和返回 Trace ID。
