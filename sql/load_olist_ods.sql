\set ON_ERROR_STOP on

BEGIN;

DO $guard$
DECLARE
    target_table TEXT;
    existing_rows BIGINT;
BEGIN
    FOREACH target_table IN ARRAY ARRAY[
        'customers',
        'geolocation',
        'order_items',
        'order_payments',
        'order_reviews',
        'orders',
        'products',
        'sellers',
        'product_category_name_translation'
    ]
    LOOP
        IF to_regclass('ods.' || target_table) IS NOT NULL THEN
            EXECUTE format('SELECT count(*) FROM ods.%I', target_table)
                INTO existing_rows;
            RAISE EXCEPTION 'Target table % already exists with % rows',
                target_table, existing_rows;
        END IF;
    END LOOP;
END
$guard$;

CREATE TABLE ods.customers (
    customer_id TEXT,
    customer_unique_id TEXT,
    customer_zip_code_prefix TEXT,
    customer_city TEXT,
    customer_state TEXT
);

CREATE TABLE ods.geolocation (
    geolocation_zip_code_prefix TEXT,
    geolocation_lat NUMERIC(23, 20),
    geolocation_lng NUMERIC(23, 20),
    geolocation_city TEXT,
    geolocation_state TEXT
);

CREATE TABLE ods.order_items (
    order_id TEXT,
    order_item_id SMALLINT,
    product_id TEXT,
    seller_id TEXT,
    shipping_limit_date TIMESTAMP,
    price NUMERIC(12, 2),
    freight_value NUMERIC(12, 2)
);

CREATE TABLE ods.order_payments (
    order_id TEXT,
    payment_sequential SMALLINT,
    payment_type TEXT,
    payment_installments SMALLINT,
    payment_value NUMERIC(12, 2)
);

CREATE TABLE ods.order_reviews (
    review_id TEXT,
    order_id TEXT,
    review_score SMALLINT,
    review_comment_title TEXT,
    review_comment_message TEXT,
    review_creation_date TIMESTAMP,
    review_answer_timestamp TIMESTAMP
);

CREATE TABLE ods.orders (
    order_id TEXT,
    customer_id TEXT,
    order_status TEXT,
    order_purchase_timestamp TIMESTAMP,
    order_approved_at TIMESTAMP,
    order_delivered_carrier_date TIMESTAMP,
    order_delivered_customer_date TIMESTAMP,
    order_estimated_delivery_date TIMESTAMP
);

CREATE TABLE ods.products (
    product_id TEXT,
    product_category_name TEXT,
    product_name_lenght SMALLINT,
    product_description_lenght SMALLINT,
    product_photos_qty SMALLINT,
    product_weight_g INTEGER,
    product_length_cm SMALLINT,
    product_height_cm SMALLINT,
    product_width_cm SMALLINT
);

CREATE TABLE ods.sellers (
    seller_id TEXT,
    seller_zip_code_prefix TEXT,
    seller_city TEXT,
    seller_state TEXT
);

CREATE TABLE ods.product_category_name_translation (
    product_category_name TEXT,
    product_category_name_english TEXT
);

\copy ods.customers FROM '/tmp/dataquery_agent_olist/olist_customers_dataset.csv' WITH (FORMAT csv, HEADER true, NULL '', ENCODING 'UTF8')
\copy ods.geolocation FROM '/tmp/dataquery_agent_olist/olist_geolocation_dataset.csv' WITH (FORMAT csv, HEADER true, NULL '', ENCODING 'UTF8')
\copy ods.order_items FROM '/tmp/dataquery_agent_olist/olist_order_items_dataset.csv' WITH (FORMAT csv, HEADER true, NULL '', ENCODING 'UTF8')
\copy ods.order_payments FROM '/tmp/dataquery_agent_olist/olist_order_payments_dataset.csv' WITH (FORMAT csv, HEADER true, NULL '', ENCODING 'UTF8')
\copy ods.order_reviews FROM '/tmp/dataquery_agent_olist/olist_order_reviews_dataset.csv' WITH (FORMAT csv, HEADER true, NULL '', ENCODING 'UTF8')
\copy ods.orders FROM '/tmp/dataquery_agent_olist/olist_orders_dataset.csv' WITH (FORMAT csv, HEADER true, NULL '', ENCODING 'UTF8')
\copy ods.products FROM '/tmp/dataquery_agent_olist/olist_products_dataset.csv' WITH (FORMAT csv, HEADER true, NULL '', ENCODING 'UTF8')
\copy ods.sellers FROM '/tmp/dataquery_agent_olist/olist_sellers_dataset.csv' WITH (FORMAT csv, HEADER true, NULL '', ENCODING 'UTF8')
\copy ods.product_category_name_translation FROM '/tmp/dataquery_agent_olist/product_category_name_translation.csv' WITH (FORMAT csv, HEADER true, NULL '', ENCODING 'UTF8')

COMMIT;
