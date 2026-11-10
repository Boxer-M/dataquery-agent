-- metric: order_count
SELECT count(*) AS value FROM ods.orders
WHERE ordered_at >= %(start)s AND ordered_at < %(end)s;

-- metric: item_net_sales_amount
SELECT coalesce(sum(i.quantity * i.unit_price - i.discount_amount), 0) AS value
FROM ods.order_items i JOIN ods.orders o USING (order_id)
WHERE o.ordered_at >= %(start)s AND o.ordered_at < %(end)s;

-- metric: payment_attempt_count
SELECT count(*) AS value FROM ods.payments
WHERE paid_at >= %(start)s AND paid_at < %(end)s;

-- metric: captured_payment_amount
SELECT coalesce(sum(amount), 0) AS value FROM ods.payments
WHERE paid_at >= %(start)s AND paid_at < %(end)s AND status = 'captured';

-- metric: payment_attempt_success_rate
SELECT round(count(*) FILTER (WHERE status = 'captured')::numeric
             / nullif(count(*), 0), 8) AS value,
       count(*) FILTER (WHERE status = 'captured') AS numerator, count(*) AS denominator
FROM ods.payments WHERE paid_at >= %(start)s AND paid_at < %(end)s;

-- metric: return_record_count
SELECT count(*) AS value FROM ods.returns
WHERE returned_at >= %(start)s AND returned_at < %(end)s;

-- metric: return_record_amount
SELECT coalesce(sum(refund_amount), 0) AS value FROM ods.returns
WHERE returned_at >= %(start)s AND returned_at < %(end)s;

-- metric: item_return_rate
-- Order-cohort observed record linkage, NOT return-month / order-month volume.
-- Keep source anomalies, including return timestamps earlier than the order.
WITH cohort AS (
SELECT i.order_item_id, EXISTS (
    SELECT 1 FROM ods.returns r
    WHERE r.order_item_id = i.order_item_id AND r.returned_at < %(as_of)s
) AS has_return
FROM ods.order_items i JOIN ods.orders o USING (order_id)
WHERE o.ordered_at >= %(start)s AND o.ordered_at < %(end)s
)
SELECT round(count(*) FILTER (WHERE has_return)::numeric / nullif(count(*), 0), 8) AS value,
       count(*) FILTER (WHERE has_return) AS numerator, count(*) AS denominator
FROM cohort;
