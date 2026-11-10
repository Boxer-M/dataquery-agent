# 电商数仓优先路线：设计 v1.0.0

状态：**A批DWD/DIM与B批6张DWS均已整批构建并验收**。B批从合并后的main 90fbdba创建feature/ecommerce-dws；原8项定义不变，新增18项限定DWS范围available。C批3个ADS普通视图已完成；查询服务与Agent集成尚未实施。[A批证据](BATCH_A_ACCEPTANCE.md)、[DWS证据](DWS_ACCEPTANCE.md)。

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

当前完成C批ADS；ODS/DWD/DIM/DWS与Olist不作写入，不调整Agent权限。学习角色仅在验收后新增3个ADS视图的SELECT。PROJECT_PLAN.md与LEARNING_MAP.md仅Mac本地保留，停止跟踪不代表清除历史提交，不改写Git历史。

按完整批次交付，超过通常200行是为同时提供模型、共享执行、独立真值与隔离事务测试。当前69项测试通过；原8项ODS/DWD/DWS指标不变，新增18项通过，4项保留明细查询待验证，8项unsupported不变。

开发单位是完整业务批次：A批DWD+DIM，B批仅DWS，C批单独ADS，之后查询服务/Agent；DWS与ADS分开PR。每批内部按依赖执行，不为单表建PR；A批完成后使用一个完整功能PR，浏览器授权受限时提供compare链接，不自动Merge。

批准的八项口径和 SQL 保持原文件不变；新增目录只扩展能力和依赖元数据。原始 SQL Shed 是 CC0 合成练习数据，无币种、源时区、CDC、更新水位、真实客户身份归并、历史商品成本或退款完成流水，不能因建成多层数仓就宣称具有这些能力。
