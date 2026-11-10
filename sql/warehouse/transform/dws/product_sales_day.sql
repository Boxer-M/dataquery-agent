SELECT o.ordered_at::date AS order_date,o.channel,i.product_id,count(*) AS order_item_count,
       sum(i.quantity)::bigint AS ordered_unit_count,sum(i.quantity*i.unit_price) AS item_gross_amount,
       sum(i.discount_amount) AS item_discount_amount,sum(i.net_item_amount) AS item_net_sales_amount
FROM dwd.order_item_fact i JOIN dwd.order_fact o USING(order_id) GROUP BY 1,2,3;
