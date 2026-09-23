"""第四阶段：从清洗后的正常销售记录生成可复核的 RFM 客户价值分析。"""

from math import ceil, isclose
from pathlib import Path

import matplotlib

# 与现有分析脚本一致：没有图形界面时也能导出 PNG。
matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, PercentFormatter
import numpy as np
import pandas as pd


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_PATH = BASE_DIR / "data" / "processed" / "online_retail_clean.csv"
RESULTS_DIR = BASE_DIR / "results" / "rfm"
EXPECTED_CUSTOMERS = 4_338  # 本项目当前清洗数据快照的客户数
REQUIRED_COLUMNS = {"CustomerID", "InvoiceNo", "InvoiceDate", "Revenue"}

SEGMENT_ORDER = [
    "Champions",
    "Loyal Customers",
    "Potential Loyalists",
    "New Customers",
    "At Risk",
    "Lost Customers",
    "Other",
]
SEGMENT_COLORS = {
    "Champions": "#236B83",
    "Loyal Customers": "#4C78A8",
    "Potential Loyalists": "#81A7C6",
    "New Customers": "#D6A34A",
    "At Risk": "#D17A37",
    "Lost Customers": "#B75757",
    "Other": "#8B96A3",
}


def load_data() -> pd.DataFrame:
    """保留客户和订单编号的字符串类型，并检查 RFM 所需原始字段。"""
    if not DATA_PATH.exists():
        raise FileNotFoundError(f"未找到清洗后的 CSV：{DATA_PATH}")

    df = pd.read_csv(
        DATA_PATH, dtype={"CustomerID": "string", "InvoiceNo": "string"}
    )
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"CSV 缺少 RFM 必需字段：{sorted(missing)}")
    if df.empty or df[list(REQUIRED_COLUMNS)].isna().any().any():
        raise ValueError("CSV 为空或 RFM 必需字段含缺失值。")

    df["InvoiceDate"] = pd.to_datetime(df["InvoiceDate"], errors="raise")
    df["Revenue"] = pd.to_numeric(df["Revenue"], errors="raise")
    if not np.isfinite(df["Revenue"]).all() or (df["Revenue"] <= 0).any():
        raise ValueError("Revenue 必须是有限的正数；请检查正常销售数据。")
    return df


def build_rfm(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Timestamp]:
    """一位客户一行；最近交易、不同订单数和销售额之和分别构成 R/F/M。"""
    # 按题目要求采用最大交易时间戳 + 24 小时，保留时分秒。
    reference_date = df["InvoiceDate"].max() + pd.Timedelta(days=1)
    customers = df.groupby("CustomerID", as_index=False).agg(
        LastPurchaseDate=("InvoiceDate", "max"),
        Frequency=("InvoiceNo", "nunique"),  # COUNT(DISTINCT InvoiceNo)
        Monetary=("Revenue", "sum"),
    )
    # .dt.days 表示已过去的完整 24 小时数；最新交易的 R 恰为 1。
    customers["Recency"] = (
        reference_date - customers["LastPurchaseDate"]
    ).dt.days.astype("int64")
    return customers[
        ["CustomerID", "Recency", "Frequency", "Monetary", "LastPurchaseDate"]
    ], reference_date


def score_metric(values: pd.Series, *, higher_is_better: bool) -> pd.Series:
    """用客户百分位给 1~5 分；相同原值始终同分，绝不拆开并列值。"""
    if values.empty or values.isna().any():
        raise ValueError("RFM 打分需要非空且无缺失的指标。")
    if values.nunique() == 1:
        # 完全相同的指标没有高低可分，给所有客户中性 3 分。
        return pd.Series(3, index=values.index, dtype="int8")

    # F=1 等并列值很多，直接 qcut(原值, 5) 会产生重复分位边界。
    # method="min" 让一个并列组都取该组最低名次；不修改原始 R/F/M，
    # 也不使用 method="first" 把相同指标硬拆进不同组。组人数不必各占 20%。
    # F/M 从小到大排名；R 从大到小排名，所以高名次总是更高价值。
    ranks = values.rank(method="min", ascending=higher_is_better)
    percentile = (ranks - 1) / (len(values) - 1)
    scores = (np.floor(percentile * 5) + 1).clip(1, 5)
    return scores.astype("int8")


def add_scores(customers: pd.DataFrame) -> pd.DataFrame:
    """R 越小越好，F/M 越大越好；RFM_Score 是三位字符串。"""
    customers = customers.copy()
    customers["R_Score"] = score_metric(
        customers["Recency"], higher_is_better=False
    )
    customers["F_Score"] = score_metric(
        customers["Frequency"], higher_is_better=True
    )
    customers["M_Score"] = score_metric(
        customers["Monetary"], higher_is_better=True
    )
    customers["RFM_Score"] = (
        customers["R_Score"].astype(str)
        + customers["F_Score"].astype(str)
        + customers["M_Score"].astype(str)
    )
    return customers


