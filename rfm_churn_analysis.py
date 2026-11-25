"""
Customer Retention, RFM Segmentation & Win-Back Campaign ROI Pipeline
Dataset: Brazilian E-Commerce Public Dataset by Olist
Tools: SQLite, pandas, numpy

Inputs
    olist_customers_dataset.csv, olist_orders_dataset.csv,
    olist_order_payments_dataset.csv   (download from Kaggle, see README)
    backtest_baseline.csv              (committed; precomputed 14-snapshot
                                        organic 30-day repurchase rates)

Outputs (regenerated on every run)
    rfm_customers.csv (git-ignored), segment_summary.csv, cohort_retention.csv,
    campaign_economics.csv, campaign_comparison.csv, campaign_sensitivity.csv,
    campaign_sensitivity_grid.csv, inactive_segment_spend.csv
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

# Conversion stress test: actual offer conversion as a multiple of the assumption
CONVERSION_MULTIPLIERS = (0.50, 0.75, 1.00, 1.25, 1.50)


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
    conversion_multiplier: float = 1.0,
) -> pd.DataFrame:
    """Compute segment-level win-back campaign economics and break-even thresholds.

    conversion_multiplier scales the assumed offer conversion (1.0 = planning
    assumption) and is used by the stress test.
    """
    df = seg_summary[seg_summary["inactive"]].copy()
    baseline_map = backtest_df.set_index("segment")["baseline_30d_rate"].to_dict()

    df["baseline_conv"] = df["segment"].map(baseline_map)
    df["offer_conv"] = df["segment"].map(OFFER_CONVERSION) * conversion_multiplier
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


def build_campaign_comparison(econ_df: pd.DataFrame) -> pd.DataFrame:
    """Blanket (all inactive segments) vs. targeted (segments that pay back) roll-up."""
    targeted_df = econ_df[econ_df["target"]]
    return pd.DataFrame(
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


def build_conversion_stress_test(
    seg_summary: pd.DataFrame,
    backtest_df: pd.DataFrame,
    multipliers: tuple = CONVERSION_MULTIPLIERS,
) -> pd.DataFrame:
    """Net profit if real offer conversion lands at a multiple of the assumption.

    The targeted list is fixed at the segments that pay back under the planning
    assumptions (a team picks its target list before it sees real conversion),
    so a targeted segment can turn negative in a stress case.
    """
    base = evaluate_campaign(seg_summary, backtest_df)
    targeted_segments = base.loc[base["target"], "segment"]
    rows = []
    for mult in multipliers:
        econ = evaluate_campaign(seg_summary, backtest_df, conversion_multiplier=mult)
        rows.append(
            {
                "actual_conversion_vs_assumed": f"{mult:.2f}x",
                "blanket_net_profit": econ["net_incremental_profit"].sum(),
                "targeted_net_profit": econ.loc[
                    econ["segment"].isin(targeted_segments), "net_incremental_profit"
                ].sum(),
            }
        )
    return pd.DataFrame(rows)


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


def build_inactive_segment_spend(
    seg_summary: pd.DataFrame, backtest_df: pd.DataFrame
) -> pd.DataFrame:
    """Historical spend of each inactive segment vs. its expected organic 30-day revenue."""
    df = seg_summary[seg_summary["inactive"]].copy()
    rate = df["segment"].map(backtest_df.set_index("segment")["baseline_30d_rate"])
    out = pd.DataFrame(
        {
            "segment": df["segment"],
            "customers": df["customers"],
            "pct_customers": df["pct_customers"],
            "historical_spend": df["total_spend"],
            "pct_of_historical_revenue": df["pct_revenue"],
            "historical_spend_per_customer": df["spend_per_customer"],
            "aov": df["aov"],
            "avg_recency_days": df["avg_recency_days"],
            "organic_repurchase_30d_pct": 100.0 * rate,
            "expected_organic_revenue_30d": df["customers"] * rate * df["aov"],
        }
    )
    return out.reset_index(drop=True)


def main() -> None:
    if not Path("backtest_baseline.csv").exists():
        raise FileNotFoundError(
            "backtest_baseline.csv is a committed input (14-snapshot organic 30-day "
            "repurchase baselines). Download it from the repository."
        )

    conn = sqlite3.connect(DB_PATH)
    try:
        build_sqlite_db(conn)
        rfm_df = run_sql_file(conn, "rfm_segmentation.sql")
        cohort_df = run_sql_file(conn, "cohort_retention.sql")
    finally:
        conn.close()

    rfm_df.to_csv("rfm_customers.csv", index=False)
    cohort_df.to_csv("cohort_retention.csv", index=False)

    # "Inactive" = bottom two recency quintiles (r_score <= 2). It is relative to the
    # customer base, so the 40% share is a property of the definition. Guard that the
    # segment labels and the quintile rule agree, and report the equivalent day cutoff.
    inactive_rfm = rfm_df[rfm_df["r_score"] <= 2]
    assert (rfm_df["segment"].isin(INACTIVE_SEGMENTS) == (rfm_df["r_score"] <= 2)).all(), (
        "Inactive segments no longer match r_score <= 2; update INACTIVE_SEGMENTS."
    )
    print(
        f"'Inactive' = bottom two recency quintiles: {len(inactive_rfm):,} customers "
        f"({100.0 * len(inactive_rfm) / len(rfm_df):.2f}% of {len(rfm_df):,}); "
        f"last order at least {inactive_rfm['recency_days'].min():.0f} days before the "
        f"reference date."
    )

    seg_summary = build_segment_summary(rfm_df)
    seg_summary.to_csv("segment_summary.csv", index=False)

    # Load 14-snapshot backtest baselines (precomputed) and run campaign economics
    backtest_df = pd.read_csv("backtest_baseline.csv")
    econ_df = evaluate_campaign(seg_summary, backtest_df)
    econ_df.to_csv("campaign_economics.csv", index=False)

    build_campaign_comparison(econ_df).to_csv("campaign_comparison.csv", index=False)
    build_conversion_stress_test(seg_summary, backtest_df).to_csv(
        "campaign_sensitivity.csv", index=False
    )
    build_sensitivity_grid(seg_summary, backtest_df).to_csv(
        "campaign_sensitivity_grid.csv", index=False
    )
    build_inactive_segment_spend(seg_summary, backtest_df).to_csv(
        "inactive_segment_spend.csv", index=False
    )
    print("Pipeline complete. All analytical CSVs generated (backtest_baseline.csv is a committed input).")


if __name__ == "__main__":
    main()
