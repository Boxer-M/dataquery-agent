WITH observed_returns AS (
 SELECT order_item_id,bool_or(dq_return_before_order OR dq_refund_above_item_net OR dq_negative_amount) AS anomalous
 FROM dwd.return_record_fact WHERE returned_at < {as_of} GROUP BY order_item_id
)
SELECT o.ordered_at::date AS order_date,o.channel,i.product_id,{as_of} AS as_of,
       count(*) AS cohort_item_count,count(r.order_item_id) AS returned_item_count,
       count(*) FILTER(WHERE r.anomalous) AS anomalous_return_item_count
FROM dwd.order_item_fact i JOIN dwd.order_fact o USING(order_id)
LEFT JOIN observed_returns r USING(order_item_id) GROUP BY 1,2,3,4;
