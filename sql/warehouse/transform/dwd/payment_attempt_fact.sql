SELECT p.payment_id,p.order_id,p.method,p.amount,p.paid_at,p.status,p.failure_reason,
       p.paid_at<o.ordered_at AS dq_payment_before_order,p.amount<0 AS dq_negative_amount,
       p.status NOT IN ('captured','failed') AS dq_unknown_payment_status
FROM ods.payments p JOIN dwd.order_fact o ON o.order_id=p.order_id;
