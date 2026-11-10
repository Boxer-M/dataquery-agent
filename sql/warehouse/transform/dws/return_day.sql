SELECT r.returned_at::date AS return_date,o.channel,r.product_id,count(*) AS return_record_count,
       sum(r.refund_amount) AS return_record_amount,
       count(*) FILTER(WHERE r.dq_return_before_order) AS dq_return_before_order_count,
       count(*) FILTER(WHERE r.dq_refund_above_item_net) AS dq_refund_above_item_net_count,
       count(*) FILTER(WHERE r.dq_negative_amount) AS dq_negative_amount_count
FROM dwd.return_record_fact r JOIN dwd.order_fact o USING(order_id) GROUP BY 1,2,3;
