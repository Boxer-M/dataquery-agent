-- Purchase cohort only. Event-day return amounts belong to return_day/overview.
SELECT c.order_date,c.channel,c.product_id,c.as_of,p.category_id,
       c.cohort_item_count,c.returned_item_count,c.anomalous_return_item_count,
       round(c.returned_item_count::numeric/nullif(c.cohort_item_count,0),8) AS item_return_rate
FROM dws.item_return_cohort_day c JOIN dim.product p USING(product_id)
WHERE c.as_of={as_of}
