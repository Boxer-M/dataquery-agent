# 加工依赖、SQL规范与运行策略

A批、DWS与ADS路径已落地；4项明细指标的通用服务能力和新源版本发布仍为规划。DWS单独入口scripts/build_dws.py，模型清单warehouse/dws_models.json，见DWS_ACCEPTANCE。实际入口和报告见BATCH_A_ACCEPTANCE.md，不用空SQL/空表伪造完成。

## SQL目录规范

| 约定位置 | 作用 |
|---|---|
| `sql/dwd/order_item_fact.sql`（现有） | 原DDL+SELECT投影，首批保持结果兼容 |
| `sql/warehouse/ddl/{dim,dwd,dws}/<object>.sql`（DWD/DIM/DWS已实现） | 一个对象显式DDL，不混入源数据修复 |
| `sql/warehouse/transform/{dim,dwd,dws}/<object>.sql`（DWD/DIM/DWS已实现） | 单一SELECT模型，不执行TRUNCATE/隐式覆盖 |
| `sql/warehouse/ads/<view>.sql`（已实现，DDL在ddl/ads） | 普通视图SELECT与明确依赖 |
| `sql/warehouse/quality/{relation,batch_a,metrics,legacy_item}.sql`（已实现） | 命名SELECT检查，分别输出阻断错误和业务告警 |
| `sql/warehouse/ground_truth/dws/models.sql`与`sql/warehouse/dws_metrics/{ods,dws}.sql`（已实现） | 独立ODS参考计算，保留原8项引用已有SQL |
| `warehouse/models.json`（已实现） | 节点、版本、depends_on、源/目标、字段、约束与索引；实际SQL哈希保存在发布报告 |
| `scripts/build_warehouse.py`（已实现） | 唯一批量入口，计划/构建/校验/已有版本复用；不把大段业务SQL埋入Python |
| `tests/test_warehouse_{models,postgres}.py`、`tests/fixtures/sqlshed_batch_a.sql`（已实现） | 主题合成测试及隔离PostgreSQL集成测试，不访问/删除个人现有表 |
| `reports/warehouse/<release_id>.json`（A批已实现） | 已提交的脱敏验收摘要；原始快照、临时提取文件、失败详细源行留忽略目录 |

Python负责连接、事务、锁、绑定、拓扑执行、断言和报告。关键SQL全部纳管；常量阈值放源快照验收清单，不散落为每个表的硬编码。动态schema只能由服务器生成/受控映射，并用Identifier绑定，不能拼接用户输入。

## 节点依赖及构建顺序

| 阶段 | 节点 | 精确依赖 |
|---|---|---|
| 0 | 源检查与发布计划 | ODS七表、官方文件快照SHA、原8项基线；核对目标有无冲突 |
| 1 | dim.customer/category/channel/date | 各自ODS；date取全部源日期/事件范围；category做父链检查 |
| 2 | dim.product | ods.products + dim.category |
| 3 | dwd.order_fact | ods.orders + customer/channel/date完整性 |
| 4 | 复用既有 dwd.order_item_fact | ods.items+orders投影、order_fact一致性、dim.product/date；现有表只核验 |
| 5 | dwd.payment_attempt_fact | ods.payments + 唯一order_fact + date |
| 6 | dwd.return_record_fact | ods.returns + 唯一order_item_fact + order_fact/product/date |
| 7 | order_day/product_sales_day/customer_order_day | order_fact与order_item_fact分别聚合；product/current category关系校验 |
| 8 | payment_day/return_day | 各自事实及唯一订单→渠道，不连接其他多行事实 |
| 9 | item_return_cohort_day | 明细→订单，EXISTS退货，绑定同一as_of |
| 10 | 三ADS视图 | 见字段设计；支付异常视图从独立事实订单预聚合，其他视图从DWS |
| 11 | 验收与发布 | 逐层行数/字段、主题GT、原8项回归、幂等/失败回滚、权限与服务可用性清单 |

顺序不代表逐表停下来等待确认；一个批次完整跑完全部必需节点和验收，只有源冲突/覆盖风险/不满足完整性时停止。未满足依赖的节点不发布；同一批次失败不留下半套可查询模型。

## 首次全量构建

第一批沿用当前不可变源快照，取REPEATABLE READ事务；先取得批次级advisory try-lock，再固定快照。确认既有明细表完全匹配已批准投影，仅创建剩余3张DWD和5张DIM。建表、填充、约束与质量验收在同一事务，任一步失败回滚本批新增对象；原明细表不写入、不受回滚实验删除。现有DDL/投影可分文件但不改其14列/业务结果。

