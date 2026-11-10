WITH orders AS (
 SELECT ordered_at::date AS order_date,channel,status,count(*) AS order_count
 FROM dwd.order_fact GROUP BY 1,2,3
), items AS (
 SELECT o.ordered_at::date AS order_date,o.channel,o.status,count(*) AS order_item_count,
        sum(i.quantity)::bigint AS ordered_unit_count,sum(i.quantity*i.unit_price) AS item_gross_amount,
        sum(i.discount_amount) AS item_discount_amount,sum(i.net_item_amount) AS item_net_sales_amount
 FROM dwd.order_item_fact i JOIN dwd.order_fact o USING(order_id) GROUP BY 1,2,3
)
SELECT o.order_date,o.channel,o.status,o.order_count,coalesce(i.order_item_count,0) AS order_item_count,
       coalesce(i.ordered_unit_count,0) AS ordered_unit_count,coalesce(i.item_gross_amount,0) AS item_gross_amount,
       coalesce(i.item_discount_amount,0) AS item_discount_amount,coalesce(i.item_net_sales_amount,0) AS item_net_sales_amount
FROM orders o LEFT JOIN items i USING(order_date,channel,status);
