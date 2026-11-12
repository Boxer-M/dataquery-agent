WITH asset_grouped AS (
  SELECT
    o.channel,
    f.order_id,
    COUNT(*) AS attempt_count
  FROM dwd.payment_attempt_fact AS f
  JOIN dwd.order_fact AS o
    ON f.order_id = o.order_id
  WHERE
    f.paid_at >= %(start)s AND f.paid_at < %(end)s
  GROUP BY
    o.channel,
    f.order_id
), ranked AS (
  SELECT
    g.channel,
    g.order_id,
    g.attempt_count,
    ROW_NUMBER() OVER (PARTITION BY g.channel ORDER BY g.attempt_count DESC, g.order_id) AS rank
  FROM asset_grouped AS g
)
SELECT
  r.channel,
  r.order_id,
  r.attempt_count,
  r.rank
FROM ranked AS r
WHERE
  r.rank <= %(top_n)s
ORDER BY
  r.channel,
  r.rank
LIMIT %(row_limit)s
