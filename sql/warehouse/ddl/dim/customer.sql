CREATE TABLE dim.customer (
    customer_id integer NOT NULL,
    city character varying NOT NULL,
    country character varying NOT NULL,
    signup_date date NOT NULL,
    loyalty_tier character varying,
    marketing_opt_in boolean NOT NULL,
    PRIMARY KEY (customer_id)
);
