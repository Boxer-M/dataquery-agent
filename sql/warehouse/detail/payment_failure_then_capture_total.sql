WITH qualified AS (
    SELECT DISTINCT f.order_id
    FROM dwd.payment_attempt_fact f
    WHERE f.status = 'failed'
      AND f.paid_at >= %(start)s AND f.paid_at < %(end)s
      AND EXISTS (
          SELECT 1 FROM dwd.payment_attempt_fact c
          WHERE c.order_id = f.order_id AND c.status = 'captured'
            AND c.paid_at > f.paid_at AND c.paid_at < %(end)s
      )
)
SELECT COUNT(*) AS order_count FROM qualified
