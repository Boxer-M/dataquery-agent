WITH orders AS (
 SELECT ordered_at::date AS order_date,customer_id,channel,count(*) AS order_count
 FROM dwd.order_fact GROUP BY 1,2,3
), items AS (
 SELECT o.ordered_at::date AS order_date,o.customer_id,o.channel,count(*) AS order_item_count,
        sum(i.net_item_amount) AS item_net_sales_amount
 FROM dwd.order_item_fact i JOIN dwd.order_fact o USING(order_id) GROUP BY 1,2,3
)
SELECT o.order_date,o.customer_id,o.channel,o.order_count,coalesce(i.order_item_count,0) AS order_item_count,coalesce(i.item_net_sales_amount,0) AS item_net_sales_amount
FROM orders o LEFT JOIN items i USING(order_date,customer_id,channel);
