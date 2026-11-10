-- check: columns
SELECT table_name, jsonb_object_agg(column_name, jsonb_build_object(
 'type',data_type,'nullable',is_nullable,'precision',numeric_precision,'scale',numeric_scale)) AS fields
FROM information_schema.columns WHERE table_schema='ods' GROUP BY table_name ORDER BY table_name;

-- check: keys
SELECT 'categories' AS table_name, count(*) AS rows, count(*)-count(DISTINCT category_id) AS invalid_keys FROM ods.categories
UNION ALL
SELECT 'customers' AS table_name, count(*) AS rows, count(*)-count(DISTINCT customer_id) AS invalid_keys FROM ods.customers
UNION ALL
SELECT 'products' AS table_name, count(*) AS rows, count(*)-count(DISTINCT product_id) AS invalid_keys FROM ods.products
UNION ALL
SELECT 'orders' AS table_name, count(*) AS rows, count(*)-count(DISTINCT order_id) AS invalid_keys FROM ods.orders
UNION ALL
SELECT 'order_items' AS table_name, count(*) AS rows, count(*)-count(DISTINCT order_item_id) AS invalid_keys FROM ods.order_items
UNION ALL
SELECT 'payments' AS table_name, count(*) AS rows, count(*)-count(DISTINCT payment_id) AS invalid_keys FROM ods.payments
UNION ALL
SELECT 'returns' AS table_name, count(*) AS rows, count(*)-count(DISTINCT return_id) AS invalid_keys FROM ods.returns;

-- check: orphans
SELECT 'categories.parent_category_id' AS relation, count(*) AS orphans FROM ods.categories c LEFT JOIN ods.categories p ON c.parent_category_id=p.category_id WHERE c.parent_category_id IS NOT NULL AND p.category_id IS NULL
UNION ALL
SELECT 'products.category_id' AS relation, count(*) AS orphans FROM ods.products c LEFT JOIN ods.categories p ON c.category_id=p.category_id WHERE c.category_id IS NOT NULL AND p.category_id IS NULL
UNION ALL
SELECT 'orders.customer_id' AS relation, count(*) AS orphans FROM ods.orders c LEFT JOIN ods.customers p ON c.customer_id=p.customer_id WHERE c.customer_id IS NOT NULL AND p.customer_id IS NULL
UNION ALL
SELECT 'order_items.order_id' AS relation, count(*) AS orphans FROM ods.order_items c LEFT JOIN ods.orders p ON c.order_id=p.order_id WHERE c.order_id IS NOT NULL AND p.order_id IS NULL
UNION ALL
SELECT 'order_items.product_id' AS relation, count(*) AS orphans FROM ods.order_items c LEFT JOIN ods.products p ON c.product_id=p.product_id WHERE c.product_id IS NOT NULL AND p.product_id IS NULL
UNION ALL
SELECT 'payments.order_id' AS relation, count(*) AS orphans FROM ods.payments c LEFT JOIN ods.orders p ON c.order_id=p.order_id WHERE c.order_id IS NOT NULL AND p.order_id IS NULL
UNION ALL
SELECT 'returns.order_item_id' AS relation, count(*) AS orphans FROM ods.returns c LEFT JOIN ods.order_items p ON c.order_item_id=p.order_item_id WHERE c.order_item_id IS NOT NULL AND p.order_item_id IS NULL;

-- check: event_times
SELECT 'orders.ordered_at' AS field, min(ordered_at)::text AS first_event,max(ordered_at)::text AS last_event,count(*) FILTER(WHERE ordered_at IS NULL) AS nulls,count(*) FILTER(WHERE NOT isfinite(ordered_at)) AS nonfinite FROM ods.orders
UNION ALL
SELECT 'orders.shipped_at' AS field, min(shipped_at)::text AS first_event,max(shipped_at)::text AS last_event,count(*) FILTER(WHERE shipped_at IS NULL) AS nulls,count(*) FILTER(WHERE NOT isfinite(shipped_at)) AS nonfinite FROM ods.orders
UNION ALL
SELECT 'payments.paid_at' AS field, min(paid_at)::text AS first_event,max(paid_at)::text AS last_event,count(*) FILTER(WHERE paid_at IS NULL) AS nulls,count(*) FILTER(WHERE NOT isfinite(paid_at)) AS nonfinite FROM ods.payments
UNION ALL
SELECT 'returns.returned_at' AS field, min(returned_at)::text AS first_event,max(returned_at)::text AS last_event,count(*) FILTER(WHERE returned_at IS NULL) AS nulls,count(*) FILTER(WHERE NOT isfinite(returned_at)) AS nonfinite FROM ods.returns
UNION ALL
SELECT 'customers.signup_date' AS field, min(signup_date)::text AS first_event,max(signup_date)::text AS last_event,count(*) FILTER(WHERE signup_date IS NULL) AS nulls,count(*) FILTER(WHERE NOT isfinite(signup_date)) AS nonfinite FROM ods.customers
UNION ALL
SELECT 'products.discontinued_at' AS field, min(discontinued_at)::text AS first_event,max(discontinued_at)::text AS last_event,count(*) FILTER(WHERE discontinued_at IS NULL) AS nulls,count(*) FILTER(WHERE NOT isfinite(discontinued_at)) AS nonfinite FROM ods.products;

