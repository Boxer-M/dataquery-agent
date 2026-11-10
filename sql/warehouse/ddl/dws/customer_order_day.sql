CREATE TABLE dws.customer_order_day (
    order_date date NOT NULL,
    customer_id integer NOT NULL,
    channel character varying NOT NULL,
    order_count bigint NOT NULL,
    order_item_count bigint NOT NULL,
    item_net_sales_amount numeric NOT NULL,
    PRIMARY KEY (order_date, customer_id, channel)
);
