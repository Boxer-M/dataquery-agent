-- metric: order_count
WITH base AS (
SELECT o.* FROM ods.orders o WHERE o.ordered_at >= %(start)s AND o.ordered_at < %(end)s AND (%(channel)s::text IS NULL OR o.channel=%(channel)s::text)
)
SELECT {bucket} AS bucket, count(*) AS value FROM base {group_by};

-- metric: item_net_sales_amount
WITH base AS (
SELECT i.*,o.channel,p.category_id FROM ods.order_items i JOIN ods.orders o USING(order_id) JOIN ods.products p USING(product_id) WHERE o.ordered_at >= %(start)s AND o.ordered_at < %(end)s AND (%(channel)s::text IS NULL OR o.channel=%(channel)s::text) AND (%(category)s::integer IS NULL OR p.category_id=%(category)s::integer)
)
SELECT {bucket} AS bucket, coalesce(sum(quantity*unit_price-discount_amount),0) AS value FROM base {group_by};

-- metric: payment_attempt_count
WITH base AS (
SELECT p.*,o.channel FROM ods.payments p JOIN ods.orders o USING(order_id) WHERE p.paid_at >= %(start)s AND p.paid_at < %(end)s AND (%(channel)s::text IS NULL OR o.channel=%(channel)s::text)
)
SELECT {bucket} AS bucket, count(*) AS value FROM base {group_by};

-- metric: captured_payment_amount
WITH base AS (
SELECT p.*,o.channel FROM ods.payments p JOIN ods.orders o USING(order_id) WHERE p.paid_at >= %(start)s AND p.paid_at < %(end)s AND (%(channel)s::text IS NULL OR o.channel=%(channel)s::text)
)
SELECT {bucket} AS bucket, coalesce(sum(amount) FILTER(WHERE status='captured'),0) AS value FROM base WHERE status='captured' {group_by};

-- metric: payment_attempt_success_rate
WITH base AS (
SELECT p.*,o.channel FROM ods.payments p JOIN ods.orders o USING(order_id) WHERE p.paid_at >= %(start)s AND p.paid_at < %(end)s AND (%(channel)s::text IS NULL OR o.channel=%(channel)s::text)
)
SELECT {bucket} AS bucket, round((count(*) FILTER(WHERE status='captured'))::numeric/nullif((count(*)),0),8) AS value, count(*) FILTER(WHERE status='captured') AS numerator, count(*) AS denominator FROM base {group_by};

-- metric: return_record_count
WITH base AS (
SELECT r.*,o.channel,i.product_id,p.category_id,o.ordered_at,
      i.quantity*i.unit_price-i.discount_amount AS item_net
      FROM ods.returns r JOIN ods.order_items i USING(order_item_id)
      JOIN ods.orders o USING(order_id) JOIN ods.products p USING(product_id) WHERE r.returned_at >= %(start)s AND r.returned_at < %(end)s AND (%(channel)s::text IS NULL OR o.channel=%(channel)s::text) AND (%(category)s::integer IS NULL OR p.category_id=%(category)s::integer)
)
SELECT {bucket} AS bucket, count(*) AS value FROM base {group_by};

-- metric: return_record_amount
WITH base AS (
SELECT r.*,o.channel,i.product_id,p.category_id,o.ordered_at,
      i.quantity*i.unit_price-i.discount_amount AS item_net
      FROM ods.returns r JOIN ods.order_items i USING(order_item_id)
      JOIN ods.orders o USING(order_id) JOIN ods.products p USING(product_id) WHERE r.returned_at >= %(start)s AND r.returned_at < %(end)s AND (%(channel)s::text IS NULL OR o.channel=%(channel)s::text) AND (%(category)s::integer IS NULL OR p.category_id=%(category)s::integer)
)
SELECT {bucket} AS bucket, coalesce(sum(refund_amount),0) AS value FROM base {group_by};

