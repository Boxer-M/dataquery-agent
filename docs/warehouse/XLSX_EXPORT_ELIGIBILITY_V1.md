# XLSX 导出资格统一

基于已合并 C2.3 的 origin/main `4e10c90`，分支 `codex/xlsx-export-eligibility-v1`。
不扩展工作簿格式，不重跑业务查询，不调用模型；既有未跟踪审计文档不纳入提交。

## 接口与实现

QueryResponse 新增可选 `xlsx_export: {eligible, code, message}`。
`export_eligibility.py:xlsx_export` 读取已授权 History 的 resolved_request、状态及原始快照，
复用 XLSX 生成器完成相同格式、精度与回读校验；返回资格和可复用字节。
Artifact API 在 `manager.get_query(owner, query_id)` 后调用它，仅成功时创建 Artifact。
ConversationManager 使用同一函数计算的回执返回资格；History 投影重新计算，消息回放保留资格。
资格检查有少量内存工作簿生成开销，暂未新增缓存或第二套校验规则。

| 原因码 | 含义/前端行为 |
|---|---|
| xlsx_export_available | 可导出；保留进度、重复点击防护及已有 Artifact 下载重试 |
| query_export_grouped | 禁用；当前 Excel 导出仅支持汇总结果，暂不支持分组明细 |
| query_export_snapshot_missing | 禁用；此查询缺少导出快照，请重新运行查询后再导出 |
| query_export_unavailable | 未完成、格式或精度不支持；禁止误建议重跑 |

拒绝导出仍使用 HTTP409，Owner 越权仍返回404，未登录仍返回401。
分组先于快照检查：即使一行、甚至存在标量快照，也不能覆盖 group_by 的真实语义。
旧服务未提供资格时前端保守禁用，不按行数或回答正文推测。

## 同批样本前后对比

样本为支持汇总、月份、渠道、单行分组、旧记录缺快照共5类；前端纯逻辑及后端 ASGI 同时验证。
旧版 completed+query_id 一律显示按钮，四类不可导出样本均被误显示；分组失败409提示错误建议重跑。

| 指标 | 改前 | 改后 |
|---|---:|---:|
| 误显示可导出/全部样本 | 4/5 | 0/5 |
| 分组失败错误建议重跑/分组样本 | 3/3 | 0/3 |
| 有效汇总可导出 | 1/1 | 1/1 |
| 直接拒绝产生 Artifact | 0 | 0 |

月份实际数据流：真实 PostgreSQL 查询2025年1月订单量后追问“按月拆分” →
返回1行，History resolved_request.group_by=[month] → 共享判断返回 query_export_grouped →
API xlsx_export.eligible=false → React exportControl 禁用按钮并展示服务端提示。
直接POST导出仍409，不生成文件或重跑SQL。渠道查询同样得到分组拒绝。

## 验证与复跑

- Python离线全量：237 passed、82 skipped。
- 后端专项（API、Artifact、XLSX）真实PostgreSQL、固定提案：31 passed；增加分组断言后 API 4 passed。
- 前端完整测试：11 passed；TypeScript及生产构建通过，保留已有大于500kB chunk警告。
- 真实工作簿回读：订单量2312、成功支付金额230864.57，金额格式0.00。
- 资格/导出新增业务指标SQL为0，真实Gemini为0；允许且保留History权限检查与Artifact元数据读写。
- 浏览器交互本轮未执行；不将ASGI测试称为Chrome验收。

```sh
CI_NO_EXTERNAL_MODELS=1 .venv/bin/python -m pytest -q
CI_NO_EXTERNAL_MODELS=1 KNOWLEDGE_POSTGRES_TESTS=1 API_POSTGRES_TESTS=1 .venv/bin/python -m pytest tests/test_artifact_api.py tests/test_api.py tests/test_xlsx_report.py -q
npm --prefix frontend test
npm --prefix frontend run build
```

面试说明：把“是否可导出”作为服务端能力契约，而非界面猜测；同一判断保护响应和直接API，
生成器仍是格式与精度的权威边界。下一步最小任务是人工浏览器确认按钮及提示，不扩展分组报表。
