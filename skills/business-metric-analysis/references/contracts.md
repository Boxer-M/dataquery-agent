# 现有权威契约引用

本资源只说明职责边界，不复制指标定义。以下路径是供维护者核对的仓库引用；资源加载器不会自动读取这些文件。

- `metrics/warehouse_catalog_v1.json`：正式指标及状态。
- `src/dataquery_agent/business_plan.py`：计划参数和业务约束。
- `src/dataquery_agent/capability_resolver.py`：可执行数据能力及权限限制。
- `src/dataquery_agent/metric_tool.py`：结果与来源校验。

阶段 A 的 allowed_tools 只是注册清单中的范围声明，加载器没有工具调用能力，也不会向任何 Agent 授权。
