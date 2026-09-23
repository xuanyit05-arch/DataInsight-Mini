"""Portfolio dashboard for the project's existing analysis results."""

from pathlib import Path

import pandas as pd
import streamlit as st


BASE_DIR = Path(__file__).resolve().parent
RESULTS_DIR = BASE_DIR / "results"
SUMMARY_PATH = RESULTS_DIR / "business_summary.csv"
CONTRIBUTION_PATH = RESULTS_DIR / "rfm" / "top_customer_contribution.csv"
CHARTS = {
    "monthly_revenue": RESULTS_DIR / "monthly_revenue.png",
    "country_revenue": RESULTS_DIR / "country_revenue.png",
    "segment_customers": RESULTS_DIR / "rfm" / "customer_segments.png",
    "segment_revenue": RESULTS_DIR / "rfm" / "segment_revenue.png",
    "concentration": RESULTS_DIR / "rfm" / "customer_value_concentration.png",
    "cohort_retention": RESULTS_DIR / "cohort" / "cohort_retention.png",
}


def load_results() -> tuple[pd.Series, pd.DataFrame]:
    """Read saved results; the dashboard does not recalculate analysis metrics."""
    required_paths = [SUMMARY_PATH, CONTRIBUTION_PATH, *CHARTS.values()]
    missing = [path.relative_to(BASE_DIR).as_posix() for path in required_paths if not path.is_file()]
    if missing:
        st.error("Required analysis results are missing: " + ", ".join(missing))
        st.info("Generate the analysis outputs before starting the dashboard. See README.md.")
        st.stop()

    summary = pd.read_csv(SUMMARY_PATH)
    contribution = pd.read_csv(CONTRIBUTION_PATH)
    kpi_columns = {"Total Revenue", "Total Orders", "Total Customers", "AOV"}
    contribution_columns = {"TopCustomerPct", "CustomerCount", "RevenueSharePct"}
    if summary.empty or not kpi_columns.issubset(summary.columns):
        st.error("results/business_summary.csv has no usable KPI row.")
        st.stop()
    if not contribution_columns.issubset(contribution.columns):
        st.error("results/rfm/top_customer_contribution.csv is missing required columns.")
        st.stop()

    top_shares = contribution.set_index("TopCustomerPct")
    if not {10, 20}.issubset(top_shares.index):
        st.error("Top 10% and Top 20% customer contribution rows are required.")
        st.stop()
    return summary.iloc[0], top_shares


def show_chart(name: str) -> None:
    """Display a chart produced by an earlier analysis stage."""
    st.image(CHARTS[name], width="stretch")


def main() -> None:
    st.set_page_config(page_title="DataInsight Mini | Dashboard", layout="wide")
    summary, top_shares = load_results()

    st.title("DataInsight Mini")
    st.caption("Online Retail dashboard · December 2010 to December 2011 · Existing analysis results")

    overview, sales, customer_value, cohort = st.tabs(
        ["Overview", "Sales Analysis", "Customer Value", "Cohort Retention"]
    )

    with overview:
        st.header("Overview")
        revenue, orders, customers, aov = st.columns(4)
        revenue.metric("Total Revenue", f"{summary['Total Revenue']:,.2f}", border=True)
        orders.metric("Total Orders", f"{int(summary['Total Orders']):,}", border=True)
        customers.metric("Total Customers", f"{int(summary['Total Customers']):,}", border=True)
        aov.metric("AOV", f"{summary['AOV']:,.2f}", border=True)
        st.caption(
            "Based on cleaned normal-sales transactions. "
            "AOV is total revenue divided by distinct orders. "
            "Monetary values use the source data's currency units."
        )

    with sales:
        st.header("Sales Analysis")
        st.info("December 2011 data covers only through 9 December; it is not a full month.")
        show_chart("monthly_revenue")
        show_chart("country_revenue")

    with customer_value:
        st.header("Customer Value")
        top_10, top_20 = st.columns(2)
        top_10.metric(
            "Revenue from top 10% of customers",
            f"{top_shares.loc[10, 'RevenueSharePct']:.2f}%",
            border=True,
        )
        top_20.metric(
            "Revenue from top 20% of customers",
            f"{top_shares.loc[20, 'RevenueSharePct']:.2f}%",
            border=True,
        )
        st.caption(
            f"Top 10%: {int(top_shares.loc[10, 'CustomerCount']):,} customers · "
            f"Top 20%: {int(top_shares.loc[20, 'CustomerCount']):,} customers. "
            "Group sizes are rounded up in the existing RFM analysis."
        )
        show_chart("concentration")
        st.divider()
        st.subheader("RFM segments")
        show_chart("segment_customers")
        show_chart("segment_revenue")

    with cohort:
        st.header("Cohort Retention")
        st.markdown(
            "**CohortMonth** is the first purchase month within the observed data. "
            "The horizontal axis shows months since that first purchase. "
            "**Retention rate** = distinct customers who purchased in that month "
            "/ initial customers in the cohort."
        )
        st.caption(
            "This is monthly purchasing retention, not survival retention; "
            "rates do not need to decrease monotonically. "
            "An asterisk marks cells in the partial December 2011 month."
        )
        show_chart("cohort_retention")


if __name__ == "__main__":
    main()
