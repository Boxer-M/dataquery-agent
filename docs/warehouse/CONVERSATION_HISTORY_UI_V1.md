# 已保存会话与消息恢复

分支 `codex/conversation-history-v1`，基于本地导出资格修复 `8c69ddd`，不是从旧 main 开发。
本次不改 Agent、模型 Prompt、指标口径、业务 SQL 或数据库表结构。

## 根因与数据边界

旧前端新建会话时清空 messages，且没有会话列表接口；后端并未因此删除原会话。
复用 agent_meta.conversations/query_history，沿用既有30天清理策略，而非新增长期记忆系统。
旧记录只按保存的摘要、来源和 Trace 展示，不补造完整回答或重新查询。
新记录在同一 History JSON 中保存经过 Public API 投影的 display_snapshot：回答、最多50行、
截断标志、定义和澄清候选。不重复保存 Trace、不保存模型 Prompt、Token、密码或原始文档。
History 仍重新校验 Owner；导出仍读取原 export_snapshot 并执行共享资格判断。
恢复的分组结果保留表格；本次未新增历史 ECharts 契约恢复。

## API 与前端

- GET `/api/conversations?offset=0`：当前服务端 Owner 的会话，更新时间倒序，每页20条。
- GET `/api/conversations/{id}/messages?offset=0`：已保存查询的消息标识、问题与时间，时间正序，每页20条。
- 每项结果继续通过原 GET `/api/queries/{query_id}` 读取；外来和不存在的会话/查询统一404。
- 分页 has_more；offset 限0–9999。前端代理只放行明确的只读路径、参数与方法。
- 左侧列表支持切换、刷新和加载更多；消息支持继续加载，不声称支持全文搜索。
- sessionStorage 仅保存当前 conversation_id；聊天内容不写入浏览器持久化存储。
- 切换始终重新读取授权记录；保留本标签页未确认的失败消息，消息重试继续使用原 message_id。
- 保留 HITL 待处理 Run 的恢复；新建会话不删除旧会话。Owner变化后的旧ID不能越权恢复。
- 长列表独立滚动，不撑高主界面；现有窄屏布局仍不展示完整侧栏列表。

## 验收

真实 PostgreSQL、固定提案：会话 A 查询订单量2312，会话 B 查询订单量2312与支付金额230864.57。
真实 Google Chrome 完成 A→B→A→刷新→B，恢复的 rows 与原响应逐项一致。
浏览阶段只观察到 GET；业务指标 SQL 总计3→3，固定提案2→2，真实Gemini0。
后端验证存储组件重建后可读取、其他Owner列表为空且消息/查询404、无额外业务查询与模型调用。

- Python离线全量：238 passed、83 skipped。
- 后端真实PostgreSQL专项：26 passed。
- 前端完整测试：11 passed；TypeScript及生产构建通过，保留既有大chunk警告。
- 浏览器截图和脱敏请求记录位于本地忽略目录 `artifacts/conversation-history/`。
- 新旧快照兼容、消息幂等、Artifact API与Owner隔离纳入回归；无新增依赖。

```sh
CI_NO_EXTERNAL_MODELS=1 .venv/bin/python -m pytest -q
CI_NO_EXTERNAL_MODELS=1 CONVERSATION_POSTGRES_TESTS=1 API_POSTGRES_TESTS=1 KNOWLEDGE_POSTGRES_TESTS=1 .venv/bin/python -m pytest tests/test_conversation_history_api.py tests/test_conversation.py tests/test_artifact_api.py tests/test_api.py -q
npm --prefix frontend test
npm --prefix frontend run build
```

核心解释：会话状态用于继续问数，History用于只读展示；二者共享现有标识和Owner边界，
查看历史不等于重新执行。下一步最小任务是人工确认自己旧会话的摘要是否满足日常使用。
