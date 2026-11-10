WITH source_dates AS (
    SELECT signup_date AS value FROM ods.customers UNION ALL
    SELECT discontinued_at FROM ods.products UNION ALL
    SELECT ordered_at::date FROM ods.orders UNION ALL
    SELECT shipped_at::date FROM ods.orders UNION ALL
    SELECT paid_at::date FROM ods.payments UNION ALL
    SELECT returned_at::date FROM ods.returns
), bounds AS (SELECT min(value) lo,max(value) hi FROM source_dates WHERE isfinite(value))
SELECT day::date AS date_value,extract(year FROM day)::integer AS calendar_year,
       extract(month FROM day)::integer AS calendar_month,date_trunc('month',day)::date AS month_start,
       extract(isoyear FROM day)::integer AS iso_week_year,
       extract(week FROM day)::integer AS iso_week,extract(isodow FROM day)::integer AS iso_weekday
FROM bounds CROSS JOIN LATERAL generate_series(lo::timestamp,hi::timestamp,interval '1 day') day;
