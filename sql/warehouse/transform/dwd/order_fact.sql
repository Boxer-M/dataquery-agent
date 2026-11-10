SELECT order_id,customer_id,ordered_at,shipped_at,status,channel,shipping_country,coupon_code,
       coalesce(shipped_at<ordered_at,false) AS dq_shipping_before_order,
       status NOT IN ('cancelled','delivered','processing','returned','shipped') AS dq_unknown_order_status
FROM ods.orders;
