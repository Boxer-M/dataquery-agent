-- check: order_day
WITH per_order AS (
 SELECT order_id,count(*) AS items,sum(quantity)::bigint AS units,
        sum(quantity*unit_price) AS gross,sum(discount_amount) AS discount,
        sum(quantity*unit_price-discount_amount) AS net
 FROM ods.order_items GROUP BY order_id
)
SELECT o.ordered_at::date,o.channel,o.status,count(*),coalesce(sum(i.items),0)::bigint,coalesce(sum(i.units),0)::bigint,coalesce(sum(i.gross),0),coalesce(sum(i.discount),0),coalesce(sum(i.net),0)
FROM ods.orders o LEFT JOIN per_order i USING(order_id) GROUP BY 1,2,3;

-- check: customer_order_day
WITH per_order AS (
 SELECT order_id,count(*) AS items,sum(quantity)::bigint AS units,
        sum(quantity*unit_price) AS gross,sum(discount_amount) AS discount,
        sum(quantity*unit_price-discount_amount) AS net
 FROM ods.order_items GROUP BY order_id
)
SELECT o.ordered_at::date,o.customer_id,o.channel,count(*),coalesce(sum(i.items),0)::bigint,coalesce(sum(i.net),0)
FROM ods.orders o LEFT JOIN per_order i USING(order_id) GROUP BY 1,2,3;

-- check: product_sales_day
SELECT o.ordered_at::date,o.channel,i.product_id,count(*),sum(i.quantity)::bigint,
       sum(i.quantity*i.unit_price),sum(i.discount_amount),sum(i.quantity*i.unit_price-i.discount_amount)
FROM ods.orders o JOIN ods.order_items i USING(order_id) GROUP BY 1,2,3;

-- check: payment_day
SELECT p.paid_at::date,o.channel,p.method,p.status,count(*),sum(p.amount),
       count(*) FILTER(WHERE p.paid_at<o.ordered_at),count(*) FILTER(WHERE p.amount<0)
FROM ods.payments p JOIN ods.orders o USING(order_id) GROUP BY 1,2,3,4;

-- check: return_day
SELECT r.returned_at::date,o.channel,i.product_id,count(*),sum(r.refund_amount),
       count(*) FILTER(WHERE r.returned_at<o.ordered_at),
       count(*) FILTER(WHERE r.refund_amount>i.quantity*i.unit_price-i.discount_amount),
       count(*) FILTER(WHERE r.refund_amount<0)
FROM ods.returns r JOIN ods.order_items i USING(order_item_id) JOIN ods.orders o USING(order_id)
GROUP BY 1,2,3;

-- check: item_return_cohort_day
SELECT o.ordered_at::date,o.channel,i.product_id,%(as_of)s::timestamp,
       count(DISTINCT i.order_item_id),count(DISTINCT r.order_item_id),
       count(DISTINCT i.order_item_id) FILTER(WHERE r.returned_at<o.ordered_at
            OR r.refund_amount>i.quantity*i.unit_price-i.discount_amount OR r.refund_amount<0)
FROM ods.order_items i JOIN ods.orders o USING(order_id)
LEFT JOIN ods.returns r ON r.order_item_id=i.order_item_id AND r.returned_at<%(as_of)s
GROUP BY 1,2,3,4;