第二批只建6张DWS，绑定同源DWD/DIM，6表及指标校验同一事务；第三批另建ADS并单独PR。失败不影响已验收源层，不声称ADS或完整查询服务已上线。只预建明确as_of队列，不生成无限日期快照。

源业务表自身在本任务只读；若外部源更新发生，快照仍一致但不是最新，发布清单注明快照版本。发布前后对ODS与既有明细核验；一致性由数据库事务保证，不用一个先写成功文件来冒充提交成功。

## 幂等、回滚与版本发布

- 相同代码+源快照+指标版本+as_of视为同一构建键；目标已存在则校验结构与全部派生内容，一致时复用，不重复追加。
- 同名目标不兼容或内容不一致，直接停止并输出差异摘要；不自动DROP/TRUNCATE/UPDATE“修复”。
- 初始批次建新对象用事务保证完整性，advisory lock锁域覆盖整批，失败实验在隔离PostgreSQL实例/临时测试库执行，不删除真实既有表。
- 后续新源快照优先构建不可变的新物理版本，例如 `dwd_r_<server_generated_id>` / `dim_r_<id>` / `dws_r_<id>` / `ads_r_<id>`，逻辑名称由发布清单映射到物理schema；当前 `dwd.order_item_fact` 永远保留，不自动改名为新schema。
- 清单字段：release_id、parent_release_id、source_snapshot_id（7源文件SHA清单哈希及数据库内容验证指纹）、code_commit、model_version、metric_versions、as_of_values、schema/object映射、行数/金额/质量摘要、状态、构建UTC时间、数据库验证完成标识。含旧明细复用对象时逐对象记录版本，不强行假设所有对象同schema。
- 发布清单由成功提交后生成的不可变报告及唯一当前指针组成；指针原子写文件。崩溃在DB提交后、指针写前时旧发布仍可查，新版本视为未发布，恢复时只读重验再发清单；不拿最新目录名推断成功。失败报告留本地，不发成功指针。
- 查询服务每个请求固定一个release映射和只读事务；一旦选定不在请求中切换版本。不部署服务前不变更真实权限。旧版本回退只切回已验收清单，不删新旧物理表。版本清理/破坏性迁移另征确认。
- DWS/ADS使用的事实、维度和清单必须同源快照；拒绝把新商品分类配旧事实又声称历史分类准确。任何不兼容模型变更升模型主版本；改变指标定义用新指标ID/版本，不能静默重定义原8项。

## 增量策略：明确尚不支持的部分

现有Parquet是全量快照，缺updated_at、可靠CDC、删除标记和状态历史。第一版只实现“相同快照验证复用 / 新快照全量新版本”，不以max(ordered_at/paid_at/returned_at)冒充可靠更新水位。

未来接入新源版本须先归档其七文件清单，并在独立版本化ODS快照映射中接入，不覆盖当前七表；逻辑上仍是七种原始源表。该新源接入不在本轮或首批DWD/DIM的默认操作范围，必须先通过新的来源与完整性核验。

未来获得可靠增量来源后才引入：按源PK+变更序列去重、删除事件、迟到窗口、受影响日期重算及发布版本。订单状态更正会影响所有关联明细/客户/渠道汇总；退货迟到或更正会影响其退货事件日以及对应下单批次所有已发布as_of；品类更正影响按当前分类的商品汇总；更换观察截止需重算cohort。不存在一个简单“只重算昨天”就正确的通用策略。

A批已实现模型清单、共享执行、脱敏内容构建键报告和本地原子指针；有序依赖清单保证执行顺序，当前无需引入调度框架。测试沿用仓库平铺unittest发现方式，不为目录约定创建空测试包。新快照物理schema发布/增量仍未实现，遇到不同现有数据仍拒绝覆盖。

## C批ADS入口

`PYTHONPATH=src .venv/bin/python scripts/build_ads.py --plan`列出三个普通视图；去掉--plan执行首次创建或现有定义核验，--check-only仅只读验证。首次验收后用--grant-reader-select为学习角色授予三视图SELECT。入口复用既有事务、锁、内容指纹与DWS验证，不复制全套ETL框架。失败回滚全部新视图，不替换既有对象。详见ADS_CONTRACTS与ADS_ACCEPTANCE。
