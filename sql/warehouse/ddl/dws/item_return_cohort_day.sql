CREATE TABLE dws.item_return_cohort_day (
    order_date date NOT NULL,
    channel character varying NOT NULL,
    product_id integer NOT NULL,
    as_of timestamp without time zone NOT NULL,
    cohort_item_count bigint NOT NULL,
    returned_item_count bigint NOT NULL,
    anomalous_return_item_count bigint NOT NULL,
    PRIMARY KEY (order_date, channel, product_id, as_of)
);
