# 跨会话偏好记忆 v1

基线 `origin/main=9ef5334`，包含 PR #68 / 日期边界修复 `1150c23`。功能分支 `codex/cross-session-preference-memory-v1`。

## 使用入口

复用现有聊天、`POST /api/chat` 和 CLI，不新建 HTTP 身份或前端入口。先在已登录的会话 A 中发送：

1. `保存默认看Web渠道`（也接受 `记住默认看Web渠道`）。
2. `查看默认渠道`。
3. 新建会话 B，发送 `2025年4月订单量是多少？`。
4. B 中发送 `只看mobile渠道`，然后 `那5月呢`；默认偏好仍为 Web。
5. A 中发送 `更新默认渠道为phone`，新会话立即使用 phone。
6. A 中发送 `删除默认渠道`，之后新会话不再默认加渠道。

渠道沿用既有 `CHANNELS` 别名和规范值 `web/mobile/partner/phone`。没有将 App 自动等同于 mobile；未登记渠道要求澄清。仅支持上述显式保存/更新/删除命令，不从查询、模型输出或隐含喜好自动学习。偏好表逻辑上仅有 `default_channel` 一个键。

已有 CLI 也可调用：

```sh
PYTHONPATH=src .venv/bin/python -m dataquery_agent.conversation_manager create
PYTHONPATH=src .venv/bin/python -m dataquery_agent.conversation_manager send CONVERSATION_ID '保存默认看Web渠道'
PYTHONPATH=src .venv/bin/python -m dataquery_agent.conversation_manager send NEW_CONVERSATION_ID '2025年4月订单量是多少？'
```

HTTP 继续使用原 `conversation_id/message_id/question` 契约、服务端 Owner、Bearer 和本地同源代理。不能通过请求提交另一个 Owner。新消息使用新 message_id；同条消息重试沿用原 ID。保存命令不清空当前会话的查询条件。

## 存储、迁移与调用链

- `sql/agent_meta/006_preferences.sql`：新增 `agent_meta.preferences`，Owner 哈希为主键；记录规范渠道、`explicit_chat` 来源、版本和更新时间。不包含密码或明文 Owner token。
- `scripts/provision_preferences.py`：事务内创建表并仅授予既有 `sqlshed_agent_meta` 管理角色该表的 CRUD；无业务表写权限、无角色属性变更。PUBLIC 无访问权限。重复执行不覆盖记录。
- `ConversationStore.preference(owner)`：按服务端 Owner 精确读取，不遍历或向量检索其他用户。
- `ConversationStore.finish`：偏好变更、会话状态、消息回执和 Query History 在同一个 PostgreSQL 事务提交。历史提交失败时偏好一并回滚；消息重放不再次更新版本。
- `preference_memory.py`：确定性命令与请求作用域。`ContextVar` 只传递本请求的已读取条件，退出即清除；持久化权威仍为 PostgreSQL，不是新建记忆缓存或任务状态机。
- `ConversationManager.dispatch → 原意图/Fast Path/Planner → UnifiedAgent.run_metric_request / run_business_plan → 原 Capability → 原受控 Metric Tool`。

部署已有本地环境时先运行（使用已有本地配置，不打印凭据）：

```sh
PYTHONPATH=src .venv/bin/python scripts/provision_preferences.py
```

干净 CI 的 `bootstrap.py` 在管理角色创建后执行同一迁移，`run.py` 纳入偏好专项。首次部署必须完成迁移；读取失败不会静默变成“不加偏好”的更宽查询。原会话/历史表及数据结构不迁移。

## 优先级与审计

优先级：本次已验证显式渠道 → 当前会话已确认渠道 → 已保存偏好。仅在构建已验证的结构化请求时补充，不改写用户原文或模型提案，不向 Prompt 注入偏好正文。

补充的请求先经过现有 `MetricQueryService.validate`，随后和原请求一样经过 Capability 及执行前校验。标准多指标计划逐子任务应用条件并同步计划 filters；不改变时间语义或指标公式。不支持渠道的指标不自动添加该偏好，Trace 记录原因。当前实际 26 个可用指标均支持渠道过滤，因此“不支持渠道”分支用受控模拟契约覆盖，未虚构真实不支持指标。

`trace.routing.channel_conditions` 逐指标记录 `channel/source/applied/preference_version/preference_read_ms`；source 为 `request/conversation/preference/none`。同样保存在 Query History。`preference_operation.saved` 保留保存后版本、时间及来源。

