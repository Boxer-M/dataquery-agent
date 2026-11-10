-- metric: order_count
WITH base AS (
SELECT s.* FROM dws.order_day s WHERE s.order_date >= %(start)s AND s.order_date < %(end)s AND (%(channel)s::text IS NULL OR s.channel=%(channel)s::text)
)
SELECT {bucket} AS bucket, coalesce(sum(order_count),0) AS value FROM base {group_by};

-- metric: item_net_sales_amount
WITH base AS (
SELECT s.*,p.category_id FROM dws.product_sales_day s JOIN dim.product p USING(product_id) WHERE s.order_date >= %(start)s AND s.order_date < %(end)s AND (%(channel)s::text IS NULL OR s.channel=%(channel)s::text) AND (%(category)s::integer IS NULL OR p.category_id=%(category)s::integer)
)
SELECT {bucket} AS bucket, coalesce(sum(item_net_sales_amount),0) AS value FROM base {group_by};

-- metric: payment_attempt_count
WITH base AS (
SELECT s.* FROM dws.payment_day s WHERE s.payment_date >= %(start)s AND s.payment_date < %(end)s AND (%(channel)s::text IS NULL OR s.channel=%(channel)s::text)
)
SELECT {bucket} AS bucket, coalesce(sum(payment_attempt_count),0) AS value FROM base {group_by};

-- metric: captured_payment_amount
WITH base AS (
SELECT s.* FROM dws.payment_day s WHERE s.payment_date >= %(start)s AND s.payment_date < %(end)s AND (%(channel)s::text IS NULL OR s.channel=%(channel)s::text)
)
SELECT {bucket} AS bucket, coalesce(sum(attempt_amount) FILTER(WHERE status='captured'),0) AS value FROM base WHERE status='captured' {group_by};

-- metric: payment_attempt_success_rate
WITH base AS (
SELECT s.* FROM dws.payment_day s WHERE s.payment_date >= %(start)s AND s.payment_date < %(end)s AND (%(channel)s::text IS NULL OR s.channel=%(channel)s::text)
)
SELECT {bucket} AS bucket, round((coalesce(sum(payment_attempt_count) FILTER(WHERE status='captured'),0))::numeric/nullif((coalesce(sum(payment_attempt_count),0)),0),8) AS value, coalesce(sum(payment_attempt_count) FILTER(WHERE status='captured'),0) AS numerator, coalesce(sum(payment_attempt_count),0) AS denominator FROM base {group_by};

-- metric: return_record_count
WITH base AS (
SELECT s.*,p.category_id FROM dws.return_day s JOIN dim.product p USING(product_id) WHERE s.return_date >= %(start)s AND s.return_date < %(end)s AND (%(channel)s::text IS NULL OR s.channel=%(channel)s::text) AND (%(category)s::integer IS NULL OR p.category_id=%(category)s::integer)
)
SELECT {bucket} AS bucket, coalesce(sum(return_record_count),0) AS value FROM base {group_by};

-- metric: return_record_amount
WITH base AS (
SELECT s.*,p.category_id FROM dws.return_day s JOIN dim.product p USING(product_id) WHERE s.return_date >= %(start)s AND s.return_date < %(end)s AND (%(channel)s::text IS NULL OR s.channel=%(channel)s::text) AND (%(category)s::integer IS NULL OR p.category_id=%(category)s::integer)
)
SELECT {bucket} AS bucket, coalesce(sum(return_record_amount),0) AS value FROM base {group_by};

-- metric: item_return_rate
WITH base AS (
SELECT s.*,p.category_id FROM dws.item_return_cohort_day s JOIN dim.product p USING(product_id) WHERE s.order_date >= %(start)s AND s.order_date < %(end)s AND (%(channel)s::text IS NULL OR s.channel=%(channel)s::text) AND (%(category)s::integer IS NULL OR p.category_id=%(category)s::integer) AND s.as_of=%(as_of)s
)
SELECT {bucket} AS bucket, round((coalesce(sum(returned_item_count),0))::numeric/nullif((coalesce(sum(cohort_item_count),0)),0),8) AS value, coalesce(sum(returned_item_count),0) AS numerator, coalesce(sum(cohort_item_count),0) AS denominator FROM base {group_by};

-- metric: order_item_count
WITH base AS (
SELECT s.*,p.category_id FROM dws.product_sales_day s JOIN dim.product p USING(product_id) WHERE s.order_date >= %(start)s AND s.order_date < %(end)s AND (%(channel)s::text IS NULL OR s.channel=%(channel)s::text) AND (%(category)s::integer IS NULL OR p.category_id=%(category)s::integer)
)
SELECT {bucket} AS bucket, coalesce(sum(order_item_count),0) AS value FROM base {group_by};

