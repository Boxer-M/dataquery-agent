\set ON_ERROR_STOP on

SELECT table_name, expected_rows, actual_rows, expected_rows = actual_rows AS matches
FROM (
    SELECT 'ods_customers' AS table_name, 99441::BIGINT AS expected_rows, count(*) AS actual_rows FROM ods_customers
    UNION ALL
    SELECT 'ods_geolocation', 1000163, count(*) FROM ods_geolocation
    UNION ALL
    SELECT 'ods_order_items', 112650, count(*) FROM ods_order_items
    UNION ALL
    SELECT 'ods_order_payments', 103886, count(*) FROM ods_order_payments
    UNION ALL
    SELECT 'ods_order_reviews', 99224, count(*) FROM ods_order_reviews
    UNION ALL
    SELECT 'ods_orders', 99441, count(*) FROM ods_orders
    UNION ALL
    SELECT 'ods_products', 32951, count(*) FROM ods_products
    UNION ALL
    SELECT 'ods_sellers', 3095, count(*) FROM ods_sellers
    UNION ALL
    SELECT 'ods_product_category_name_translation', 71, count(*) FROM ods_product_category_name_translation
) AS row_counts
ORDER BY table_name;

SELECT 'ods_customers' AS table_name, to_jsonb(sample) AS sample FROM (SELECT * FROM ods_customers LIMIT 1) AS sample
UNION ALL
SELECT 'ods_geolocation', to_jsonb(sample) FROM (SELECT * FROM ods_geolocation LIMIT 1) AS sample
UNION ALL
SELECT 'ods_order_items', to_jsonb(sample) FROM (SELECT * FROM ods_order_items LIMIT 1) AS sample
UNION ALL
SELECT 'ods_order_payments', to_jsonb(sample) FROM (SELECT * FROM ods_order_payments LIMIT 1) AS sample
UNION ALL
SELECT 'ods_order_reviews', to_jsonb(sample) FROM (SELECT * FROM ods_order_reviews LIMIT 1) AS sample
UNION ALL
SELECT 'ods_orders', to_jsonb(sample) FROM (SELECT * FROM ods_orders LIMIT 1) AS sample
UNION ALL
SELECT 'ods_products', to_jsonb(sample) FROM (SELECT * FROM ods_products LIMIT 1) AS sample
UNION ALL
SELECT 'ods_sellers', to_jsonb(sample) FROM (SELECT * FROM ods_sellers LIMIT 1) AS sample
UNION ALL
SELECT 'ods_product_category_name_translation', to_jsonb(sample) FROM (SELECT * FROM ods_product_category_name_translation LIMIT 1) AS sample;

SELECT
    table_name,
    column_name,
    data_type,
    numeric_precision,
    numeric_scale
FROM information_schema.columns
WHERE table_schema = current_schema()
  AND table_name LIKE 'ods_%'
  AND (
      column_name IN ('price', 'freight_value', 'payment_value')
      OR data_type = 'timestamp without time zone'
  )
ORDER BY table_name, ordinal_position;
