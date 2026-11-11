-- Fixed exclusive observation cutoff. All orders, including those without attempts.
WITH items AS (
 SELECT order_id,sum(net_item_amount) AS item_net_amount
 FROM dwd.order_item_fact GROUP BY order_id
), attempts AS (
 SELECT order_id,count(*) AS payment_attempt_count,
        count(*) FILTER(WHERE status='captured') AS captured_attempt_count,
        count(*) FILTER(WHERE status='failed') AS failed_attempt_count,
        coalesce(sum(amount) FILTER(WHERE status='captured'),0::numeric) AS captured_payment_amount,
        min(paid_at) AS first_payment_at,max(paid_at) AS last_payment_at
 FROM dwd.payment_attempt_fact WHERE paid_at < {as_of} GROUP BY order_id
)
SELECT o.order_id,o.ordered_at,o.customer_id,o.channel,o.status AS order_status,
       coalesce(i.item_net_amount,0::numeric) AS item_net_amount,
       coalesce(p.payment_attempt_count,0::bigint) AS payment_attempt_count,
       coalesce(p.captured_attempt_count,0::bigint) AS captured_attempt_count,
       coalesce(p.failed_attempt_count,0::bigint) AS failed_attempt_count,
       coalesce(p.captured_payment_amount,0::numeric) AS captured_payment_amount,
       coalesce(p.captured_payment_amount,0::numeric)-coalesce(i.item_net_amount,0::numeric) AS captured_minus_item_net,
       coalesce(p.captured_payment_amount,0::numeric)<>coalesce(i.item_net_amount,0::numeric) AS has_amount_mismatch,
       p.first_payment_at,p.last_payment_at,{as_of} AS as_of,
       round(coalesce(p.failed_attempt_count,0)::numeric/nullif(p.payment_attempt_count,0),8) AS payment_failed_attempt_rate,
       coalesce(p.payment_attempt_count,0)>1 AS has_multiple_attempts
FROM dwd.order_fact o LEFT JOIN items i USING(order_id) LEFT JOIN attempts p USING(order_id)
WHERE o.ordered_at < {as_of}
