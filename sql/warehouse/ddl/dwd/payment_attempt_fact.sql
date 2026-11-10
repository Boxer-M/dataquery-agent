CREATE TABLE dwd.payment_attempt_fact (
    payment_id integer NOT NULL,
    order_id integer NOT NULL,
    method character varying NOT NULL,
    amount numeric(12,2) NOT NULL,
    paid_at timestamp without time zone NOT NULL,
    status character varying NOT NULL,
    failure_reason character varying,
    dq_payment_before_order boolean NOT NULL,
    dq_negative_amount boolean NOT NULL,
    dq_unknown_payment_status boolean NOT NULL,
    PRIMARY KEY (payment_id),
    FOREIGN KEY (order_id) REFERENCES dwd.order_fact(order_id)
);
