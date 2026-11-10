CREATE TABLE dim.product (
    product_id integer NOT NULL,
    sku character varying NOT NULL,
    product_name character varying NOT NULL,
    category_id integer NOT NULL,
    current_unit_price numeric(12,2) NOT NULL,
    current_unit_cost numeric(12,2) NOT NULL,
    discontinued_at date,
    PRIMARY KEY (product_id),
    FOREIGN KEY (category_id) REFERENCES dim.category(category_id)
);
