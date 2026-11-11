SELECT i.order_id, o.channel, SUM(i.net_item_amount) AS net_amount
FROM dwd.order_item_fact i
JOIN dwd.order_fact o ON o.order_id = i.order_id
WHERE i.order_purchase_time >= %(start)s AND i.order_purchase_time < %(end)s
GROUP BY i.order_id, o.channel
ORDER BY i.order_id, o.channel
