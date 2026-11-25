-- ============================================================================
-- Monthly Cohort Retention Analysis (SQLite)
-- Tracks the share of each acquisition cohort (month of first delivered order)
-- that places a delivered order in months 0 through 12 after that month.
--
-- A month spine guarantees one row for every observable cohort/month cell, so a
-- month in which nobody came back is reported as 0%, not silently dropped.
-- Cells that fall after the last month in the data are omitted (right-censored)
-- rather than reported as 0%.
-- ============================================================================

WITH RECURSIVE
delivered_orders AS (
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
data_end AS (
    SELECT MAX(month_num) AS last_month_num
    FROM delivered_orders
),
month_spine AS (
    SELECT 0 AS month_index
    UNION ALL
    SELECT month_index + 1
    FROM month_spine
    WHERE month_index < 12
),
cohort_sizes AS (
    SELECT
        cohort_month,
        cohort_month_num,
        COUNT(*) AS cohort_size
    FROM first_purchase
    GROUP BY cohort_month, cohort_month_num
),
cohort_grid AS (
    SELECT
        s.cohort_month,
        s.cohort_size,
        m.month_index
    FROM cohort_sizes s
    CROSS JOIN month_spine m
    CROSS JOIN data_end e
    WHERE s.cohort_month_num + m.month_index <= e.last_month_num
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
)
SELECT
    g.cohort_month,
    g.month_index,
    g.cohort_size,
    COALESCE(a.active_customers, 0) AS active_customers,
    ROUND(100.0 * COALESCE(a.active_customers, 0) / g.cohort_size, 2) AS retention_pct
FROM cohort_grid g
LEFT JOIN cohort_activity a
    ON a.cohort_month = g.cohort_month
   AND a.month_index = g.month_index
ORDER BY g.cohort_month, g.month_index;