def segment_masks(customers: pd.DataFrame) -> dict[str, pd.Series]:
    """七类分群的互斥条件；未命中前六类的客户属于 Other。"""
    r, f, m = (customers[f"{metric}_Score"] for metric in "RFM")

    # Champions：最近购买、购买频繁且消费高，R/F/M 都至少 4 分。
    champions = (r >= 4) & (f >= 4) & (m >= 4)
    # Loyal：最近或中等活跃且购买频繁；Champions 优先，不重复归类。
    loyal = (r >= 3) & (f >= 4) & ~champions
    # Potential：最近购买，已有 2~3 分的复购频率，有培养潜力。
    potential = (r >= 4) & f.between(2, 3)
    # New：最近购买，但 F=1；表示当前观察窗内仅一单，并非真实注册时间。
    new = (r >= 4) & (f == 1)
    # At Risk：已久未购买（R<=2），但曾经频繁或高额消费。
    at_risk = (r <= 2) & ((f >= 3) | (m >= 4))
    # Lost：已久未购买，购买频率和金额都较低。
    lost = (r <= 2) & (f <= 2) & (m <= 3)
    masks = {
        "Champions": champions,
        "Loyal Customers": loyal,
        "Potential Loyalists": potential,
        "New Customers": new,
        "At Risk": at_risk,
        "Lost Customers": lost,
    }
    masks["Other"] = ~pd.DataFrame(masks).any(axis=1)
    return masks


def add_segments(customers: pd.DataFrame) -> pd.DataFrame:
    """按明确的互斥规则为每位客户指定恰好一个群组。"""
    customers = customers.copy()
    masks = segment_masks(customers)
    membership_count = pd.DataFrame(masks).sum(axis=1)
    if not membership_count.eq(1).all():
        raise ValueError("客户分群条件重叠或存在遗漏。")
    customers["Segment"] = np.select(
        [masks[name] for name in SEGMENT_ORDER], SEGMENT_ORDER, default="Other"
    )
    return customers


def summarize_segments(customers: pd.DataFrame) -> pd.DataFrame:
    """按群统计人数、人数占比、收入、收入占比和人均消费。"""
    total_customers = len(customers)
    total_revenue = customers["Monetary"].sum()
    summary = (
        customers.groupby("Segment")
        .agg(CustomerCount=("CustomerID", "size"), TotalRevenue=("Monetary", "sum"))
        .reindex(SEGMENT_ORDER, fill_value=0)
        .rename_axis("Segment")
        .reset_index()
    )
    summary["CustomerSharePct"] = summary["CustomerCount"] / total_customers * 100
    summary["RevenueSharePct"] = summary["TotalRevenue"] / total_revenue * 100
    summary["AverageMonetary"] = summary["TotalRevenue"].div(
        summary["CustomerCount"].replace(0, np.nan)
    )
    return summary[
        [
            "Segment", "CustomerCount", "CustomerSharePct", "TotalRevenue",
            "RevenueSharePct", "AverageMonetary",
        ]
    ]


