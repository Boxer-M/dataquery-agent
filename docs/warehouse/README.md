# 电商数仓优先路线：设计 v1.0.0

状态：**设计完成，批量模型尚未实施或验收**。2026-11-10 核查起点为 feature/ecommerce-dwd / 9d979c9。仅现有 ODS 七表和 dwd.order_item_fact 已落地；本文其余对象均是拟建逻辑对象，不是数据库现状。保留既有表及八项指标原定义，不改现有 Agent。

正式设计入口：

1. [现有代码与数据库核查](CODE_AUDIT.md)
2. [整体架构与模型取舍](ARCHITECTURE.md)
3. [表清单与字段级设计](TABLE_DESIGN.md)
4. [指标目录与血缘](METRICS_AND_LINEAGE.md)，机器目录位于 [warehouse_catalog_v1.json](../../metrics/warehouse_catalog_v1.json)
5. [SQL 依赖与运行顺序](BUILD_AND_RELEASE.md)
6. [质量分级与主题验收](QUALITY_AND_ACCEPTANCE.md)
7. [Agent 查询服务契约](QUERY_SERVICE_CONTRACT.md)
8. [下一轮批量实施清单](IMPLEMENTATION_BATCHES.md)

本轮边界：文档、目录、代码审查、数据库只读核实；不运行构建入口、不创建表、不修改数据、不调整数据库权限。PROJECT_PLAN.md 与 LEARNING_MAP.md 是 Mac 本地文件，不作为公开设计入口；停止跟踪不代表清除历史提交。本次不改写 Git 历史。

完整设计需同时覆盖字段、38项指标、依赖、验收与接口，因此本轮作为一个设计里程碑超过通常200行；不将规格拆成互相缺失的单表交付。实际验证：36项既有测试通过；目录原8项逐字段/文件哈希一致，全部真实源字段、指标ID唯一性及设计文档链接检查通过。新增模型与22项扩展GT仍未实施。

开发单位由“单表”改为“业务主题及完整验收批次”。第一批一起交付完整 DWD + 必要 DIM；第二批交付 DWS + ADS 与经营主题验收；之后才接查询服务/Agent。每批内部按依赖执行，但不为每张表单独创建 PR。本轮不创建功能 PR，第一批验收后统一提交功能 PR。

批准的八项口径和 SQL 保持原文件不变；新增目录只扩展能力和依赖元数据。原始 SQL Shed 是 CC0 合成练习数据，无币种、源时区、CDC、更新水位、真实客户身份归并、历史商品成本或退款完成流水，不能因建成多层数仓就宣称具有这些能力。
