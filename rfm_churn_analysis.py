"""
Customer Retention, RFM Segmentation & Win-Back Campaign ROI Pipeline
Dataset: Brazilian E-Commerce Public Dataset by Olist
Tools: SQLite, pandas, numpy
"""

import sqlite3
from pathlib import Path
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# 1. Configuration & Planning Assumptions
# ---------------------------------------------------------------------------
DB_PATH = Path("olist_ecommerce.db")
DISCOUNT = 0.15
GROSS_MARGIN = 0.30
OUTREACH_COST = 0.50

OFFER_CONVERSION = {
    "At-Risk High-Ticket (1x)": 0.04,
    "Hibernating / Low-Value (1x)": 0.015,
    "At-Risk Repeat Buyers": 0.05,
}

INACTIVE_SEGMENTS = list(OFFER_CONVERSION.keys())


def build_sqlite_db(conn: sqlite3.Connection) -> None:
    """Load raw Olist CSVs into SQLite tables."""
    tables = {
        "customers": "olist_customers_dataset.csv",
        "orders": "olist_orders_dataset.csv",
        "payments": "olist_order_payments_dataset.csv",
    }
    for table_name, csv_file in tables.items():
        df = pd.read_csv(csv_file)
        df.to_sql(table_name, conn, if_exists="replace", index=False)


def run_sql_file(conn: sqlite3.Connection, sql_path: str) -> pd.DataFrame:
    """Execute a standalone .sql file and return results as a DataFrame."""
    query = Path(sql_path).read_text(encoding="utf-8")
    return pd.read_sql_query(query, conn)


