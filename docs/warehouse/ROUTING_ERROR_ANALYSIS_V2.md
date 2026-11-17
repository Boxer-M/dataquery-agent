# Intent 与 Tool Routing v2 失败归因

基于 Connectivity Rerun 的逐请求 JSON。实际意图从已记录的工具和状态推断；基线没有保留所有模型原文。路由和端到端失败按首个可证实的阻断点归因，同一上游失败不重复计为多个系统缺陷。

| 用例 | 原工具路由通过 | 首次阻断 |
| --- | --- | --- |
| `exp1:intent:discount` | 否 | Fast Router |
| `exp1:intent:account-count` | 否 | Fast Router |
| `exp1:planning:gross-discount` | 否 | Planner validation |
| `exp1:planning:items-products` | 否 | Planner validation |
| `exp1:planning:account-pair` | 否 | Planner validation |
| `exp1:planning:payment-pair` | 否 | Planner validation |
| `exp1:planning:cohort-drift` | N/A | Entity/clarification |
| `exp1:sql:gross` | 否 | Intent validation |
| `exp1:sql:returns-above` | 否 | Fast Router |
| `exp1:rag:65bb92063dc947e39bcc` | 是 | External provider |
| `exp1:pdf:pdf-old-ocr` | 否 | Fast Router |
| `exp1:pdf:pdf-original-bbox` | 否 | Fast Router |

