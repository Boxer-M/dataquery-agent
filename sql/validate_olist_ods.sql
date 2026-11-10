\set ON_ERROR_STOP on

SELECT table_name, expected_rows, actual_rows, expected_rows = actual_rows AS matches
FROM (
    SELECT 'customers' AS table_name, 99441::BIGINT AS expected_rows, count(*) AS actual_rows FROM ods.customers
    UNION ALL
    SELECT 'geolocation', 1000163, count(*) FROM ods.geolocation
    UNION ALL
    SELECT 'order_items', 112650, count(*) FROM ods.order_items
    UNION ALL
    SELECT 'order_payments', 103886, count(*) FROM ods.order_payments
    UNION ALL
    SELECT 'order_reviews', 99224, count(*) FROM ods.order_reviews
    UNION ALL
    SELECT 'orders', 99441, count(*) FROM ods.orders
    UNION ALL
    SELECT 'products', 32951, count(*) FROM ods.products
    UNION ALL
    SELECT 'sellers', 3095, count(*) FROM ods.sellers
    UNION ALL
    SELECT 'product_category_name_translation', 71, count(*) FROM ods.product_category_name_translation
) AS row_counts
ORDER BY table_name;

SELECT 'customers' AS table_name, to_jsonb(sample) AS sample FROM (SELECT * FROM ods.customers LIMIT 1) AS sample
UNION ALL
SELECT 'geolocation', to_jsonb(sample) FROM (SELECT * FROM ods.geolocation LIMIT 1) AS sample
UNION ALL
SELECT 'order_items', to_jsonb(sample) FROM (SELECT * FROM ods.order_items LIMIT 1) AS sample
UNION ALL
SELECT 'order_payments', to_jsonb(sample) FROM (SELECT * FROM ods.order_payments LIMIT 1) AS sample
UNION ALL
SELECT 'order_reviews', to_jsonb(sample) FROM (SELECT * FROM ods.order_reviews LIMIT 1) AS sample
UNION ALL
SELECT 'orders', to_jsonb(sample) FROM (SELECT * FROM ods.orders LIMIT 1) AS sample
UNION ALL
SELECT 'products', to_jsonb(sample) FROM (SELECT * FROM ods.products LIMIT 1) AS sample
UNION ALL
SELECT 'sellers', to_jsonb(sample) FROM (SELECT * FROM ods.sellers LIMIT 1) AS sample
UNION ALL
SELECT 'product_category_name_translation', to_jsonb(sample) FROM (SELECT * FROM ods.product_category_name_translation LIMIT 1) AS sample;

SELECT
    table_name,
    column_name,
    data_type,
    numeric_precision,
    numeric_scale
FROM information_schema.columns
WHERE table_schema = 'ods'
  AND (
      column_name IN ('price', 'freight_value', 'payment_value')
      OR data_type = 'timestamp without time zone'
  )
ORDER BY table_name, ordinal_position;
