# 会话状态与查询历史

`ConversationManager` 提供 `create_conversation(owner)`、`get_conversation(owner, conversation_id)`、`process_message(owner, conversation_id, message, message_id)` 和 `get_query(owner, query_id)`。现有 `UnifiedAgent` 仍可独立处理单轮请求。

## 一轮请求如何执行

1. 通过所有者凭据和会话 ID 获取状态，使用消息 UUID 认领本轮。
2. 用版本化规则 `conversation-rules-v1` 判断新主题、追问和澄清回复。只读取必要的结构化条件，新的自然语言任务使用现有 Unified Agent Prompt。
3. 将合法条件交给原有能力判断、指标服务、业务规划或受控 SQL 执行器。
4. 在同一个事务提交消息、会话状态和历史摘要，通过 Trace ID 关联实际工具执行。

单指标追问支持换月、设置或删除渠道、切换正式指标、按渠道或月份拆分、清除分组和对比上一个自然月。上一轮条件不兼容时返回澄清。对未识别的改写及复杂计划的继续编辑，本轮请提供完整的新问题。待澄清任务保存正式候选、原始时间条件和明确到期时间（15 分钟）。命中候选后重新校验并恢复执行，相对时间以发起澄清时解析的范围为准。

## 一致性与权限

- 短事务认领消息后释放数据库锁，Gemini 和业务查询在事务外运行。相同 UUID 和正文返回已有摘要及原查询 ID；UUID 相同而正文不同返回冲突。其他请求在 180 秒认领期内收到忙碌响应。过期请求不能用旧版本覆盖新状态。
- 历史写入失败时回滚本轮状态和消息完成标记。业务查询是只读操作，认领过期时旧请求可能已读到数据，其结果不能写回会话。
- `agent_meta` 使用独立管理角色 `sqlshed_agent_meta`。业务查询沿用已有只读角色，无法读取管理表。所有者令牌仅由可信调用方持有，数据库保存令牌散列；应用在每次读取或写入时校验会话归属。管理 DSN 只供服务端使用。
- 历史保存脱敏问题、结构化请求、来源、可取得的版本、结果摘要、错误和 Trace。明细与动态查询可追溯获准执行的 SQL 和安全参数；标准指标保存实际模型来源和标准 SQL 资源引用。完整行集保留在当轮响应中，重放返回有界摘要。未知数据构建版本返回 `null`。
- `prune` 由所有者显式触发，清理本所有者超过 30 天的已结束消息及关联历史，以及超过 30 天未更新的会话。存在有效认领时跳过清理。命令可以由部署方定期调用。常见凭据及联系方式会在入库前拦截，本地调用仍应避免输入敏感信息。

## 初始化与操作

首次部署可用现有本地管理员配置运行初始化。脚本创建管理 Schema 及独立角色，并将随机凭据和所有者令牌写入被 Git 忽略的 `.env.agent-meta`，权限为 `600`。已有配置时跳过初始化。发送消息沿用项目现有 Gemini 和业务只读连接。

```bash
PYTHONPATH=src .venv/bin/python scripts/provision_agent_meta.py
PYTHONPATH=src .venv/bin/python -m dataquery_agent.conversation_manager create
PYTHONPATH=src .venv/bin/python -m dataquery_agent.conversation_manager send "$CONVERSATION_ID" '2025年1月web渠道成功支付金额是多少？'
PYTHONPATH=src .venv/bin/python -m dataquery_agent.conversation_manager send "$CONVERSATION_ID" '那2月份呢？'
PYTHONPATH=src .venv/bin/python -m dataquery_agent.conversation_manager state "$CONVERSATION_ID"
PYTHONPATH=src .venv/bin/python -m dataquery_agent.conversation_manager history "$QUERY_ID"
```

从 `create` 的 JSON 取 `conversation_id`，从 `send` 的 JSON 取 `query_id`。澄清回复也通过 `send` 发给同一个会话。需重试同一消息时用 `--message-id` 提交原 UUID。

## 测试与实际数据流

[业务用例](../../tests/fixtures/conversation_v1.json)和[评测报告](../../reports/warehouse/conversation_v1.json)包含连续追问、澄清恢复、动态 SQL 历史追溯。报告保存每轮前后状态、模型输出、工具 Trace、历史和独立 ODS 对账，并保留首次真实 Gemini 运行的失败及修复后的结果。

```bash
PYTHONPATH=src .venv/bin/python tests/evaluate_conversation.py --mode fixed
PYTHONPATH=src .venv/bin/python tests/evaluate_conversation.py --mode live
CONVERSATION_POSTGRES_TESTS=1 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p test_conversation.py -v
```
