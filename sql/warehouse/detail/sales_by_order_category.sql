SELECT i.order_id, p.category_id, SUM(i.net_item_amount) AS net_amount
FROM dwd.order_item_fact i
JOIN dim.product p ON p.product_id = i.product_id
WHERE i.order_purchase_time >= %(start)s AND i.order_purchase_time < %(end)s
GROUP BY i.order_id, p.category_id
ORDER BY i.order_id, p.category_id
