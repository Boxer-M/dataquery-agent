SELECT p.paid_at::date AS payment_date,o.channel,p.method,p.status,count(*) AS payment_attempt_count,
       sum(p.amount) AS attempt_amount,count(*) FILTER(WHERE p.dq_payment_before_order) AS dq_payment_before_order_count,
       count(*) FILTER(WHERE p.dq_negative_amount) AS dq_negative_amount_count
FROM dwd.payment_attempt_fact p JOIN dwd.order_fact o USING(order_id) GROUP BY 1,2,3,4;