-- metric: item_return_rate
WITH base AS (
SELECT i.order_item_id,o.channel,i.product_id,p.category_id,(r.order_item_id IS NOT NULL) AS has_return
      FROM ods.order_items i JOIN ods.orders o USING(order_id) JOIN ods.products p USING(product_id)
      LEFT JOIN (SELECT DISTINCT order_item_id FROM ods.returns WHERE returned_at<%(as_of)s) r USING(order_item_id)
      WHERE o.ordered_at >= %(start)s AND o.ordered_at < %(end)s AND (%(channel)s::text IS NULL OR o.channel=%(channel)s::text) AND (%(category)s::integer IS NULL OR p.category_id=%(category)s::integer)
)
SELECT {bucket} AS bucket, round((count(*) FILTER(WHERE has_return))::numeric/nullif((count(*)),0),8) AS value, count(*) FILTER(WHERE has_return) AS numerator, count(*) AS denominator FROM base {group_by};

-- metric: order_item_count
WITH base AS (
SELECT i.*,o.channel,p.category_id FROM ods.order_items i JOIN ods.orders o USING(order_id) JOIN ods.products p USING(product_id) WHERE o.ordered_at >= %(start)s AND o.ordered_at < %(end)s AND (%(channel)s::text IS NULL OR o.channel=%(channel)s::text) AND (%(category)s::integer IS NULL OR p.category_id=%(category)s::integer)
)
SELECT {bucket} AS bucket, count(*) AS value FROM base {group_by};

-- metric: ordered_unit_count
WITH base AS (
SELECT i.*,o.channel,p.category_id FROM ods.order_items i JOIN ods.orders o USING(order_id) JOIN ods.products p USING(product_id) WHERE o.ordered_at >= %(start)s AND o.ordered_at < %(end)s AND (%(channel)s::text IS NULL OR o.channel=%(channel)s::text) AND (%(category)s::integer IS NULL OR p.category_id=%(category)s::integer)
)
SELECT {bucket} AS bucket, coalesce(sum(quantity),0) AS value FROM base {group_by};

-- metric: item_gross_sales_amount
WITH base AS (
SELECT i.*,o.channel,p.category_id FROM ods.order_items i JOIN ods.orders o USING(order_id) JOIN ods.products p USING(product_id) WHERE o.ordered_at >= %(start)s AND o.ordered_at < %(end)s AND (%(channel)s::text IS NULL OR o.channel=%(channel)s::text) AND (%(category)s::integer IS NULL OR p.category_id=%(category)s::integer)
)
SELECT {bucket} AS bucket, coalesce(sum(quantity*unit_price),0) AS value FROM base {group_by};

-- metric: item_discount_amount
WITH base AS (
SELECT i.*,o.channel,p.category_id FROM ods.order_items i JOIN ods.orders o USING(order_id) JOIN ods.products p USING(product_id) WHERE o.ordered_at >= %(start)s AND o.ordered_at < %(end)s AND (%(channel)s::text IS NULL OR o.channel=%(channel)s::text) AND (%(category)s::integer IS NULL OR p.category_id=%(category)s::integer)
)
SELECT {bucket} AS bucket, coalesce(sum(discount_amount),0) AS value FROM base {group_by};

-- metric: ordered_product_count
WITH base AS (
SELECT i.*,o.channel,p.category_id FROM ods.order_items i JOIN ods.orders o USING(order_id) JOIN ods.products p USING(product_id) WHERE o.ordered_at >= %(start)s AND o.ordered_at < %(end)s AND (%(channel)s::text IS NULL OR o.channel=%(channel)s::text) AND (%(category)s::integer IS NULL OR p.category_id=%(category)s::integer)
)
SELECT {bucket} AS bucket, count(DISTINCT product_id) AS value FROM base {group_by};

-- metric: cancelled_order_count
WITH base AS (
SELECT o.* FROM ods.orders o WHERE o.ordered_at >= %(start)s AND o.ordered_at < %(end)s AND (%(channel)s::text IS NULL OR o.channel=%(channel)s::text)
)
SELECT {bucket} AS bucket, count(*) FILTER(WHERE status='cancelled') AS value FROM base WHERE status='cancelled' {group_by};

