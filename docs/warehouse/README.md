# 电商数仓优先路线：设计 v1.0.0

SQL Shed 电商数仓已完成 ODS 接入、DWD/DIM 建模、DWS 汇总和 ADS 视图验收。标准指标服务、自然语言解析、经营查询规划及元数据能力判断已有独立入口和测试报告。

正式设计入口：

1. [现有代码与数据库核查](CODE_AUDIT.md)
2. [整体架构与模型取舍](ARCHITECTURE.md)
3. [表清单与字段级设计](TABLE_DESIGN.md)
4. [指标目录与血缘](METRICS_AND_LINEAGE.md)，机器目录位于 [warehouse_catalog_v1.json](../../metrics/warehouse_catalog_v1.json)
5. [SQL 依赖与运行顺序](BUILD_AND_RELEASE.md)
6. [质量分级与主题验收](QUALITY_AND_ACCEPTANCE.md)
7. [Agent 查询服务契约](QUERY_SERVICE_CONTRACT.md)
8. [下一轮批量实施清单](IMPLEMENTATION_BATCHES.md)
9. [A批实体建模及验收](BATCH_A_ACCEPTANCE.md)
10. [DWS粒度与能力契约](DWS_CONTRACTS.md)
11. [DWS实体及指标验收](DWS_ACCEPTANCE.md)
12. [ADS应用契约](ADS_CONTRACTS.md)
13. [ADS实体及验收](ADS_ACCEPTANCE.md)

## 查询能力与测试

- [标准指标查询服务](METRIC_QUERY_SERVICE.md)
- [自然语言指标解析](INTENT_SEMANTIC_RESOLVER.md)
- [多指标经营查询规划](BUSINESS_QUERY_PLANNING.md)
- [元数据与能力判断](METADATA_CAPABILITY.md)
- [统一 Agent 查询入口](UNIFIED_AGENT.md)
- [受控明细执行与对账](CONTROLLED_DETAIL_EXECUTION.md)
- [语义驱动动态 SQL](DYNAMIC_SQL_ENGINE.md)
- [统一 Agent 端到端报告](../../reports/warehouse/unified_agent_v1.json)

指标目录包含 26 项已验证指标、4 项待验证指标和 8 项不支持指标。测试覆盖数据库结构、字段权限、关联粒度与指标对账。PostgreSQL 只读账号用于查询与对账测试，明细路由结论用于规划。

SQL Shed 是采用 CC0 许可的公开合成测试数据。数据契约的已知边界包括币种、源时区、CDC、更新水位、跨账户客户身份、历史商品成本和退款到账记录的缺失。
