# Olist 数据来源与 D1 审计

## 来源与许可

- 数据集：Brazilian E-Commerce Public Dataset by Olist
- 发布者：Olist（Kaggle owner ref：`olistbr`）
- 官方页面：<https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce>
- 官方元数据 API：<https://www.kaggle.com/api/v1/datasets/view/olistbr/brazilian-ecommerce>
- 许可证：CC BY-NC-SA 4.0
- 下载日期：2026-11-09（Asia/Shanghai）
- 官方 API 当前返回：`currentVersionNumber=2`、`lastUpdated=2021-10-01T19:08:27.97Z`、版本说明 `Data Update 2021/10/01`

官方 API 的 `versions` 数组同时含有更早、编号最高到 7 的记录，与当前版本号 2 的编号顺序不一致。本项目按原值记录，不推测或重写 Kaggle 的版本语义。官方元数据快照保存在被 Git 忽略的 `data/raw/kaggle_dataset_metadata.json`。

## 下载物校验

- 压缩包：`olist_brazilian_ecommerce_kaggle.zip`
- 压缩包大小：44,717,580 bytes
- 解压后 9 个 CSV 总大小：126,186,995 bytes，与 Kaggle API 的 `totalBytes` 一致
- 压缩包 SHA-256：`967e41e04fc306fe604e2a693f488995a8b41e5047418f8a5c8e4abd6deca784`
- 官方元数据快照大小：17,017 bytes
- 元数据 SHA-256：`38e1eabb7fccefdf12d2c9a67f775bdb350c46a924a08cde188d9c1543cb2acb`

`data/raw/` 只保存下载原件和原样解压文件，不做覆盖式清洗，也不提交 Git。逐文件 SHA-256 见 `reports/d1_data_audit.json`。

## 文件、字段与行数

| 文件 | 实际数据行数 | 字段 |
|---|---:|---|
| `olist_customers_dataset.csv` | 99,441 | `customer_id`, `customer_unique_id`, `customer_zip_code_prefix`, `customer_city`, `customer_state` |
| `olist_geolocation_dataset.csv` | 1,000,163 | `geolocation_zip_code_prefix`, `geolocation_lat`, `geolocation_lng`, `geolocation_city`, `geolocation_state` |
| `olist_order_items_dataset.csv` | 112,650 | `order_id`, `order_item_id`, `product_id`, `seller_id`, `shipping_limit_date`, `price`, `freight_value` |
| `olist_order_payments_dataset.csv` | 103,886 | `order_id`, `payment_sequential`, `payment_type`, `payment_installments`, `payment_value` |
| `olist_order_reviews_dataset.csv` | 99,224 | `review_id`, `order_id`, `review_score`, `review_comment_title`, `review_comment_message`, `review_creation_date`, `review_answer_timestamp` |
| `olist_orders_dataset.csv` | 99,441 | `order_id`, `customer_id`, `order_status`, `order_purchase_timestamp`, `order_approved_at`, `order_delivered_carrier_date`, `order_delivered_customer_date`, `order_estimated_delivery_date` |
| `olist_products_dataset.csv` | 32,951 | `product_id`, `product_category_name`, `product_name_lenght`, `product_description_lenght`, `product_photos_qty`, `product_weight_g`, `product_length_cm`, `product_height_cm`, `product_width_cm` |
| `olist_sellers_dataset.csv` | 3,095 | `seller_id`, `seller_zip_code_prefix`, `seller_city`, `seller_state` |
| `product_category_name_translation.csv` | 71 | `product_category_name`, `product_category_name_english` |

源字段中的 `lenght` 是数据集原始拼写，本阶段原样保留。

## 键与关系

以下 6 条关键关联在当前快照中均为 0 个孤儿键：

- `orders.customer_id → customers.customer_id`
- `order_items.order_id → orders.order_id`
- `order_items.product_id → products.product_id`
- `order_items.seller_id → sellers.seller_id`
- `payments.order_id → orders.order_id`
- `reviews.order_id → orders.order_id`

候选键审计的关键结论：

- `customer_id` 唯一；`customer_unique_id` 有 3,345 条重复行。96,096 个逻辑客户中，2,997 个关联多个 `customer_id`，单个逻辑客户最多关联 17 个客户记录。
- `order_id`、`product_id`、`seller_id`、`order_id + order_item_id`、`order_id + payment_sequential` 在当前快照中无重复或空键。
- `review_id` 有 814 条重复；`review_id + order_id` 无重复，可作为 D2 设计候选，但仍需根据查询和导入策略决定是否增加代理键。
- `geolocation_zip_code_prefix` 不唯一：1,000,163 行中只有 19,015 个不同前缀，单个前缀最多 1,146 行。

## 数据质量摘要

- 订单购买时间：2016-09-04 21:15:19 至 2018-10-17 17:30:18。
- `shipping_limit_date`：2016-09-19 00:15:34 至 2020-04-09 22:35:08；最大值明显晚于主要订单范围，本阶段只记录、不删除。
- 所有被审计日期字段解析失败数均为 0。
- `price`、`freight_value`、`payment_value` 均无空值、负值或解析异常；解析使用 Python `Decimal`。
- 订单状态：`delivered` 96,478、`shipped` 1,107、`canceled` 625、`unavailable` 609、`invoiced` 314、`processing` 301、`created` 5、`approved` 2。
- 订单日期空值：`order_approved_at` 160、`order_delivered_carrier_date` 1,783、`order_delivered_customer_date` 2,965。
- 评价文本允许为空：标题空 87,658，正文空 58,274；评价创建和回复时间无空值。
- 产品字段有缺失：类别及名称/描述长度/图片数各 610；重量和三个尺寸字段各 2。

## 一笔订单的子记录数量

| 子记录 | 无记录订单 | 1 条 | 多条 | 最大条数 | 平均条数 |
|---|---:|---:|---:|---:|---:|
| 商品明细 | 775 | 88,863 | 9,803 | 21 | 1.132833 |
| 支付记录 | 1 | 96,479 | 2,961 | 29 | 1.044700 |
| 评价记录 | 768 | 98,126 | 547 | 3 | 0.997818 |

这说明订单到商品、支付和评价都不能简单假设为严格一对一。

## 能力边界

当前 9 个 CSV 中没有：

- 真实退款金额字段；
- 退款完成时间字段；
- 独立支付事件时间字段。

`order_status` 中的 `canceled`、`order_approved_at` 和 `payment_value` 不能证明退款已经发生，也不能组成真实退款金额或退款完成时间。因此 D2 和后续指标层不得直接实现“成功退款金额”“退款完成时长”或按支付时间统计；如业务必须要求这些能力，需要更换数据源或显著标记并隔离 synthetic 扩展数据。

## 可复现证据

- 下载与运行命令：`data/README.md`
- 审计实现：`src/dataquery_agent/data_audit.py`
- 机器报告：`reports/d1_data_audit.json`
- 测试：`tests/test_data_audit.py`
