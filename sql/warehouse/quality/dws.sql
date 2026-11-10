-- check: relations
WITH records AS (
 SELECT order_date AS day,channel,NULL::integer AS product_id,NULL::integer AS customer_id FROM dws.order_day
 UNION ALL SELECT order_date,channel,product_id,NULL FROM dws.product_sales_day
 UNION ALL SELECT payment_date,channel,NULL,NULL FROM dws.payment_day
 UNION ALL SELECT return_date,channel,product_id,NULL FROM dws.return_day
 UNION ALL SELECT order_date,channel,product_id,NULL FROM dws.item_return_cohort_day
 UNION ALL SELECT order_date,channel,NULL,customer_id FROM dws.customer_order_day
)
SELECT count(*) FROM records r
LEFT JOIN dim.date d ON d.date_value=r.day LEFT JOIN dim.channel c ON c.channel_code=r.channel
LEFT JOIN dim.product p ON p.product_id=r.product_id LEFT JOIN dim.customer u ON u.customer_id=r.customer_id
WHERE d.date_value IS NULL OR c.channel_code IS NULL
 OR (r.product_id IS NOT NULL AND p.product_id IS NULL)
 OR (r.customer_id IS NOT NULL AND u.customer_id IS NULL);

-- check: cohort_bounds
SELECT count(*) FROM dws.item_return_cohort_day WHERE returned_item_count<0
 OR returned_item_count>cohort_item_count OR anomalous_return_item_count<0
 OR anomalous_return_item_count>returned_item_count OR as_of<>%(as_of)s;

-- check: totals
SELECT (SELECT sum(order_count) FROM dws.order_day) AS orders,
 (SELECT sum(order_item_count) FROM dws.order_day) AS items,
 (SELECT sum(item_net_sales_amount) FROM dws.order_day) AS order_net,
 (SELECT sum(item_net_sales_amount) FROM dws.product_sales_day) AS product_net,
 (SELECT sum(item_net_sales_amount) FROM dws.customer_order_day) AS customer_net,
 (SELECT sum(payment_attempt_count) FROM dws.payment_day) AS attempts,
 (SELECT sum(attempt_amount) FROM dws.payment_day WHERE status='captured') AS captured,
 (SELECT sum(return_record_count) FROM dws.return_day) AS returns,
 (SELECT sum(return_record_amount) FROM dws.return_day) AS return_amount,
 (SELECT sum(dq_return_before_order_count) FROM dws.return_day) AS before_order,
 (SELECT sum(dq_refund_above_item_net_count) FROM dws.return_day) AS above_item_net,
 (SELECT sum(cohort_item_count) FROM dws.item_return_cohort_day) AS cohort_items,
 (SELECT sum(returned_item_count) FROM dws.item_return_cohort_day) AS returned_items,
 (SELECT sum(anomalous_return_item_count) FROM dws.item_return_cohort_day) AS anomalous_return_items;

-- check: source_end
SELECT (max(ordered_at)::date+1)::timestamp FROM dwd.order_fact;

-- check: protected_objects
SELECT n.nspname,c.relname,c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='ads' ORDER BY 1,2;