-- metric: cancelled_order_rate
WITH base AS (
SELECT o.* FROM ods.orders o WHERE o.ordered_at >= %(start)s AND o.ordered_at < %(end)s AND (%(channel)s::text IS NULL OR o.channel=%(channel)s::text)
)
SELECT {bucket} AS bucket, round((count(*) FILTER(WHERE status='cancelled'))::numeric/nullif((count(*)),0),8) AS value, count(*) FILTER(WHERE status='cancelled') AS numerator, count(*) AS denominator FROM base {group_by};

-- metric: average_order_record_amount
WITH base AS (
SELECT o.*,coalesce(i.net,0) AS net FROM ods.orders o LEFT JOIN (
       SELECT order_id,sum(quantity*unit_price-discount_amount) net FROM ods.order_items GROUP BY order_id
       ) i USING(order_id) WHERE o.ordered_at >= %(start)s AND o.ordered_at < %(end)s AND (%(channel)s::text IS NULL OR o.channel=%(channel)s::text)
)
SELECT {bucket} AS bucket, round((coalesce(sum(net),0))::numeric/nullif((count(*)),0),8) AS value, coalesce(sum(net),0) AS numerator, count(*) AS denominator FROM base {group_by};

-- metric: payment_failed_attempt_count
WITH base AS (
SELECT p.*,o.channel FROM ods.payments p JOIN ods.orders o USING(order_id) WHERE p.paid_at >= %(start)s AND p.paid_at < %(end)s AND (%(channel)s::text IS NULL OR o.channel=%(channel)s::text)
)
SELECT {bucket} AS bucket, count(*) FILTER(WHERE status='failed') AS value FROM base WHERE status='failed' {group_by};

-- metric: payment_failed_attempt_rate
WITH base AS (
SELECT p.*,o.channel FROM ods.payments p JOIN ods.orders o USING(order_id) WHERE p.paid_at >= %(start)s AND p.paid_at < %(end)s AND (%(channel)s::text IS NULL OR o.channel=%(channel)s::text)
)
SELECT {bucket} AS bucket, round((count(*) FILTER(WHERE status='failed'))::numeric/nullif((count(*)),0),8) AS value, count(*) FILTER(WHERE status='failed') AS numerator, count(*) AS denominator FROM base {group_by};

-- metric: return_before_order_count
WITH base AS (
SELECT r.*,o.channel,i.product_id,p.category_id,o.ordered_at,
      i.quantity*i.unit_price-i.discount_amount AS item_net
      FROM ods.returns r JOIN ods.order_items i USING(order_item_id)
      JOIN ods.orders o USING(order_id) JOIN ods.products p USING(product_id) WHERE r.returned_at >= %(start)s AND r.returned_at < %(end)s AND (%(channel)s::text IS NULL OR o.channel=%(channel)s::text) AND (%(category)s::integer IS NULL OR p.category_id=%(category)s::integer)
)
SELECT {bucket} AS bucket, count(*) FILTER(WHERE returned_at<ordered_at) AS value FROM base {group_by};

-- metric: return_above_item_net_count
WITH base AS (
SELECT r.*,o.channel,i.product_id,p.category_id,o.ordered_at,
      i.quantity*i.unit_price-i.discount_amount AS item_net
      FROM ods.returns r JOIN ods.order_items i USING(order_item_id)
      JOIN ods.orders o USING(order_id) JOIN ods.products p USING(product_id) WHERE r.returned_at >= %(start)s AND r.returned_at < %(end)s AND (%(channel)s::text IS NULL OR o.channel=%(channel)s::text) AND (%(category)s::integer IS NULL OR p.category_id=%(category)s::integer)
)
SELECT {bucket} AS bucket, count(*) FILTER(WHERE refund_amount>item_net) AS value FROM base {group_by};

