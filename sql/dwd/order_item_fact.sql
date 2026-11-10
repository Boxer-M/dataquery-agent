-- check: create_table
CREATE TABLE dwd.order_item_fact (
    order_item_id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL,
    unit_price NUMERIC(12,2) NOT NULL,
    discount_amount NUMERIC(12,2) NOT NULL,
    net_item_amount NUMERIC NOT NULL,
    order_purchase_time TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    order_status VARCHAR NOT NULL,
    dq_nonpositive_quantity BOOLEAN NOT NULL,
    dq_negative_amount BOOLEAN NOT NULL,
    dq_discount_exceeds_gross BOOLEAN NOT NULL,
    dq_unknown_order_status BOOLEAN NOT NULL,
    dq_invalid_order_time BOOLEAN NOT NULL
);

-- check: projection
SELECT i.order_item_id, i.order_id, i.product_id, i.quantity,
       i.unit_price, i.discount_amount,
       i.quantity * i.unit_price - i.discount_amount AS net_item_amount,
       o.ordered_at AS order_purchase_time, o.status AS order_status,
       i.quantity <= 0 AS dq_nonpositive_quantity,
       i.unit_price < 0 OR i.discount_amount < 0 AS dq_negative_amount,
       i.discount_amount > i.quantity * i.unit_price AS dq_discount_exceeds_gross,
       o.status NOT IN ('cancelled','delivered','processing','returned','shipped') AS dq_unknown_order_status,
       NOT isfinite(o.ordered_at) AS dq_invalid_order_time
FROM ods.order_items i JOIN ods.orders o ON i.order_id = o.order_id;