-- check: money
SELECT 'products.unit_price' AS field,min(unit_price) AS minimum,max(unit_price) AS maximum,count(*) FILTER(WHERE unit_price IS NULL) AS nulls,count(*) FILTER(WHERE unit_price<0) AS negative,count(*) FILTER(WHERE unit_price::text IN ('NaN','Infinity','-Infinity')) AS nonfinite FROM ods.products
UNION ALL
SELECT 'products.unit_cost' AS field,min(unit_cost) AS minimum,max(unit_cost) AS maximum,count(*) FILTER(WHERE unit_cost IS NULL) AS nulls,count(*) FILTER(WHERE unit_cost<0) AS negative,count(*) FILTER(WHERE unit_cost::text IN ('NaN','Infinity','-Infinity')) AS nonfinite FROM ods.products
UNION ALL
SELECT 'order_items.unit_price' AS field,min(unit_price) AS minimum,max(unit_price) AS maximum,count(*) FILTER(WHERE unit_price IS NULL) AS nulls,count(*) FILTER(WHERE unit_price<0) AS negative,count(*) FILTER(WHERE unit_price::text IN ('NaN','Infinity','-Infinity')) AS nonfinite FROM ods.order_items
UNION ALL
SELECT 'order_items.discount_amount' AS field,min(discount_amount) AS minimum,max(discount_amount) AS maximum,count(*) FILTER(WHERE discount_amount IS NULL) AS nulls,count(*) FILTER(WHERE discount_amount<0) AS negative,count(*) FILTER(WHERE discount_amount::text IN ('NaN','Infinity','-Infinity')) AS nonfinite FROM ods.order_items
UNION ALL
SELECT 'payments.amount' AS field,min(amount) AS minimum,max(amount) AS maximum,count(*) FILTER(WHERE amount IS NULL) AS nulls,count(*) FILTER(WHERE amount<0) AS negative,count(*) FILTER(WHERE amount::text IN ('NaN','Infinity','-Infinity')) AS nonfinite FROM ods.payments
UNION ALL
SELECT 'returns.refund_amount' AS field,min(refund_amount) AS minimum,max(refund_amount) AS maximum,count(*) FILTER(WHERE refund_amount IS NULL) AS nulls,count(*) FILTER(WHERE refund_amount<0) AS negative,count(*) FILTER(WHERE refund_amount::text IN ('NaN','Infinity','-Infinity')) AS nonfinite FROM ods.returns;

-- check: order_statuses
SELECT status,count(*) AS rows FROM ods.orders GROUP BY status ORDER BY status;

-- check: payment_statuses
SELECT status,count(*) AS rows,sum(amount) AS amount FROM ods.payments GROUP BY status ORDER BY status;

-- check: channels
SELECT channel,count(*) AS rows FROM ods.orders GROUP BY channel ORDER BY channel;

-- check: payment_methods
SELECT method,count(*) AS rows FROM ods.payments GROUP BY method ORDER BY method;

-- check: multiplicity
SELECT
 (SELECT count(*) FROM (SELECT order_id FROM ods.order_items GROUP BY 1 HAVING count(*)>1) x) AS multi_item_orders,
 (SELECT count(*) FROM (SELECT order_id FROM ods.payments GROUP BY 1 HAVING count(*)>1) x) AS multi_payment_orders,
 (SELECT count(*) FROM (SELECT order_item_id FROM ods.returns GROUP BY 1 HAVING count(*)>1) x) AS multi_return_items;

-- check: return_anomalies
SELECT count(*) FILTER(WHERE r.returned_at<o.ordered_at) AS before_order,
 count(*) FILTER(WHERE r.refund_amount>i.quantity*i.unit_price-i.discount_amount) AS above_item_net
FROM ods.returns r JOIN ods.order_items i USING(order_item_id) JOIN ods.orders o USING(order_id);

-- check: other_anomalies
SELECT (SELECT count(*) FROM ods.orders WHERE shipped_at<ordered_at) AS shipping_before_order,
 (SELECT count(*) FROM ods.payments p JOIN ods.orders o USING(order_id) WHERE p.paid_at<o.ordered_at) AS payment_before_order,
 (SELECT count(*) FROM ods.order_items WHERE quantity<=0 OR discount_amount>quantity*unit_price) AS invalid_item_amount;

-- check: fanout
SELECT count(*) AS joined_rows,sum(i.quantity*i.unit_price-i.discount_amount) AS inflated_item_net,
 sum(p.amount) FILTER(WHERE p.status='captured') AS inflated_captured_amount,
 sum(r.refund_amount) AS inflated_return_amount
FROM ods.orders o JOIN ods.order_items i USING(order_id)
JOIN ods.payments p USING(order_id) LEFT JOIN ods.returns r USING(order_item_id);

-- check: constraints
SELECT c.relname AS table_name,p.contype AS kind,pg_get_constraintdef(p.oid) AS definition
FROM pg_constraint p JOIN pg_class c ON c.oid=p.conrelid
JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='ods' ORDER BY c.relname,p.contype,pg_get_constraintdef(p.oid);

-- check: optional_dimension_nulls
SELECT (SELECT count(*) FROM ods.customers WHERE loyalty_tier IS NULL) AS missing_loyalty_tier,
 (SELECT count(*) FROM ods.products WHERE discontinued_at IS NULL) AS active_product_date_null,
 (SELECT count(*) FROM ods.orders WHERE coupon_code IS NULL) AS missing_coupon,
 (SELECT count(*) FROM ods.categories WHERE parent_category_id IS NULL) AS root_categories;