-- metric: ordered_unit_count
WITH base AS (
SELECT s.*,p.category_id FROM dws.product_sales_day s JOIN dim.product p USING(product_id) WHERE s.order_date >= %(start)s AND s.order_date < %(end)s AND (%(channel)s::text IS NULL OR s.channel=%(channel)s::text) AND (%(category)s::integer IS NULL OR p.category_id=%(category)s::integer)
)
SELECT {bucket} AS bucket, coalesce(sum(ordered_unit_count),0) AS value FROM base {group_by};

-- metric: item_gross_sales_amount
WITH base AS (
SELECT s.*,p.category_id FROM dws.product_sales_day s JOIN dim.product p USING(product_id) WHERE s.order_date >= %(start)s AND s.order_date < %(end)s AND (%(channel)s::text IS NULL OR s.channel=%(channel)s::text) AND (%(category)s::integer IS NULL OR p.category_id=%(category)s::integer)
)
SELECT {bucket} AS bucket, coalesce(sum(item_gross_amount),0) AS value FROM base {group_by};

-- metric: item_discount_amount
WITH base AS (
SELECT s.*,p.category_id FROM dws.product_sales_day s JOIN dim.product p USING(product_id) WHERE s.order_date >= %(start)s AND s.order_date < %(end)s AND (%(channel)s::text IS NULL OR s.channel=%(channel)s::text) AND (%(category)s::integer IS NULL OR p.category_id=%(category)s::integer)
)
SELECT {bucket} AS bucket, coalesce(sum(item_discount_amount),0) AS value FROM base {group_by};

-- metric: ordered_product_count
WITH base AS (
SELECT s.*,p.category_id FROM dws.product_sales_day s JOIN dim.product p USING(product_id) WHERE s.order_date >= %(start)s AND s.order_date < %(end)s AND (%(channel)s::text IS NULL OR s.channel=%(channel)s::text) AND (%(category)s::integer IS NULL OR p.category_id=%(category)s::integer)
)
SELECT {bucket} AS bucket, count(DISTINCT product_id) AS value FROM base {group_by};

-- metric: cancelled_order_count
WITH base AS (
SELECT s.* FROM dws.order_day s WHERE s.order_date >= %(start)s AND s.order_date < %(end)s AND (%(channel)s::text IS NULL OR s.channel=%(channel)s::text)
)
SELECT {bucket} AS bucket, coalesce(sum(order_count) FILTER(WHERE status='cancelled'),0) AS value FROM base WHERE status='cancelled' {group_by};

-- metric: cancelled_order_rate
WITH base AS (
SELECT s.* FROM dws.order_day s WHERE s.order_date >= %(start)s AND s.order_date < %(end)s AND (%(channel)s::text IS NULL OR s.channel=%(channel)s::text)
)
SELECT {bucket} AS bucket, round((coalesce(sum(order_count) FILTER(WHERE status='cancelled'),0))::numeric/nullif((coalesce(sum(order_count),0)),0),8) AS value, coalesce(sum(order_count) FILTER(WHERE status='cancelled'),0) AS numerator, coalesce(sum(order_count),0) AS denominator FROM base {group_by};

-- metric: average_order_record_amount
WITH base AS (
SELECT s.* FROM dws.order_day s WHERE s.order_date >= %(start)s AND s.order_date < %(end)s AND (%(channel)s::text IS NULL OR s.channel=%(channel)s::text)
)
SELECT {bucket} AS bucket, round((coalesce(sum(item_net_sales_amount),0))::numeric/nullif((coalesce(sum(order_count),0)),0),8) AS value, coalesce(sum(item_net_sales_amount),0) AS numerator, coalesce(sum(order_count),0) AS denominator FROM base {group_by};

-- metric: payment_failed_attempt_count
WITH base AS (
SELECT s.* FROM dws.payment_day s WHERE s.payment_date >= %(start)s AND s.payment_date < %(end)s AND (%(channel)s::text IS NULL OR s.channel=%(channel)s::text)
)
SELECT {bucket} AS bucket, coalesce(sum(payment_attempt_count) FILTER(WHERE status='failed'),0) AS value FROM base WHERE status='failed' {group_by};

-- metric: payment_failed_attempt_rate
WITH base AS (
SELECT s.* FROM dws.payment_day s WHERE s.payment_date >= %(start)s AND s.payment_date < %(end)s AND (%(channel)s::text IS NULL OR s.channel=%(channel)s::text)
)
SELECT {bucket} AS bucket, round((coalesce(sum(payment_attempt_count) FILTER(WHERE status='failed'),0))::numeric/nullif((coalesce(sum(payment_attempt_count),0)),0),8) AS value, coalesce(sum(payment_attempt_count) FILTER(WHERE status='failed'),0) AS numerator, coalesce(sum(payment_attempt_count),0) AS denominator FROM base {group_by};

