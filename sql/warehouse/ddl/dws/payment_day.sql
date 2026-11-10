CREATE TABLE dws.payment_day (
    payment_date date NOT NULL,
    channel character varying NOT NULL,
    method character varying NOT NULL,
    status character varying NOT NULL,
    payment_attempt_count bigint NOT NULL,
    attempt_amount numeric NOT NULL,
    dq_payment_before_order_count bigint NOT NULL,
    dq_negative_amount_count numeric NOT NULL,
    PRIMARY KEY (payment_date, channel, method, status)
);