会话上下文增加可选 `channel_origin`：自动应用的偏好不升级成用户确认，后续请求会重新读取，所以更新和删除生效。用户亲自指定的渠道继续保留为会话条件。`所有渠道/取消渠道筛选` 可以显式抑制该会话默认渠道。旧上下文没有来源字段时保留原有已确认条件语义。

Catalog 定向恢复与 HITL 恢复继续沿用原冻结参数，本轮不以当前偏好重写恢复计划。偏好不用于任意 SQL、独立文档问答或正式 Catalog 定义。Owner 隔离复用现有受信服务身份和参数化查询；不是新增数据库多租户 RLS 体系。

## 实际验收

固定 Fast Path、真实 PostgreSQL、FastAPI ASGI；另用一次固定 Planner 提案验证两指标。真实 Gemini 调用 0，真实 Embedding 调用 0，偏好引入的额外模型调用 0。

| 场景 | 实际参数及结果 | 独立 ODS |
|---|---|---|
| 新会话 2025 年 4 月订单量 | web / preference，1286 | 1286 |
| 本次只看 mobile | mobile / request，708 | 708 |
| 同会话追问 5 月 | mobile / conversation，716 | 716 |
| 更新为 phone 后新会话 | phone / preference，92 | 92 |
| 删除后新会话 | 无渠道过滤，2251 | 2251 |
| Web 两指标 | 订单量 1286、支付尝试次数 1376 | 1286 / 1376 |

完整报告包含 11 次查询场景、12 个指标值的独立 SQL 对账。订单量用 `ods.orders.ordered_at` 与渠道直接计数；支付尝试用 `ods.payments.paid_at` 关联唯一订单渠道计数，不把支付尝试当成支付订单。

其他验证：存储对象重新初始化后跨会话读取成功；保存消息重放不增加版本；更新为版本 2；删除后新会话和偏好来源的追问不再应用；Owner B 不能通过 A 会话读取/改写/删除，且 B 自己的偏好操作不影响 A；无认证 401、客户端 owner_id 422；模拟历史写入冲突时偏好回滚。偏好命令本身新增指标 SQL 和模型调用均为 0。

- 偏好专项：4 passed（3 个离线方法、1 个真实 PostgreSQL 闭环方法，内部包含多项断言）。
- Python 全量离线：249 passed、87 skipped。
- 相关真实 PostgreSQL 回归：49 passed；含日期、Planner、DAG、专业角色、History、Owner、HITL、Catalog恢复，不与离线数量相加。
- 前端原测试 11 passed，TypeScript 和生产构建通过；既有 >500 kB chunk 提醒仍在。本轮未修改前端、未执行新 Chrome 验收。
- 11 次请求的偏好读取中位数 5.267 ms，范围 4.865–6.579 ms，包含本地连接、角色检查和精确查询；不是压测或性能收益声明。

```sh
CI_NO_EXTERNAL_MODELS=1 .venv/bin/python -m pytest tests/test_preference_memory.py -q
CI_NO_EXTERNAL_MODELS=1 CONVERSATION_POSTGRES_TESTS=1 PREFERENCE_REPORT=/tmp/preference-memory.json .venv/bin/python -m pytest tests/test_preference_memory.py -q
CI_NO_EXTERNAL_MODELS=1 .venv/bin/python -m pytest -q
CI_NO_EXTERNAL_MODELS=1 CONVERSATION_POSTGRES_TESTS=1 KNOWLEDGE_POSTGRES_TESTS=1 API_POSTGRES_TESTS=1 HITL_POSTGRES_TESTS=1 .venv/bin/python -m pytest tests/test_preference_memory.py tests/test_explicit_date_windows.py tests/test_business_query_planner.py tests/test_business_planner_v2.py tests/test_conversation.py tests/test_conversation_history_api.py tests/test_task_dag.py tests/test_specialists.py tests/test_catalog_recovery.py tests/test_capability_trace_linkage.py tests/test_api.py tests/test_hitl.py -q
npm --prefix frontend test
npm --prefix frontend run build
```

验收报告：`reports/warehouse/cross_session_preference_memory_v1.json`。测试只清理随机测试 Owner 的管理记录和偏好，不修改业务数据。真实运行需本地已有管理与只读指标配置，不需要 Gemini key。

## 边界与后续

仅一个渠道偏好，不保存事实、任务摘要、个人画像或跨用户向量记忆。删除偏好行后重新保存从版本 1 开始；此前审计留在原 Query History，遵循既有保留策略。偏好长期保存至显式删除，不随单会话清理而消失。本次完整闭环包含迁移、生产接入、事务与隔离测试和证据报告，总改动超过通常 200 行，未按组件拆成多个不可独立验收的提交。

下一步仅审查本分支并手动创建 PR，不自动推送或合并。
