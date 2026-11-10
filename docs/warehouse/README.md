# 电商数仓优先路线：设计 v1.0.0

状态：**A批4张DWD+5张DIM已整批构建并验收**。设计起点为 feature/ecommerce-dwd / 9d979c9，A批从设计提交d354041实施；既有明细表只读复用，其他8表新建。DWS/ADS与查询服务仍为设计，保留原8项指标，不改现有Agent。[实际验收与复现入口](BATCH_A_ACCEPTANCE.md)。

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

当前实施边界为A批，源ODS、原明细和Olist不作写入；不创建DWS/ADS，不调整Agent权限。PROJECT_PLAN.md与LEARNING_MAP.md仅Mac本地保留，停止跟踪不代表清除历史提交，不改写Git历史。

完整设计与A批实现作为完整里程碑交付，超过通常200行的原因是同时提供9模型、共享编排、逐行校验及隔离事务测试。最终48项测试通过（含8项隔离PostgreSQL集成测试），原8项ODS/DWD多窗口GT通过；22项扩展GT仍未实施。

开发单位是业务主题及完整验收批次：A批完整DWD+DIM，B批DWS+ADS，C批查询服务/Agent。每批内部按依赖执行，不为单表建PR；A批完成后使用一个完整功能PR，浏览器授权受限时提供compare链接，不自动Merge。

批准的八项口径和 SQL 保持原文件不变；新增目录只扩展能力和依赖元数据。原始 SQL Shed 是 CC0 合成练习数据，无币种、源时区、CDC、更新水位、真实客户身份归并、历史商品成本或退款完成流水，不能因建成多层数仓就宣称具有这些能力。
