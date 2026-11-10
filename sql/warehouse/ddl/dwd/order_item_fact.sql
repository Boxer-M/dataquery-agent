CREATE TABLE dwd.order_item_fact (
    order_item_id integer NOT NULL,
    order_id integer NOT NULL,
    product_id integer NOT NULL,
    quantity integer NOT NULL,
    unit_price numeric(12,2) NOT NULL,
    discount_amount numeric(12,2) NOT NULL,
    net_item_amount numeric NOT NULL,
    order_purchase_time timestamp without time zone NOT NULL,
    order_status character varying NOT NULL,
    dq_nonpositive_quantity boolean NOT NULL,
    dq_negative_amount boolean NOT NULL,
    dq_discount_exceeds_gross boolean NOT NULL,
    dq_unknown_order_status boolean NOT NULL,
    dq_invalid_order_time boolean NOT NULL,
    PRIMARY KEY (order_item_id)
);
