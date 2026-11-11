SELECT i.order_id, SUM(i.net_item_amount) AS net_amount
FROM dwd.order_item_fact i

WHERE i.order_purchase_time >= %(start)s AND i.order_purchase_time < %(end)s
GROUP BY i.order_id
ORDER BY i.order_id
