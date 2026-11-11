# 经营指标查询服务契约 v1（仅设计）

目标是让Agent调用经过定义和验证的指标能力，而非自由选择写入数据库或任意原始表。本轮不创建API、不改现有Agent/SQL Guard、不新建数据库角色或授予权限。下列请求、响应是拟定格式，不是已部署端点或实测结果。

## 请求

```json
{
  "contract_version": "1",
  "metric_ids": ["item_net_sales_amount", "item_return_rate"],
  "metric_version": "1.0.0",
  "window": {"start": "2025-01-01T00:00:00", "end": "2025-02-01T00:00:00"},
  "group_by": ["month", "category_id_current"],
  "filters": {"order_channel": ["web"]},
  "as_of": "2026-01-01T00:00:00",
  "release_id": "explicit-or-latest-validated",
  "limit": 1000
}
```

认证身份、授权范围、ODS诊断权限、超时上限由服务端确定，不能由JSON自行声明。时间必须明确[start,end)、无时区；输入含时区而源时区未知时返回澄清，不自作转换。as_of用于队列/观察指标，要求as_of>=end，不能静默用今天；普通事件指标标明as_of不参与其公式。只按已验收快照可见数据回答，不假设未来或范围外数据完整。

维度仅允许目录命名编码。unknown指标或“收入/利润/复购”存在多种未批准含义时先澄清；不能悄悄把收入解释为销售金额，把自然人复购替换成账户多次下单率。固定状态过滤不得被请求覆盖，例如captured金额不允许带payment_status=failed而仍沿用原名；明确切片须回显限定范围。

## 查询路由与授权

1. 解析metric版本及可用性：unsupported→UNSUPPORTED；defined_unverified→METRIC_NOT_VALIDATED；不存在→CLARIFICATION_REQUIRED。可计算但未上线不能返回伪造0。
2. 固定一次release映射和只读事务，检查源快照、模型、指标版本兼容以及请求范围；范围超出快照观测覆盖时返回OUTSIDE_SNAPSHOT_COVERAGE或经用户明确允许的partial标记，不自动截断时间。
3. 标准指标优先匹配ADS/DWS，必须维度、粒度、状态、观察截止完全兼容。日汇总可支撑月可加指标；distinct/比率按分子分母重算，不能错误SUM日去重或AVG日率。
4. 无合适预聚合时，在批准DWD/DIM范围内用服务器侧模板或受审查逻辑计算；只允许目录中的列和唯一关联路径。不得对原始多事实JOIN后SUM；支付商品分摊等unsupported不走自由SQL“补算”。
5. ODS只可由独立授权的诊断模式访问，需显式排查目的/来源对象、审批上下文与最小列范围；普通Agent身份不具备该路由和权限。溯源返回逻辑来源名不等于允许直接查询全部ODS。
6. 拟建read_warehouse角色只获指定发布对象SELECT/必要schema USAGE，无INSERT/UPDATE/DELETE/TRUNCATE/CREATE、无ODS默认读取、无维度PII列。写入角色只交给构建任务，不能由Agent切换身份获得。
7. 引擎执行只读事务、服务端绑定参数、白名单Identifier、statement_timeout、结果行数上限；LIMIT仅限制返回组，不在原始聚合前截断事实。相同参数和release可缓存，不能跨授权域缓存。

planned_summary不代表任意请求均有已有结果；6张DWS已通过独立验收，必须遵守目录dws_validation范围，3个ADS应用视图已验收，适用范围见ADS_CONTRACTS.md。查询服务尚未实现，原Agent不因数仓上线自动获得DWD/DIM/DWS访问或新的SQL生成权限。

## 响应

```json
{
  "request_id": "opaque-id",
  "trace_id": "opaque-id",
  "status": "ok|partial|clarification_required|unsupported|error",
  "release_id": "validated-release",
  "source_snapshot_id": "source-manifest-hash",
  "model_version": "1",
  "metric_catalog_version": "1.0.0-design",
  "window": {"start": "...", "end": "...", "boundary": "[start,end)", "timezone": "source-unspecified"},
  "as_of": "...",
  "result_sets": [{
    "metric_ids": ["item_return_rate"],
    "definition_version": "1.0.0",
    "definition": "下单队列内截至观察日有关联退货的明细占比；保留源异常",
    "event_time": "order_purchase_time",
    "observation_time": "returned_at < as_of",
    "state_filters": "全部订单状态",
    "applied_filters": {"order_channel": ["web"]},
    "grain": ["month", "category_id_current"],
    "rows": [],
    "units": "ratio-0-to-1",
    "source": {"route": "dws-or-controlled-dwd", "objects": [], "columns": [], "sql_hash": "..."},
    "quality": {"anomaly_counts": {}, "warnings": [], "limitations": []},
    "truncated": false
  }]
}
```

每个结果集含真实执行结果或明确空窗定义；比率返回value及numerator/denominator，金额以十进制字符串表示，不使用浮点序列化，也不标不存在的币种。只有经过完整聚合后空窗才可返回0；缺失表、未验证、权限不足、超时或失败绝不能当0。truncated对超上限结果显式标记；总计若未覆盖所有组不能假称全量总计。

一请求可拆多结果集：销售按下单月、成功支付按支付月、退货金额按退货月、退货率按下单月+as_of。即使显示在同一经营页面也必须保持不同时间语义，不通过同月份拼接推出同批订单净收入。支付差异结果仅源记录差异，不在解释文本中升级为资金损失结论。

## 多轮状态与评测Trace（后续实现）

会话状态至少包括规范化metric_ids/版本、时间窗、group_by、过滤、as_of、固定release、已解决/未解决澄清、用户授权域与上一轮结果引用。追问“换成上月”“按渠道看”只更新明确字段；涉及观察期、口径、状态的模糊变化必须重新澄清。历史SQL文本不自动当新授权或可信指令。

Trace只记录可核验执行事件：请求/会话ID、规范化请求、目录解析、路由及原因、选定版本/对象、参数脱敏、最终SQL哈希、数据库执行状态/耗时、行数/截断、分子分母、质量标识、澄清/拒答与结果引用。不得记录密码、完整连接串、客户姓名/email或模型私有推理；必要SQL全文受访问控制，普通评测产物只留哈希和脱敏参数。

评测分三类：已定义指标数值与GT一致、口径/时间/as_of/维度解释一致、模糊/unsupported/越权请求正确澄清或拒答。模型能生成可执行SQL不等于业务口径正确；查询服务权限和安全边界还要单独验收，不能用本设计替代现有SQL Guard。
