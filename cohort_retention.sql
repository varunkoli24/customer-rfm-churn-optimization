-- ============================================================================
-- Monthly Cohort Retention Analysis (SQLite)
-- Tracks the share of each acquisition cohort that places a subsequent
-- delivered order in months 0 through 12 after their first purchase.
-- ============================================================================

WITH delivered_orders AS (
    SELECT
        c.customer_unique_id,
        strftime('%Y-%m', o.order_purchase_timestamp) AS order_month,
        CAST(strftime('%Y', o.order_purchase_timestamp) AS INTEGER) * 12
            + CAST(strftime('%m', o.order_purchase_timestamp) AS INTEGER) AS month_num
    FROM orders o
    INNER JOIN customers c
        ON o.customer_id = c.customer_id
    WHERE o.order_status = 'delivered'
),
first_purchase AS (
    SELECT
        customer_unique_id,
        MIN(order_month) AS cohort_month,
        MIN(month_num) AS cohort_month_num
    FROM delivered_orders
    GROUP BY customer_unique_id
),
cohort_activity AS (
    SELECT
        f.cohort_month,
        (d.month_num - f.cohort_month_num) AS month_index,
        COUNT(DISTINCT d.customer_unique_id) AS active_customers
    FROM delivered_orders d
    INNER JOIN first_purchase f
        ON d.customer_unique_id = f.customer_unique_id
    WHERE (d.month_num - f.cohort_month_num) BETWEEN 0 AND 12
    GROUP BY f.cohort_month, (d.month_num - f.cohort_month_num)
),
cohort_sizes AS (
    SELECT
        cohort_month,
        COUNT(DISTINCT customer_unique_id) AS cohort_size
    FROM first_purchase
    GROUP BY cohort_month
)
SELECT
    a.cohort_month,
    a.month_index,
    s.cohort_size,
    a.active_customers,
    ROUND(100.0 * a.active_customers / s.cohort_size, 4) AS retention_pct
FROM cohort_activity a
INNER JOIN cohort_sizes s
    ON a.cohort_month = s.cohort_month
ORDER BY a.cohort_month, a.month_index;