- **`exp1:intent:discount`**。问题「请依据正式定义回答：指标手册里的 item_discount_amount 正式加总的是什么？」。上下文 单轮。预期 catalog_definition / ['item_discount_amount'] / catalog_exact。实际 文档知识 / [] / pgvector-cosine-exact-v1。Planner/模型 knowledge:received。Capability 未观测到执行绑定。首次偏离在 Fast Router。知识入口识别了「正式定义」，Catalog 分支使用另一套表达式，未调用实体解析便进入 RAG。 Trace `da271acfa56f4fe5b14eb214ba7782bc`。
- **`exp1:intent:account-count`**。问题「ordering_customer_count 的正式定义按账户还是按自然人去重？」。上下文 单轮。预期 catalog_definition / ['ordering_customer_count'] / catalog_exact。实际 文档知识 / [] / pgvector-cosine-exact-v1。Planner/模型 knowledge:received。Capability 未观测到执行绑定。首次偏离在 Fast Router。「正式定义」通过知识入口，实际实体解析未被调用。按账户/自然人的问法随后进入通用 RAG。 Trace `0a3a53fc69dd40b58b999d661d004d55`。
- **`exp1:planning:gross-discount`**。问题「从2025-04-01至2025-06-30按品类同时列折扣前商品记录金额、商品行折扣金额；分别保留两列。」。上下文 单轮。预期 business_plan / ['item_gross_sales_amount', 'item_discount_amount'] / metric_query。实际 经营规划 / ['item_gross_sales_amount', 'item_discount_amount'] / 无工具调度。Planner/模型 planning:received。Capability 未观测到执行绑定。首次偏离在 Planner validation。window_mismatch：提案与原文时间窗不一致，SQL 尚未调度。原始提案文本未保留，不能断言具体格式差异。 Trace `8e1ce2961da44ad993a602ef73b60c47`。
- **`exp1:planning:items-products`**。问题「2025年7月按品类同时列订单商品明细数和有下单明细的商品数，重复商品只在后一列去重。」。上下文 单轮。预期 business_plan / ['order_item_count', 'ordered_product_count'] / metric_query。实际 模型规划 / 明细拒绝 / ['order_item_count', 'ordered_product_count'] / 无工具调度。Planner/模型 planning:received。Capability 未观测到执行绑定。首次偏离在 Planner validation。detail_required：聚合任务被当作逐笔明细拦截；原句正式指标以外不含逐笔词，现有验证器也会拒绝模型提案的 detail 类型。没有 SQL 事件。 Trace `12557c73bb524d5099831ddaf51cda41`。
- **`exp1:planning:account-pair`**。问题「2025年5月按国家并列下单账户数和窗口内多次下单账户数，同一账户只计一次。」。上下文 单轮。预期 business_plan / ['ordering_customer_count', 'repeat_ordering_customer_count'] / metric_query。实际 经营规划 / ['ordering_customer_count', 'repeat_ordering_customer_count'] / 无工具调度。Planner/模型 planning:received。Capability 未观测到执行绑定。首次偏离在 Planner validation。unresolved_business_rules：提案仍带未解决口径，校验器先澄清；没有 SQL 事件。原提案内容未保留。 Trace `511112565256434589e56c40706c6021`。
- **`exp1:planning:payment-pair`**。问题「2025年2月按支付方式并列支付尝试次数和失败支付尝试次数，使用支付事件时间。」。上下文 单轮。预期 business_plan / ['payment_attempt_count', 'payment_failed_attempt_count'] / metric_query。实际 经营规划 / ['payment_attempt_count', 'payment_failed_attempt_count'] / 无工具调度。Planner/模型 planning:received。Capability 未观测到执行绑定。首次偏离在 Planner validation。unresolved_business_rules：提案仍带未解决口径，校验器先澄清；没有 SQL 事件。原提案内容未保留。 Trace `ac325215e0a34b72a6cf68518331951d`。
- **`exp1:planning:cohort-drift`**。问题「对比同一批商品明细在下单后7天和30天的退货率。」。上下文 单轮。预期 business_plan / ['item_return_rate'] / 只澄清。实际 歧义澄清 / [] / 无工具调度。Planner/模型 无模型。Capability 未观测到执行绑定。首次偏离在 Entity/clarification。泛化别名「退货率」有两个候选。通用歧义分支先返回，未保留原文已指定的商品明细粒度。 Trace `fac76a47272f4defb6435f86d2500208`。
- **`exp1:sql:gross`**。问题「给出从2025-04-01至2025-06-30按品类的折扣前商品记录金额，包括取消状态原始记录。」。上下文 单轮。预期 metric / ['item_gross_sales_amount'] / metric_query。实际 数值意图校验 / ['item_gross_sales_amount'] / 无工具调度。Planner/模型 intent:received。Capability 未观测到执行绑定。首次偏离在 Intent validation。missing_time：原句有时间范围，但执行时未形成有效原文时间字段。没有 SQL 事件；原提案内容未保留。 Trace `7ec5d66ec68348eba5d97fe2038ede70`。
- **`exp1:sql:returns-above`**。问题「以退货事件为时间口径，2025年9月退款记录超明细净额数按渠道怎么分布？」。上下文 单轮。预期 metric / ['return_above_item_net_count'] / metric_query。实际 单指标定义 / ['return_above_item_net_count'] / catalog_exact。Planner/模型 无模型。Capability 未观测到执行绑定。首次偏离在 Fast Router。时间口径触发定义路由，「怎么分布」未识别为数值动作；Catalog trace 确认没有 SQL 调度。 Trace `6bde2bcc4e8447b2b83976bb1de70b61`。
- **`exp1:rag:65bb92063dc947e39bcc`**。问题「请查阅技术文档《表清单与字段级设计》：字段级设计允许把支付事实按商品分摊吗？」。上下文 单轮。预期 knowledge / None / rag。实际 文档知识 / 无实体判分 / pgvector-cosine-exact-v1。Planner/模型 knowledge:error, knowledge:error。Capability 未观测到执行绑定。首次偏离在 External provider。正确进入 RAG，相关 Chunk 已检索到；回答生成两次 HTTP 503，首次阻断是外部服务。 Trace `ce88bb3bcbba42c2bf17269d3d87d943`。
- **`exp1:pdf:pdf-old-ocr`**。问题「昨天的扫描页数字更正后，请引用旧数字确认历史记录。」。上下文 单轮。预期 knowledge / None / rag。实际 数值意图校验 / 无实体判分 / 无工具调度。Planner/模型 intent:received。Capability 未观测到执行绑定。首次偏离在 Fast Router。隐式「扫描页」未进入 PDF 受限检索，交给指标提取，返回 unconfirmed_metric。 Trace `ff4b633f75904b51beb3f797423ddfca`。
- **`exp1:pdf:pdf-original-bbox`**。问题「扫描页某行已人工修订，能给我修订文字在原件上的准确坐标吗？」。上下文 单轮。预期 knowledge / None / rag。实际 数值意图校验 / 无实体判分 / 无工具调度。Planner/模型 intent:received。Capability 未观测到执行绑定。首次偏离在 Fast Router。隐式扫描页、原件坐标未进入 PDF 受限检索，指标提取返回 missing_metric。 Trace `275362110f224e4b9d3d3066e9d3aaa7`。
