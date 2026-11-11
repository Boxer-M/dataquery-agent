-- check: payment_sequence_ods
SELECT order_id
FROM ods.payments
WHERE paid_at >= %(start)s AND paid_at < %(end)s
GROUP BY order_id
HAVING min(paid_at) FILTER (WHERE status = 'failed') < max(paid_at) FILTER (WHERE status = 'captured')
ORDER BY order_id;

-- check: payment_sequence_dwd
SELECT DISTINCT f.order_id
FROM dwd.payment_attempt_fact f
WHERE f.status = 'failed' AND f.paid_at >= %(start)s AND f.paid_at < %(end)s
  AND EXISTS (
    SELECT 1 FROM dwd.payment_attempt_fact c
    WHERE c.order_id = f.order_id AND c.status = 'captured'
      AND c.paid_at > f.paid_at AND c.paid_at < %(end)s
  )
ORDER BY f.order_id;

-- check: sales_by_order_ods
SELECT i.order_id, p.category_id, sum(i.quantity*i.unit_price-i.discount_amount) AS net
FROM ods.order_items i JOIN ods.orders o USING (order_id) JOIN ods.products p USING (product_id)
WHERE o.ordered_at >= %(start)s AND o.ordered_at < %(end)s
GROUP BY i.order_id, p.category_id
ORDER BY i.order_id, p.category_id;

-- check: sales_by_order_dwd
SELECT i.order_id, p.category_id, sum(i.net_item_amount) AS net
FROM dwd.order_item_fact i JOIN dim.product p USING (product_id)
WHERE i.order_purchase_time >= %(start)s AND i.order_purchase_time < %(end)s
GROUP BY i.order_id, p.category_id
ORDER BY i.order_id, p.category_id;