def summarize_concentration(customers: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """按 Monetary 降序计算累计贡献；Top x% 人数向上取整。"""
    ranked = customers.sort_values(
        ["Monetary", "CustomerID"], ascending=[False, True]
    ).reset_index(drop=True)
    ranked["CumulativeCustomersPct"] = (
        np.arange(1, len(ranked) + 1) / len(ranked) * 100
    )
    ranked["CumulativeRevenuePct"] = (
        ranked["Monetary"].cumsum() / ranked["Monetary"].sum() * 100
    )

    rows = []
    for pct in (10, 20):
        count = ceil(len(ranked) * pct / 100)
        revenue = float(ranked["Monetary"].iloc[:count].sum())
        rows.append(
            {
                "TopCustomerPct": pct,
                "CustomerCount": count,
                "ActualCustomerPct": count / len(ranked) * 100,
                "Revenue": revenue,
                "RevenueSharePct": revenue / ranked["Monetary"].sum() * 100,
            }
        )
    return pd.DataFrame(rows), ranked


def validate_results(
    df: pd.DataFrame, customers: pd.DataFrame, summary: pd.DataFrame
) -> pd.DataFrame:
    """将关键核对结果导出；任一项失败时中止生成最终分析。"""
    source_customers = set(df["CustomerID"])
    # 与 groupby nunique 不同，独立用客户-订单去重后计数复核 F。
    distinct_orders = (
        df[["CustomerID", "InvoiceNo"]]
        .drop_duplicates()
        .groupby("CustomerID")
        .size()
    )
    frequency_matches = customers.set_index("CustomerID")["Frequency"].eq(
        distinct_orders.reindex(customers["CustomerID"]).to_numpy()
    ).all()
    source_revenue = float(df["Revenue"].sum())
    rfm_revenue = float(customers["Monetary"].sum())
    masks = segment_masks(customers)
    exactly_one_segment = pd.DataFrame(masks).sum(axis=1).eq(1).all()
    expected_segments = np.select(
        [masks[name] for name in SEGMENT_ORDER], SEGMENT_ORDER, default="Other"
    )
    segment_labels_match = customers["Segment"].eq(expected_segments).all()
    scores_in_range = customers[["R_Score", "F_Score", "M_Score"]].apply(
        lambda column: column.between(1, 5).all()
    ).all()
    score_strings_match = customers["RFM_Score"].eq(
        customers["R_Score"].astype(str)
        + customers["F_Score"].astype(str)
        + customers["M_Score"].astype(str)
    ).all()
    checks = [
        ("expected_customer_count", len(customers), EXPECTED_CUSTOMERS, len(customers) == EXPECTED_CUSTOMERS),
        ("source_customer_count", len(customers), len(source_customers), len(customers) == len(source_customers)),
        ("unique_customer_ids", customers["CustomerID"].nunique(), len(customers), customers["CustomerID"].is_unique),
        ("same_customer_ids", set(customers["CustomerID"]) == source_customers, True, set(customers["CustomerID"]) == source_customers),
        ("monetary_equals_source_revenue", rfm_revenue, source_revenue, isclose(rfm_revenue, source_revenue, rel_tol=0, abs_tol=1e-7)),
        ("frequency_equals_distinct_invoices", bool(frequency_matches), True, bool(frequency_matches)),
        ("exactly_one_segment_per_customer", bool(exactly_one_segment), True, bool(exactly_one_segment)),
        ("segment_labels_match_rules", bool(segment_labels_match), True, bool(segment_labels_match)),
        ("valid_segment_names", customers["Segment"].isin(SEGMENT_ORDER).all(), True, customers["Segment"].isin(SEGMENT_ORDER).all()),
        ("scores_between_one_and_five", bool(scores_in_range), True, bool(scores_in_range)),
        ("rfm_score_matches_components", bool(score_strings_match), True, bool(score_strings_match)),
        ("segment_counts_equal_customers", int(summary["CustomerCount"].sum()), len(customers), int(summary["CustomerCount"].sum()) == len(customers)),
        ("segment_revenue_equals_rfm", float(summary["TotalRevenue"].sum()), rfm_revenue, isclose(float(summary["TotalRevenue"].sum()), rfm_revenue, rel_tol=0, abs_tol=1e-7)),
    ]
    validation = pd.DataFrame(checks, columns=["Check", "Actual", "Expected", "Passed"])
    validation.to_csv(RESULTS_DIR / "validation.csv", index=False, encoding="utf-8-sig")
    if not validation["Passed"].all():
        failed = validation.loc[~validation["Passed"], "Check"].tolist()
        raise ValueError(f"RFM 验证失败：{failed}。详情见 results/rfm/validation.csv")
    return validation


def chart_style(ax: plt.Axes) -> None:
    """保持三张图一致的简洁作品集样式。"""
    ax.set_facecolor("#FFFFFF")
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_color("#D7DEE5")
    ax.tick_params(axis="both", colors="#354354", length=0, labelsize=10)
    ax.grid(axis="x", color="#E5EAF0", linewidth=0.8)
    ax.set_axisbelow(True)


def plot_segment_bars(summary: pd.DataFrame, *, revenue: bool) -> None:
    """生成客户人数或销售额横向柱状图，标出绝对值和占比。"""
    value_col = "TotalRevenue" if revenue else "CustomerCount"
    share_col = "RevenueSharePct" if revenue else "CustomerSharePct"
    path = RESULTS_DIR / ("segment_revenue.png" if revenue else "customer_segments.png")
    title = "Revenue by RFM segment" if revenue else "Customers by RFM segment"
    xlabel = "Revenue (source data units)" if revenue else "Customers"
    values = summary[value_col].to_numpy()
    max_value = float(values.max())

    fig, ax = plt.subplots(figsize=(11, 6.2), facecolor="white")
    fig.subplots_adjust(left=0.22, right=0.88, top=0.83, bottom=0.15)
    y = np.arange(len(summary))
    ax.barh(
        y, values,
        color=[SEGMENT_COLORS[name] for name in summary["Segment"]],
        height=0.64,
    )
    ax.set_yticks(y, summary["Segment"])
    ax.invert_yaxis()
    ax.set_xlim(0, max_value * 1.24)
    ax.set_xlabel(xlabel, fontsize=10, color="#354354", labelpad=10)
    ax.xaxis.set_major_formatter(
        FuncFormatter(lambda value, _: f"{value / 1_000_000:.1f}m")
        if revenue
        else FuncFormatter(lambda value, _: f"{value:,.0f}")
    )
    chart_style(ax)
    for i, row in summary.iterrows():
        value = float(row[value_col])
        label = f"{value:,.0f}  |  {row[share_col]:.1f}%"
        ax.text(
            value + max_value * 0.015, i, label,
            va="center", ha="left", fontsize=9.5, color="#273747",
        )

    fig.text(0.22, 0.93, title, ha="left", fontsize=18, weight="bold", color="#213547")
    fig.text(
        0.22, 0.885,
        f"Normal sales only  ·  {int(summary['CustomerCount'].sum()):,} customers",
        ha="left", fontsize=10, color="#647486",
    )
    fig.savefig(path, dpi=180, facecolor="white")
    plt.close(fig)


def plot_concentration(ranked: pd.DataFrame, top_summary: pd.DataFrame) -> None:
    """按消费额从高到低绘制客户占比与累计销售额的集中度曲线。"""
    fig, ax = plt.subplots(figsize=(10.5, 6.3), facecolor="white")
    fig.subplots_adjust(left=0.10, right=0.95, top=0.82, bottom=0.17)
    x = np.r_[0, ranked["CumulativeCustomersPct"].to_numpy()]
    y = np.r_[0, ranked["CumulativeRevenuePct"].to_numpy()]
    ax.fill_between(x, y, color="#DDEAF1", alpha=0.75)
    ax.plot(x, y, color="#236B83", linewidth=2.7, label="Actual cumulative revenue")
    ax.plot([0, 100], [0, 100], linestyle="--", color="#9BA7B3", linewidth=1.3, label="Equal contribution")
    for row in top_summary.itertuples(index=False):
        share_x = float(row.ActualCustomerPct)
        share_y = float(row.RevenueSharePct)
        ax.scatter(share_x, share_y, s=55, color="#D17A37", zorder=4)
        ax.plot([share_x, share_x], [0, share_y], color="#D17A37", linestyle=":", linewidth=1)
        ax.annotate(
            f"Top {row.TopCustomerPct}%: {share_y:.1f}%",
            xy=(share_x, share_y), xytext=(share_x + 5, share_y - (8 if row.TopCustomerPct == 10 else 10)),
            fontsize=10, weight="bold", color="#9B5528",
            arrowprops={"arrowstyle": "-", "color": "#D17A37", "lw": 1},
        )
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 103)
    ax.set_xlabel("Customers, ranked by spend (cumulative %)", fontsize=10, color="#354354", labelpad=9)
    ax.set_ylabel("Revenue (cumulative %)", fontsize=10, color="#354354", labelpad=9)
    ax.xaxis.set_major_formatter(PercentFormatter(100))
    ax.yaxis.set_major_formatter(PercentFormatter(100))
    chart_style(ax)
    ax.grid(axis="y", color="#E5EAF0", linewidth=0.8)
    ax.legend(loc="lower right", frameon=False, fontsize=9)
    fig.text(0.10, 0.93, "Customer value concentration", ha="left", fontsize=18, weight="bold", color="#213547")
    fig.text(
        0.10, 0.885,
        f"Customers sorted by Monetary  ·  {len(ranked):,} customers  ·  top group sizes rounded up",
        ha="left", fontsize=10, color="#647486",
    )
    fig.savefig(RESULTS_DIR / "customer_value_concentration.png", dpi=180, facecolor="white")
    plt.close(fig)