-- metric: ordering_customer_count
WITH base AS (
SELECT o.*,c.country,c.loyalty_tier FROM ods.orders o JOIN ods.customers c USING(customer_id) WHERE o.ordered_at >= %(start)s AND o.ordered_at < %(end)s AND (%(channel)s::text IS NULL OR o.channel=%(channel)s::text)
), per_account AS (
SELECT {bucket} AS bucket,customer_id,count(*) AS n FROM base GROUP BY 1,customer_id
)
SELECT {output_bucket} AS bucket,count(*) AS value FROM per_account {group_by};

-- metric: repeat_ordering_customer_count
WITH base AS (
SELECT o.*,c.country,c.loyalty_tier FROM ods.orders o JOIN ods.customers c USING(customer_id) WHERE o.ordered_at >= %(start)s AND o.ordered_at < %(end)s AND (%(channel)s::text IS NULL OR o.channel=%(channel)s::text)
), per_account AS (
SELECT {bucket} AS bucket,customer_id,count(*) AS n FROM base GROUP BY 1,customer_id
)
SELECT {output_bucket} AS bucket,count(*) FILTER(WHERE n>=2) AS value FROM per_account {group_by};

-- metric: customer_repeat_order_rate
WITH base AS (
SELECT o.*,c.country,c.loyalty_tier FROM ods.orders o JOIN ods.customers c USING(customer_id) WHERE o.ordered_at >= %(start)s AND o.ordered_at < %(end)s AND (%(channel)s::text IS NULL OR o.channel=%(channel)s::text)
), per_account AS (
SELECT {bucket} AS bucket,customer_id,count(*) AS n FROM base GROUP BY 1,customer_id
)
SELECT {output_bucket} AS bucket,round((count(*) FILTER(WHERE n>=2))::numeric/nullif((count(*)),0),8) AS value, count(*) FILTER(WHERE n>=2) AS numerator, count(*) AS denominator FROM per_account {group_by};

-- metric: orders_per_ordering_customer
WITH base AS (
SELECT o.*,c.country,c.loyalty_tier FROM ods.orders o JOIN ods.customers c USING(customer_id) WHERE o.ordered_at >= %(start)s AND o.ordered_at < %(end)s AND (%(channel)s::text IS NULL OR o.channel=%(channel)s::text)
), per_account AS (
SELECT {bucket} AS bucket,customer_id,count(*) AS n FROM base GROUP BY 1,customer_id
)
SELECT {output_bucket} AS bucket,round((coalesce(sum(n),0))::numeric/nullif((count(*)),0),8) AS value, coalesce(sum(n),0) AS numerator, count(*) AS denominator FROM per_account {group_by};

-- metric: channel_order_share
WITH base AS (
SELECT o.* FROM ods.orders o WHERE o.ordered_at >= %(start)s AND o.ordered_at < %(end)s
), grouped AS (SELECT channel AS axis,count(*) AS n FROM base GROUP BY 1), shares AS (
SELECT axis,n,sum(n) OVER() AS denominator FROM grouped
)
SELECT axis::text AS bucket,round((n)::numeric/nullif((denominator),0),8) AS value,n AS numerator,denominator
FROM shares WHERE (%(channel)s::text IS NULL OR axis=%(channel)s::text);

-- metric: category_sales_share
WITH base AS (
SELECT i.*,o.channel,p.category_id FROM ods.order_items i JOIN ods.orders o USING(order_id) JOIN ods.products p USING(product_id) WHERE o.ordered_at >= %(start)s AND o.ordered_at < %(end)s AND (%(channel)s::text IS NULL OR o.channel=%(channel)s::text)
), grouped AS (SELECT category_id AS axis,coalesce(sum(quantity*unit_price-discount_amount),0) AS n FROM base GROUP BY 1), shares AS (
SELECT axis,n,sum(n) OVER() AS denominator FROM grouped
)
SELECT axis::text AS bucket,round((n)::numeric/nullif((denominator),0),8) AS value,n AS numerator,denominator
FROM shares WHERE (%(category)s::integer IS NULL OR axis=%(category)s::integer);
