# Redis Performance Optimization v1

Redis 可选。FastAPI 在聊天与 Resume 入口使用认证后的 Owner 摘要限流。指标工具仅在现有 Capability 绑定通过后尝试缓存。命中时用专用 PostgreSQL 指标角色探测实际表及字段 ACL，检查角色、库名、Schema USAGE 和列 SELECT，并按原指标契约再次校验结果。带行级安全或非普通物理表的来源通过原 SQL 路径执行。

缓存键是 Owner 摘要、规范化请求、Catalog 版本与指标定义、SQL 模板、观察截止、Capability 元数据版本、来源字段、数据库端点及 `DATAQUERY_DATA_VERSION` 的 SHA-256。每条缓存 TTL 60 秒。数据发布版本由操作者随 DWS 数据变更更新。未设置数据版本时查询直接走 SQL。短 TTL 约束原地更新可能产生的旧值窗口。命中生成新的工具 Trace ID，保留原 Run、Task 和授权关联。

冷键由 Redis `SET NX EX` 选出填充者，其他请求最多轮询五次后返回工具繁忙。限流用 Lua 原子更新 Owner 固定窗口计数，超限返回 HTTP 429 和数字秒 `Retry-After`。进程内最多有 16 个并发聊天及恢复请求、8 个指标 SQL 或权限探测和 4 个模型调用。Redis 连接失败时回退 SQL，限流退化为进程内固定窗口。SQL 和模型并发保护按进程生效。Resume 继续使用原历史行预占保护。连接沿用现有每次请求建立的 PostgreSQL 连接。

~~~sh
.venv/bin/python -m pip install -r requirements-redis.txt
docker compose -f compose.redis.yaml up -d redis
DATAQUERY_REDIS_URL=redis://127.0.0.1:6381/0 DATAQUERY_DATA_VERSION=sqlshed-release-1 \
  .venv/bin/python scripts/local_workspace.py
~~~

重新发布仓库数据时，更新 `DATAQUERY_DATA_VERSION` 并重启应用实例。默认 Owner 限额是每 10 秒 60 个聊天及恢复请求。`DATAQUERY_RATE_LIMIT` 可覆盖请求数。可设置 `DATAQUERY_PERF_HEADERS=1` 输出本地压测计数。

~~~sh
PYTHONPATH=src .venv/bin/python scripts/benchmark_redis.py --out /tmp/redis-benchmark.json
REDIS_POSTGRES_TESTS=1 PYTHONPATH=src:tests \
  .venv/bin/python -m unittest -v test_redis_performance
DATAQUERY_REDIS_URL=redis://127.0.0.1:6381/0 DATAQUERY_DATA_VERSION=hitl-redis-test \
  DATAQUERY_RATE_LIMIT=0 HITL_POSTGRES_TESTS=1 PYTHONPATH=src:tests \
  .venv/bin/python -m unittest -v test_hitl
~~~

实验经本地 Uvicorn 的真实 HTTP 聊天入口。固定 24 条正式指标查询，覆盖 2025 年 12 个月的订单量和订单商品明细数。预热后，每种配置及并发阶梯运行三批；冷缓存每批用新数据版本，热缓存先填充相同请求集。每个完成结果都与 A 组同题校验。D 组在并发 20 时测试每个 Owner 30 秒最多 12 个请求。压测使用固定意图回调，外部模型请求及 Token 为零。指标 SQL 实际语句数与命中 ACL 探测分别计数，会话、历史与元数据查询仍存在。

参考 [Redis 原子计数文档](https://redis.io/docs/latest/commands/incr/) 和 [redis-py 文档](https://redis.io/docs/latest/develop/clients/redis-py/)。

## 实测结果

Apple M4（10 核、16 GB），macOS 15.7.7，Python 3.12.13，PostgreSQL 16.15 与 Redis 7.4.7 在本机 Docker 运行。各 A/B/C 模式每档 72 个请求。P95 包含全部 HTTP 响应，QPS 只计业务成功。D 组为 24 个请求。

| 模式 | 并发 | 完成 QPS | P50 ms | P95 ms | 指标 SQL | ACL 探测 | HTTP 429 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A 关闭 | 1 | 26.65 | 37.03 | 41.24 | 72 | 0 | 0 |
| B 冷 | 1 | 25.33 | 39.20 | 41.66 | 72 | 0 | 0 |
| C 热 | 1 | 27.36 | 36.23 | 38.87 | 0 | 72 | 0 |
| A 关闭 | 5 | 49.85 | 98.07 | 113.11 | 72 | 0 | 0 |
| B 冷 | 5 | 51.70 | 96.42 | 111.34 | 72 | 0 | 0 |
| C 热 | 5 | 51.28 | 96.52 | 113.50 | 0 | 72 | 0 |
| A 关闭 | 10 | 50.58 | 188.33 | 215.38 | 70 | 0 | 0 |
| B 冷 | 10 | 51.33 | 188.45 | 217.99 | 72 | 0 | 0 |
| C 热 | 10 | 51.17 | 190.96 | 212.05 | 0 | 71 | 0 |
| A 关闭 | 20 | 47.54 | 285.91 | 308.89 | 44 | 0 | 24 |
| B 冷 | 20 | 47.80 | 284.96 | 323.28 | 46 | 0 | 24 |
| C 热 | 20 | 49.38 | 286.05 | 320.14 | 0 | 47 | 24 |
| D Owner 限流 | 20 | 38.38 | 280.09 | 309.26 | 12 | 0 | 12 |

并发 5 的热缓存通过 72/72，命中率 100%，指标业务 SQL 从 72 次降至 0 次，每次命中保留一次轻量 ACL 查询。关闭和热缓存的端到端 P95 接近。并发 20 的 A/B/C 各有 24 个 429，均来自 HTTP 并发门槛。D 组 24 次中 12 次成功，另有 8 个并发拒绝和 4 个 Redis Owner 限流拒绝，均带 `Retry-After`。高并发下另有少量 HTTP 200 的业务失败，完整计数见报告。

简历可引用「实现按 Owner、指标目录及数据版本隔离的 Redis 缓存与原子限流。24 条查询、72 次请求的并发 5 热缓存实验中，指标 SQL 从 72 次降至零，每次命中执行当前权限探测，实测完成吞吐 51.28 QPS。」

已验证真实 Redis 和 PostgreSQL、跨 Owner 隔离、目录与数据版本切换、同键并发单次填充、Redis 连接拒绝时的正确 SQL 降级，以及历史工具 Trace。实际 PostgreSQL ACL 探测验证无权限来源。暖缓存撤权分支通过故障注入验证。Python 全量 229 项中通过 227 项、跳过 2 项可选测试，Redis 开启的 HITL 与缓存安全专项另行通过。前端 5 项测试、类型检查和生产构建通过。

[完整 A/B/C/D 数据](../../reports/benchmark/redis_performance_v1.json)
