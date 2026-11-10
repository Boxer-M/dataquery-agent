CREATE TABLE dws.return_day (
    return_date date NOT NULL,
    channel character varying NOT NULL,
    product_id integer NOT NULL,
    return_record_count bigint NOT NULL,
    return_record_amount numeric NOT NULL,
    dq_return_before_order_count bigint NOT NULL,
    dq_refund_above_item_net_count bigint NOT NULL,
    dq_negative_amount_count numeric NOT NULL,
    PRIMARY KEY (return_date, channel, product_id)
);
