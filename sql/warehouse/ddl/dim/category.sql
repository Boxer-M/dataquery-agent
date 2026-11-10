CREATE TABLE dim.category (
    category_id integer NOT NULL,
    category_name character varying NOT NULL,
    parent_category_id integer,
    PRIMARY KEY (category_id),
    FOREIGN KEY (parent_category_id) REFERENCES dim.category(category_id)
);