-- metric: return_before_order_count
WITH base AS (
SELECT s.*,p.category_id FROM dws.return_day s JOIN dim.product p USING(product_id) WHERE s.return_date >= %(start)s AND s.return_date < %(end)s AND (%(channel)s::text IS NULL OR s.channel=%(channel)s::text) AND (%(category)s::integer IS NULL OR p.category_id=%(category)s::integer)
)
SELECT {bucket} AS bucket, coalesce(sum(dq_return_before_order_count),0) AS value FROM base {group_by};

-- metric: return_above_item_net_count
WITH base AS (
SELECT s.*,p.category_id FROM dws.return_day s JOIN dim.product p USING(product_id) WHERE s.return_date >= %(start)s AND s.return_date < %(end)s AND (%(channel)s::text IS NULL OR s.channel=%(channel)s::text) AND (%(category)s::integer IS NULL OR p.category_id=%(category)s::integer)
)
SELECT {bucket} AS bucket, coalesce(sum(dq_refund_above_item_net_count),0) AS value FROM base {group_by};

-- metric: ordering_customer_count
WITH base AS (
SELECT s.*,c.country,c.loyalty_tier FROM dws.customer_order_day s JOIN dim.customer c USING(customer_id) WHERE s.order_date >= %(start)s AND s.order_date < %(end)s AND (%(channel)s::text IS NULL OR s.channel=%(channel)s::text)
), per_account AS (
SELECT {bucket} AS bucket,customer_id,sum(order_count) AS n FROM base GROUP BY 1,customer_id
)
SELECT {output_bucket} AS bucket,count(*) AS value FROM per_account {group_by};

-- metric: repeat_ordering_customer_count
WITH base AS (
SELECT s.*,c.country,c.loyalty_tier FROM dws.customer_order_day s JOIN dim.customer c USING(customer_id) WHERE s.order_date >= %(start)s AND s.order_date < %(end)s AND (%(channel)s::text IS NULL OR s.channel=%(channel)s::text)
), per_account AS (
SELECT {bucket} AS bucket,customer_id,sum(order_count) AS n FROM base GROUP BY 1,customer_id
)
SELECT {output_bucket} AS bucket,count(*) FILTER(WHERE n>=2) AS value FROM per_account {group_by};

-- metric: customer_repeat_order_rate
WITH base AS (
SELECT s.*,c.country,c.loyalty_tier FROM dws.customer_order_day s JOIN dim.customer c USING(customer_id) WHERE s.order_date >= %(start)s AND s.order_date < %(end)s AND (%(channel)s::text IS NULL OR s.channel=%(channel)s::text)
), per_account AS (
SELECT {bucket} AS bucket,customer_id,sum(order_count) AS n FROM base GROUP BY 1,customer_id
)
SELECT {output_bucket} AS bucket,round((count(*) FILTER(WHERE n>=2))::numeric/nullif((count(*)),0),8) AS value, count(*) FILTER(WHERE n>=2) AS numerator, count(*) AS denominator FROM per_account {group_by};

-- metric: orders_per_ordering_customer
WITH base AS (
SELECT s.*,c.country,c.loyalty_tier FROM dws.customer_order_day s JOIN dim.customer c USING(customer_id) WHERE s.order_date >= %(start)s AND s.order_date < %(end)s AND (%(channel)s::text IS NULL OR s.channel=%(channel)s::text)
), per_account AS (
SELECT {bucket} AS bucket,customer_id,sum(order_count) AS n FROM base GROUP BY 1,customer_id
)
SELECT {output_bucket} AS bucket,round((coalesce(sum(n),0))::numeric/nullif((count(*)),0),8) AS value, coalesce(sum(n),0) AS numerator, count(*) AS denominator FROM per_account {group_by};

-- metric: channel_order_share
WITH base AS (
SELECT s.* FROM dws.order_day s WHERE s.order_date >= %(start)s AND s.order_date < %(end)s
), grouped AS (SELECT channel AS axis,coalesce(sum(order_count),0) AS n FROM base GROUP BY 1), shares AS (
SELECT axis,n,sum(n) OVER() AS denominator FROM grouped
)
SELECT axis::text AS bucket,round((n)::numeric/nullif((denominator),0),8) AS value,n AS numerator,denominator
FROM shares WHERE (%(channel)s::text IS NULL OR axis=%(channel)s::text);

-- metric: category_sales_share
WITH base AS (
SELECT s.*,p.category_id FROM dws.product_sales_day s JOIN dim.product p USING(product_id) WHERE s.order_date >= %(start)s AND s.order_date < %(end)s AND (%(channel)s::text IS NULL OR s.channel=%(channel)s::text)
), grouped AS (SELECT category_id AS axis,coalesce(sum(item_net_sales_amount),0) AS n FROM base GROUP BY 1), shares AS (
SELECT axis,n,sum(n) OVER() AS denominator FROM grouped
)
SELECT axis::text AS bucket,round((n)::numeric/nullif((denominator),0),8) AS value,n AS numerator,denominator
FROM shares WHERE (%(category)s::integer IS NULL OR axis=%(category)s::integer);
