-- check: business_overview_day
WITH records AS ({records})
SELECT count(*) AS groups,coalesce(sum(order_count),0) AS orders,
 coalesce(sum(item_net_sales_amount),0) AS item_net,
 coalesce(sum(payment_attempt_count),0) AS attempts,coalesce(sum(captured_attempt_count),0) AS captured,
 coalesce(sum(captured_payment_amount),0) AS paid,coalesce(sum(return_record_count),0) AS returns,
 coalesce(sum(return_record_amount),0) AS return_amount,
 coalesce(sum(dq_return_before_order_count),0) AS early_returns,
 coalesce(sum(dq_refund_above_item_net_count),0) AS excessive_returns
FROM records WHERE event_date>=%(start)s AND event_date<%(end)s
 AND (%(channel)s::text IS NULL OR channel=%(channel)s);

-- check: payment_anomaly_order
WITH records AS ({records})
SELECT count(*) AS orders,coalesce(sum(item_net_amount),0) AS item_net,
 coalesce(sum(payment_attempt_count),0) AS attempts,coalesce(sum(captured_attempt_count),0) AS captured,
 coalesce(sum(failed_attempt_count),0) AS failed,coalesce(sum(captured_payment_amount),0) AS paid,
 count(*) FILTER(WHERE has_multiple_attempts) AS multiple_attempt_orders,
 count(*) FILTER(WHERE has_amount_mismatch) AS mismatch_orders,
 round(sum(failed_attempt_count)::numeric/nullif(sum(payment_attempt_count),0),8) AS failed_rate,
 count(*) FILTER(WHERE payment_attempt_count=0 AND first_payment_at IS NULL AND last_payment_at IS NULL AND payment_failed_attempt_rate IS NULL) AS no_attempt_orders
FROM records WHERE ordered_at>=%(start)s AND ordered_at<%(end)s
 AND (%(channel)s::text IS NULL OR channel=%(channel)s);

-- check: product_return_cohort
WITH records AS ({records})
SELECT count(*) AS groups,coalesce(sum(cohort_item_count),0) AS denominator,
 coalesce(sum(returned_item_count),0) AS numerator,coalesce(sum(anomalous_return_item_count),0) AS anomalous,
 round(sum(returned_item_count)::numeric/nullif(sum(cohort_item_count),0),8) AS item_return_rate
FROM records WHERE order_date>=%(start)s AND order_date<%(end)s AND as_of=%(as_of)s
 AND (%(channel)s::text IS NULL OR channel=%(channel)s);
