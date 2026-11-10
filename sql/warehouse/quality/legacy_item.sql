-- check: source_orders
SELECT count(*),count(DISTINCT order_id) FROM ods.orders;

-- check: source_items
SELECT count(*),count(DISTINCT order_item_id) FROM ods.order_items;

-- check: source_invalid
SELECT count(*) FROM ods.order_items i
        LEFT JOIN ods.orders o ON o.order_id=i.order_id
        WHERE o.order_id IS NULL OR i.order_id IS NULL OR i.product_id IS NULL
        OR i.quantity IS NULL OR i.unit_price IS NULL OR i.discount_amount IS NULL
        OR o.ordered_at IS NULL OR o.status IS NULL
        OR i.unit_price::text IN ('NaN','Infinity','-Infinity')
        OR i.discount_amount::text IN ('NaN','Infinity','-Infinity');

-- check: item_summary
SELECT count(*),count(DISTINCT order_item_id),
        count(*) FILTER(WHERE order_item_id IS NULL),sum(net_item_amount)
        FROM dwd.order_item_fact;

-- check: source_amount
SELECT sum(quantity*unit_price-discount_amount) FROM ods.order_items;

-- check: cancelled_summary
SELECT count(*),sum(net_item_amount) FROM dwd.order_item_fact
        WHERE order_status='cancelled';