def build_segment_summary(rfm_df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate customer-level RFM output into the 7-segment summary."""
    summary = (
        rfm_df.groupby("segment", as_index=False)
        .agg(
            customers=("customer_unique_id", "count"),
            total_spend=("monetary", "sum"),
            orders=("frequency", "sum"),
            avg_recency_days=("recency_days", "mean"),
        )
    )
    total_customers = summary["customers"].sum()
    total_revenue = summary["total_spend"].sum()

    summary["pct_customers"] = 100.0 * summary["customers"] / total_customers
    summary["pct_revenue"] = 100.0 * summary["total_spend"] / total_revenue
    summary["spend_per_customer"] = summary["total_spend"] / summary["customers"]
    summary["aov"] = summary["total_spend"] / summary["orders"]
    summary["inactive"] = summary["segment"].isin(INACTIVE_SEGMENTS)
    return summary.sort_values("total_spend", ascending=False).reset_index(drop=True)


def evaluate_campaign(
    seg_summary: pd.DataFrame,
    backtest_df: pd.DataFrame,
    discount: float = DISCOUNT,
    gross_margin: float = GROSS_MARGIN,
    outreach_cost: float = OUTREACH_COST,
) -> pd.DataFrame:
    """Compute segment-level win-back campaign economics and break-even thresholds."""
    df = seg_summary[seg_summary["inactive"]].copy()
    baseline_map = backtest_df.set_index("segment")["baseline_30d_rate"].to_dict()

    df["baseline_conv"] = df["segment"].map(baseline_map)
    df["offer_conv"] = df["segment"].map(OFFER_CONVERSION)
    df["extra_orders"] = df["customers"] * (df["offer_conv"] - df["baseline_conv"])
    df["outreach_cost"] = df["customers"] * outreach_cost
    df["discount_cost"] = df["customers"] * df["offer_conv"] * df["aov"] * discount
    df["campaign_cost"] = df["outreach_cost"] + df["discount_cost"]
    df["incremental_revenue"] = df["customers"] * (
        df["offer_conv"] * df["aov"] * (1.0 - discount)
        - df["baseline_conv"] * df["aov"]
    )
    df["incremental_gross_profit"] = df["extra_orders"] * df["aov"] * gross_margin
    df["net_incremental_profit"] = (
        df["incremental_gross_profit"] - df["outreach_cost"] - df["discount_cost"]
    )
    df["net_per_customer"] = df["net_incremental_profit"] / df["customers"]
    df["roi_pct"] = 100.0 * df["net_incremental_profit"] / df["campaign_cost"]

    margin_after_disc = gross_margin - discount
    df["breakeven_conv"] = (
        (outreach_cost + df["baseline_conv"] * df["aov"] * gross_margin)
        / (df["aov"] * margin_after_disc)
        if margin_after_disc > 0
        else np.nan
    )
    df["target"] = df["net_incremental_profit"] > 0
    df["implied_lift_x"] = df["offer_conv"] / df["baseline_conv"]
    df["baseline_conv_pct"] = 100.0 * df["baseline_conv"]
    df["offer_conv_pct"] = 100.0 * df["offer_conv"]
    df["breakeven_conv_pct"] = 100.0 * df["breakeven_conv"]
    return df


def build_sensitivity_grid(
    seg_summary: pd.DataFrame, backtest_df: pd.DataFrame
) -> pd.DataFrame:
    """Re-run campaign economics across a 5x4 Gross Margin x Discount grid."""
    rows = []
    for gm in [0.15, 0.20, 0.25, 0.30, 0.35]:
        for disc in [0.05, 0.10, 0.15, 0.20]:
            econ = evaluate_campaign(seg_summary, backtest_df, discount=disc, gross_margin=gm)
            blanket_profit = econ["net_incremental_profit"].sum()
            targeted = econ[econ["target"]]
            targeted_profit = targeted["net_incremental_profit"].sum() if len(targeted) else 0.0
            targeted_segs = (
                ", ".join(targeted["segment"].tolist())
                if len(targeted)
                else "none - do not run"
            )
            rows.append(
                {
                    "gross_margin": gm,
                    "discount": disc,
                    "blanket_net_profit": blanket_profit,
                    "targeted_net_profit": targeted_profit,
                    "targeted_segments": targeted_segs,
                }
            )
    return pd.DataFrame(rows)


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        build_sqlite_db(conn)
        rfm_df = run_sql_file(conn, "rfm_segmentation.sql")
        cohort_df = run_sql_file(conn, "cohort_retention.sql")
    finally:
        conn.close()

    rfm_df.to_csv("rfm_customers.csv", index=False)
    cohort_df.to_csv("cohort_retention.csv", index=False)

    seg_summary = build_segment_summary(rfm_df)
    seg_summary.to_csv("segment_summary.csv", index=False)

    # Load 14-snapshot backtest baselines and run campaign economics
    backtest_df = pd.read_csv("backtest_baseline.csv")
    econ_df = evaluate_campaign(seg_summary, backtest_df)
    econ_df.to_csv("campaign_economics.csv", index=False)

    # Blanket vs. Targeted roll-up
    targeted_df = econ_df[econ_df["target"]]
    comparison_df = pd.DataFrame(
        [
            {
                "scenario": "Blanket 15% discount to all inactive customers",
                "segments": ", ".join(econ_df["segment"].tolist()),
                "customers_contacted": int(econ_df["customers"].sum()),
                "outreach_cost": econ_df["outreach_cost"].sum(),
                "discount_cost": econ_df["discount_cost"].sum(),
                "incremental_revenue": econ_df["incremental_revenue"].sum(),
                "net_incremental_profit": econ_df["net_incremental_profit"].sum(),
                "roi_pct": 100.0
                * econ_df["net_incremental_profit"].sum()
                / econ_df["campaign_cost"].sum(),
            },
            {
                "scenario": "Targeted offer only where it pays back",
                "segments": ", ".join(targeted_df["segment"].tolist()),
                "customers_contacted": int(targeted_df["customers"].sum()),
                "outreach_cost": targeted_df["outreach_cost"].sum(),
                "discount_cost": targeted_df["discount_cost"].sum(),
                "incremental_revenue": targeted_df["incremental_revenue"].sum(),
                "net_incremental_profit": targeted_df["net_incremental_profit"].sum(),
                "roi_pct": 100.0
                * targeted_df["net_incremental_profit"].sum()
                / targeted_df["campaign_cost"].sum(),
            },
        ]
    )
    comparison_df.to_csv("campaign_comparison.csv", index=False)

    # 5x4 Gross Margin x Discount sensitivity grid
    grid_df = build_sensitivity_grid(seg_summary, backtest_df)
    grid_df.to_csv("campaign_sensitivity_grid.csv", index=False)
    print("Pipeline complete. All analytical CSVs generated.")


if __name__ == "__main__":
    main()
