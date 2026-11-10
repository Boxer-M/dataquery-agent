SELECT r.return_id,r.order_item_id,r.returned_at,r.reason,r.refund_amount,r.restocked,
       i.order_id,i.product_id,r.returned_at<o.ordered_at AS dq_return_before_order,
       r.refund_amount>i.net_item_amount AS dq_refund_above_item_net,r.refund_amount<0 AS dq_negative_amount
FROM ods.returns r JOIN dwd.order_item_fact i ON i.order_item_id=r.order_item_id
JOIN dwd.order_fact o ON o.order_id=i.order_id;
