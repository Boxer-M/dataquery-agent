-- check: business_overview_day
-- Raw source event streams are unioned, never joined at fact grain.
WITH events AS (
 SELECT ordered_at::date AS event_date,channel,1::bigint AS orders,0::numeric AS net,
  0::bigint AS attempts,0::bigint AS captured,0::numeric AS paid,0::bigint AS returns,0::numeric AS refunds,0::bigint AS early,0::bigint AS excess
 FROM ods.orders
 UNION ALL
 SELECT o.ordered_at::date,o.channel,0,i.quantity*i.unit_price-i.discount_amount,0,0,0,0,0,0,0
 FROM ods.order_items i JOIN ods.orders o USING(order_id)
 UNION ALL
 SELECT p.paid_at::date,o.channel,0,0,1,(p.status='captured')::integer,
  CASE WHEN p.status='captured' THEN p.amount ELSE 0 END,0,0,0,0
 FROM ods.payments p JOIN ods.orders o USING(order_id)
 UNION ALL
 SELECT r.returned_at::date,o.channel,0,0,0,0,0,1,r.refund_amount,
  (r.returned_at<o.ordered_at)::integer,(r.refund_amount>i.quantity*i.unit_price-i.discount_amount)::integer
 FROM ods.returns r JOIN ods.order_items i USING(order_item_id) JOIN ods.orders o USING(order_id)
)
SELECT event_date,channel,sum(orders)::bigint AS order_count,sum(net) AS item_net_sales_amount,
 sum(attempts)::bigint AS payment_attempt_count,sum(captured)::bigint AS captured_attempt_count,
 sum(paid) AS captured_payment_amount,sum(returns)::bigint AS return_record_count,sum(refunds) AS return_record_amount,
 sum(early)::bigint AS dq_return_before_order_count,sum(excess)::bigint AS dq_refund_above_item_net_count
FROM events GROUP BY 1,2;

-- check: payment_anomaly_order
-- Union source measures before aggregation; no cross-fact product.
WITH ledger AS (
 SELECT order_id,sum(quantity*unit_price-discount_amount) AS net,0::bigint AS attempts,
  0::bigint AS captured,0::bigint AS failed,0::numeric AS paid,
  NULL::timestamp AS first_at,NULL::timestamp AS last_at FROM ods.order_items GROUP BY 1
 UNION ALL
 SELECT order_id,0,count(*),count(*) FILTER(WHERE status='captured'),
  count(*) FILTER(WHERE status='failed'),coalesce(sum(amount) FILTER(WHERE status='captured'),0),
  min(paid_at),max(paid_at) FROM ods.payments WHERE paid_at<%(as_of)s GROUP BY 1
), measures AS (
 SELECT order_id,sum(net) AS net,sum(attempts)::bigint AS attempts,sum(captured)::bigint AS captured,
  sum(failed)::bigint AS failed,sum(paid) AS paid,min(first_at) AS first_at,max(last_at) AS last_at
 FROM ledger GROUP BY 1
), records AS (
 SELECT o.order_id,o.ordered_at,o.customer_id,o.channel,o.status AS order_status,
 coalesce(m.net,0) AS item_net_amount,coalesce(m.attempts,0::bigint) AS payment_attempt_count,
 coalesce(m.captured,0::bigint) AS captured_attempt_count,coalesce(m.failed,0::bigint) AS failed_attempt_count,
 coalesce(m.paid,0) AS captured_payment_amount,m.first_at AS first_payment_at,m.last_at AS last_payment_at
 FROM ods.orders o LEFT JOIN measures m USING(order_id) WHERE o.ordered_at<%(as_of)s
)
SELECT order_id,ordered_at,customer_id,channel,order_status,item_net_amount,payment_attempt_count,
 captured_attempt_count,failed_attempt_count,captured_payment_amount,
 captured_payment_amount-item_net_amount AS captured_minus_item_net,
 captured_payment_amount<>item_net_amount AS has_amount_mismatch,
 first_payment_at,last_payment_at,%(as_of)s::timestamp AS as_of,
 round(failed_attempt_count::numeric/nullif(payment_attempt_count,0),8) AS payment_failed_attempt_rate,
 payment_attempt_count>1 AS has_multiple_attempts FROM records;

-- check: product_return_cohort
WITH items AS (
 SELECT o.ordered_at::date AS order_date,o.channel,i.product_id,p.category_id,i.order_item_id,
 count(r.return_id)>0 AS returned,
 coalesce(bool_or(r.returned_at<o.ordered_at OR r.refund_amount>i.quantity*i.unit_price-i.discount_amount OR r.refund_amount<0),false) AS anomalous
 FROM ods.order_items i JOIN ods.orders o USING(order_id) JOIN ods.products p USING(product_id)
 LEFT JOIN ods.returns r ON r.order_item_id=i.order_item_id AND r.returned_at<%(as_of)s
 GROUP BY o.ordered_at::date,o.channel,i.product_id,p.category_id,i.order_item_id
)
SELECT order_date,channel,product_id,%(as_of)s::timestamp AS as_of,category_id,
 count(*) AS cohort_item_count,count(*) FILTER(WHERE returned) AS returned_item_count,
 count(*) FILTER(WHERE anomalous) AS anomalous_return_item_count,
 round(count(*) FILTER(WHERE returned)::numeric/nullif(count(*),0),8) AS item_return_rate
FROM items GROUP BY 1,2,3,5;
