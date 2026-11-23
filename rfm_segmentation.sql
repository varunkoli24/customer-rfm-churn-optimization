-- ============================================================================
-- RFM Segmentation & Customer-Level Aggregation (SQLite)
-- Dataset: Brazilian E-Commerce Public Dataset by Olist
-- Note: Pre-aggregates payments by order_id in a CTE to prevent 1-to-many
--       fan-out when orders have multiple payment installments/methods.
-- ============================================================================

WITH order_pay AS (
    SELECT
        order_id,
        SUM(payment_value) AS order_value
    FROM payments
    GROUP BY order_id
),
delivered_orders AS (
    SELECT
        c.customer_unique_id,
        o.order_id,
        o.order_purchase_timestamp,
        COALESCE(p.order_value, 0.0) AS order_value
    FROM orders o
    INNER JOIN customers c
        ON o.customer_id = c.customer_id
    LEFT JOIN order_pay p
        ON o.order_id = p.order_id
    WHERE o.order_status = 'delivered'
),
ref_date AS (
    SELECT MAX(order_purchase_timestamp) AS max_ts
    FROM delivered_orders
),
rfm_base AS (
    SELECT
        d.customer_unique_id,
        CAST(julianday(r.max_ts) - julianday(MAX(d.order_purchase_timestamp)) AS REAL) AS recency_days,
        COUNT(DISTINCT d.order_id) AS frequency,
        SUM(d.order_value) AS monetary
    FROM delivered_orders d
    CROSS JOIN ref_date r
    GROUP BY d.customer_unique_id
),
rfm_scored AS (
    SELECT
        customer_unique_id,
        recency_days,
        frequency,
        monetary,
        NTILE(5) OVER (ORDER BY recency_days DESC) AS r_score,
        CASE
            WHEN frequency >= 3 THEN 5
            WHEN frequency = 2 THEN 4
            ELSE 1
        END AS f_score,
        NTILE(5) OVER (ORDER BY monetary ASC) AS m_score
    FROM rfm_base
)
SELECT
    customer_unique_id,
    ROUND(recency_days, 2) AS recency_days,
    frequency,
    ROUND(monetary, 2) AS monetary,
    r_score,
    f_score,
    m_score,
    CASE
        WHEN frequency >= 2 AND r_score >= 4 AND m_score >= 4 THEN 'Champions (Repeat High-Value)'
        WHEN frequency >= 2 AND r_score >= 3 THEN 'Loyal Repeat Buyers'
        WHEN frequency >= 2 THEN 'At-Risk Repeat Buyers'
        WHEN r_score >= 3 AND m_score >= 4 THEN 'Recent High-Ticket (1x)'
        WHEN r_score >= 3 THEN 'Recent Standard Buyers (1x)'
        WHEN m_score >= 4 THEN 'At-Risk High-Ticket (1x)'
        ELSE 'Hibernating / Low-Value (1x)'
    END AS segment
FROM rfm_scored;