def main() -> None:
    """计算、验证并保存第四阶段所有数据和图表。"""
    df = load_data()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    customers, reference_date = build_rfm(df)
    customers = add_segments(add_scores(customers))
    summary = summarize_segments(customers)
    top_summary, ranked = summarize_concentration(customers)
    validation = validate_results(df, customers, summary)

    customers.to_csv(RESULTS_DIR / "rfm_customers.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(RESULTS_DIR / "segment_summary.csv", index=False, encoding="utf-8-sig")
    top_summary.to_csv(RESULTS_DIR / "top_customer_contribution.csv", index=False, encoding="utf-8-sig")
    plot_segment_bars(summary, revenue=False)
    plot_segment_bars(summary, revenue=True)
    plot_concentration(ranked, top_summary)

    print(f"Reference date: {reference_date}")
    print(f"Customers: {len(customers):,}; sales rows: {len(df):,}")
    print("\nRFM segment summary:")
    print(summary.to_string(index=False, float_format=lambda value: f"{value:,.2f}"))
    print("\nTop customer revenue contribution:")
    print(top_summary.to_string(index=False, float_format=lambda value: f"{value:,.2f}"))
    print(f"\nValidation: {len(validation)} / {len(validation)} checks passed")
    print(f"Results: {RESULTS_DIR}")


if __name__ == "__main__":
    main()
