CREATE TABLE dws.product_sales_day (
    order_date date NOT NULL,
    channel character varying NOT NULL,
    product_id integer NOT NULL,
    order_item_count bigint NOT NULL,
    ordered_unit_count bigint NOT NULL,
    item_gross_amount numeric NOT NULL,
    item_discount_amount numeric NOT NULL,
    item_net_sales_amount numeric NOT NULL,
    PRIMARY KEY (order_date, channel, product_id)
);
