-- Independent event clocks; no order-cohort cash balance is implied.
WITH orders AS (
 SELECT order_date AS event_date,channel,sum(order_count)::bigint AS order_count,
        sum(item_net_sales_amount) AS item_net_sales_amount FROM dws.order_day GROUP BY 1,2
), payments AS (
 SELECT payment_date AS event_date,channel,sum(payment_attempt_count)::bigint AS payment_attempt_count,
        sum(payment_attempt_count) FILTER(WHERE status='captured')::bigint AS captured_attempt_count,
        sum(attempt_amount) FILTER(WHERE status='captured') AS captured_payment_amount
 FROM dws.payment_day GROUP BY 1,2
), returns AS (
 SELECT return_date AS event_date,channel,sum(return_record_count)::bigint AS return_record_count,
        sum(return_record_amount) AS return_record_amount,
        sum(dq_return_before_order_count)::bigint AS dq_return_before_order_count,
        sum(dq_refund_above_item_net_count)::bigint AS dq_refund_above_item_net_count FROM dws.return_day GROUP BY 1,2
)
SELECT coalesce(o.event_date,p.event_date,r.event_date) AS event_date,
       coalesce(o.channel,p.channel,r.channel) AS channel,
       coalesce(o.order_count,0::bigint) AS order_count,
       coalesce(o.item_net_sales_amount,0::numeric) AS item_net_sales_amount,
       coalesce(p.payment_attempt_count,0::bigint) AS payment_attempt_count,
       coalesce(p.captured_attempt_count,0::bigint) AS captured_attempt_count,
       coalesce(p.captured_payment_amount,0::numeric) AS captured_payment_amount,
       coalesce(r.return_record_count,0::bigint) AS return_record_count,
       coalesce(r.return_record_amount,0::numeric) AS return_record_amount,
       coalesce(r.dq_return_before_order_count,0::bigint) AS dq_return_before_order_count,
       coalesce(r.dq_refund_above_item_net_count,0::bigint) AS dq_refund_above_item_net_count
FROM orders o FULL JOIN payments p USING(event_date,channel)
FULL JOIN returns r ON r.event_date=coalesce(o.event_date,p.event_date)
 AND r.channel=coalesce(o.channel,p.channel)
