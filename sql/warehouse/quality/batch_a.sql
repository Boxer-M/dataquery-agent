-- check: category_cycles
WITH RECURSIVE walk AS (
 SELECT category_id AS root,parent_category_id AS parent,ARRAY[category_id] AS path,false AS cycle
 FROM ods.categories
 UNION ALL
 SELECT w.root,c.parent_category_id,w.path||c.category_id,c.category_id=ANY(w.path)
 FROM walk w JOIN ods.categories c ON c.category_id=w.parent WHERE NOT w.cycle
)
SELECT count(DISTINCT root) FROM walk WHERE cycle;

-- check: date_bounds
WITH dates AS (
 SELECT signup_date AS value FROM ods.customers UNION ALL
 SELECT discontinued_at FROM ods.products UNION ALL
 SELECT ordered_at::date FROM ods.orders UNION ALL
 SELECT shipped_at::date FROM ods.orders UNION ALL
 SELECT paid_at::date FROM ods.payments UNION ALL
 SELECT returned_at::date FROM ods.returns
)
SELECT min(value),max(value),max(value)-min(value)+1 FROM dates WHERE isfinite(value);

-- check: dimension_links
SELECT 'orders.customer' AS link,count(*) AS orphan_count
FROM dwd.order_fact o LEFT JOIN dim.customer c USING(customer_id) WHERE c.customer_id IS NULL
UNION ALL
SELECT 'orders.channel',count(*) FROM dwd.order_fact o LEFT JOIN dim.channel c ON c.channel_code=o.channel WHERE c.channel_code IS NULL
UNION ALL
SELECT 'items.order',count(*) FROM dwd.order_item_fact i LEFT JOIN dwd.order_fact o USING(order_id) WHERE o.order_id IS NULL
UNION ALL
SELECT 'items.product',count(*) FROM dwd.order_item_fact i LEFT JOIN dim.product p USING(product_id) WHERE p.product_id IS NULL
UNION ALL
SELECT 'products.category',count(*) FROM dim.product p LEFT JOIN dim.category c USING(category_id) WHERE c.category_id IS NULL
UNION ALL
SELECT 'payments.order',count(*) FROM dwd.payment_attempt_fact p LEFT JOIN dwd.order_fact o USING(order_id) WHERE o.order_id IS NULL
UNION ALL
SELECT 'returns.item',count(*) FROM dwd.return_record_fact r LEFT JOIN dwd.order_item_fact i USING(order_item_id) WHERE i.order_item_id IS NULL
UNION ALL
SELECT 'returns.derived_keys',count(*) FROM dwd.return_record_fact r JOIN dwd.order_item_fact i USING(order_item_id) WHERE r.order_id<>i.order_id OR r.product_id<>i.product_id;

-- check: date_links
WITH events AS (
 SELECT ordered_at::date value FROM dwd.order_fact UNION ALL
 SELECT shipped_at::date FROM dwd.order_fact WHERE shipped_at IS NOT NULL UNION ALL
 SELECT order_purchase_time::date FROM dwd.order_item_fact UNION ALL
 SELECT paid_at::date FROM dwd.payment_attempt_fact UNION ALL
 SELECT returned_at::date FROM dwd.return_record_fact UNION ALL
 SELECT signup_date FROM dim.customer UNION ALL
 SELECT discontinued_at FROM dim.product WHERE discontinued_at IS NOT NULL
)
SELECT count(*) FROM events e LEFT JOIN dim.date d ON d.date_value=e.value WHERE d.date_value IS NULL;

-- check: cancelled_items
SELECT count(*),sum(net_item_amount) FROM dwd.order_item_fact WHERE order_status='cancelled';

-- check: payment_statuses
SELECT status,count(*),sum(amount) FROM dwd.payment_attempt_fact GROUP BY status ORDER BY status;

-- check: source_return_flags
SELECT count(*) FILTER(WHERE r.returned_at<o.ordered_at),
       count(*) FILTER(WHERE r.refund_amount>i.quantity*i.unit_price-i.discount_amount),
       count(*) FILTER(WHERE r.refund_amount<0)
FROM ods.returns r JOIN ods.order_items i USING(order_item_id) JOIN ods.orders o USING(order_id);

-- check: target_return_flags
SELECT count(*) FILTER(WHERE dq_return_before_order),count(*) FILTER(WHERE dq_refund_above_item_net),
       count(*) FILTER(WHERE dq_negative_amount) FROM dwd.return_record_fact;
