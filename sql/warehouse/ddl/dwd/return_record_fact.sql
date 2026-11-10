CREATE TABLE dwd.return_record_fact (
    return_id integer NOT NULL,
    order_item_id integer NOT NULL,
    returned_at timestamp without time zone NOT NULL,
    reason character varying NOT NULL,
    refund_amount numeric(12,2) NOT NULL,
    restocked boolean NOT NULL,
    order_id integer NOT NULL,
    product_id integer NOT NULL,
    dq_return_before_order boolean NOT NULL,
    dq_refund_above_item_net boolean NOT NULL,
    dq_negative_amount boolean NOT NULL,
    PRIMARY KEY (return_id),
    FOREIGN KEY (order_id) REFERENCES dwd.order_fact(order_id),
    FOREIGN KEY (product_id) REFERENCES dim.product(product_id)
);
