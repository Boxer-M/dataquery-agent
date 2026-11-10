CREATE TABLE dwd.order_fact (
    order_id integer NOT NULL,
    customer_id integer NOT NULL,
    ordered_at timestamp without time zone NOT NULL,
    shipped_at timestamp without time zone,
    status character varying NOT NULL,
    channel character varying NOT NULL,
    shipping_country character varying NOT NULL,
    coupon_code character varying,
    dq_shipping_before_order boolean NOT NULL,
    dq_unknown_order_status boolean NOT NULL,
    PRIMARY KEY (order_id),
    FOREIGN KEY (customer_id) REFERENCES dim.customer(customer_id),
    FOREIGN KEY (channel) REFERENCES dim.channel(channel_code)
);
