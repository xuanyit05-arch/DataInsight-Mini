"""第五阶段：按客户首次购买月分析正常销售记录的月度留存。"""

from pathlib import Path

import matplotlib

# 无图形界面的环境也可以直接生成作品集图片。
matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.colors import PowerNorm
from matplotlib.ticker import PercentFormatter
import numpy as np
import pandas as pd


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_PATH = BASE_DIR / "data" / "processed" / "online_retail_clean.csv"
RESULTS_DIR = BASE_DIR / "results" / "cohort"
REQUIRED_COLUMNS = {"CustomerID", "InvoiceDate"}


def load_data() -> pd.DataFrame:
    """只读取客户和交易日期；一行仍是商品明细，不代表一位客户。"""
    if not DATA_PATH.exists():
        raise FileNotFoundError(f"未找到清洗后的 CSV：{DATA_PATH}")

    columns = set(pd.read_csv(DATA_PATH, nrows=0).columns)
    missing = REQUIRED_COLUMNS - columns
    if missing:
        raise ValueError(f"CSV 缺少 Cohort 分析必需字段：{sorted(missing)}")

    df = pd.read_csv(
        DATA_PATH, usecols=["CustomerID", "InvoiceDate"],
        dtype={"CustomerID": "string"},
    )
    if df.empty or df[list(REQUIRED_COLUMNS)].isna().any().any():
        raise ValueError("CSV 为空，或 CustomerID / InvoiceDate 含缺失值。")
    if df["CustomerID"].str.strip().eq("").any():
        raise ValueError("CustomerID 含空字符串。")
    df["InvoiceDate"] = pd.to_datetime(df["InvoiceDate"], errors="raise")
    return df


def add_cohort_columns(df: pd.DataFrame) -> pd.DataFrame:
    """将客户的最早购买月映射到其全部交易，并计算跨年的月差。"""
    sales = df.copy()
    first_purchase = sales.groupby("CustomerID")["InvoiceDate"].min()
    sales["CohortMonth"] = sales["CustomerID"].map(first_purchase).dt.to_period("M")
    sales["InvoiceMonth"] = sales["InvoiceDate"].dt.to_period("M")
    sales["CohortIndex"] = (
        (sales["InvoiceMonth"].dt.year - sales["CohortMonth"].dt.year) * 12
        + sales["InvoiceMonth"].dt.month - sales["CohortMonth"].dt.month
    ).astype("int64")
    return sales


def build_matrices(sales: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """每格计算不同 CustomerID 数；未到观察月份留空，已观察但无人购买填 0。"""
    counts = (
        sales.groupby(["CohortMonth", "CohortIndex"])["CustomerID"]
        .nunique()
        .unstack(fill_value=0)
        .sort_index()
    )
    last_month = sales["InvoiceMonth"].max()
    counts = counts.reindex(columns=range(0, int(counts.columns.max()) + 1), fill_value=0)

    # 较晚加入的 Cohort 尚未走到右侧月份；NaN 与真实观察到的 0 必须区分。
    observed_months = np.array([(last_month - month).n for month in counts.index])
    outside_window = np.arange(counts.shape[1])[None, :] > observed_months[:, None]
    counts = counts.mask(outside_window).astype("Int64")
    retention = counts.astype("float64").div(counts[0].astype("float64"), axis=0)
    counts.index.name = retention.index.name = "CohortMonth"
    counts.columns.name = retention.columns.name = "CohortIndex"
    return counts, retention


def validate_results(
    sales: pd.DataFrame, counts: pd.DataFrame, retention: pd.DataFrame
) -> pd.DataFrame:
    """用客户月去重等独立口径复核核心约束，失败时不导出分析结果。"""
    unique_cohort = sales.groupby("CustomerID")["CohortMonth"].nunique().eq(1).all()
    first_month = sales.groupby("CustomerID")["InvoiceDate"].min().dt.to_period("M")
    mapped_cohort = sales.drop_duplicates("CustomerID").set_index("CustomerID")["CohortMonth"]
    cohort_matches_first_purchase = mapped_cohort.sort_index().equals(first_month.sort_index())

    # 独立地先按 CustomerID + 月份去重，再计数，复核未把商品明细当客户。
    unique_customer_months = sales[
        ["CustomerID", "CohortMonth", "CohortIndex"]
    ].drop_duplicates()
    deduplicated_counts = (
        unique_customer_months.groupby(["CohortMonth", "CohortIndex"])
        .size()
        .unstack(fill_value=0)
        .reindex(index=counts.index, columns=counts.columns, fill_value=0)
    )
    distinct_counts_match = np.array_equal(
        counts.fillna(-1).to_numpy(dtype="int64"),
        deduplicated_counts.mask(counts.isna()).fillna(-1).to_numpy(dtype="int64"),
    )
    initial_sizes = sales.groupby("CohortMonth")["CustomerID"].nunique()
    initial_counts_match = counts[0].astype("int64").equals(
        initial_sizes.reindex(counts.index).astype("int64")
    )
    last_month = sales["InvoiceMonth"].max()
    expected_missing = np.array([
        [(cohort + index) > last_month for index in counts.columns]
        for cohort in counts.index
    ])
    observation_window_matches = (
        np.array_equal(counts.isna().to_numpy(), expected_missing)
        and np.array_equal(retention.isna().to_numpy(), expected_missing)
    )
    # 只检查已观测单元格，同时拒绝无穷大与意外 NaN。
    observed_rates = retention.to_numpy(dtype="float64")[~expected_missing]
    valid_rates = np.isfinite(observed_rates).all() and np.all(
        (observed_rates >= 0) & (observed_rates <= 1)
    )
    expected_rates = counts.astype("float64").div(
        counts[0].astype("float64"), axis=0
    )
    rates_match_counts = np.allclose(
        retention.to_numpy(dtype="float64"), expected_rates.to_numpy(dtype="float64"),
        rtol=0, atol=1e-12, equal_nan=True,
    )
    month_zero_is_one = np.allclose(retention[0].to_numpy(), 1.0)
    no_negative_index = sales["CohortIndex"].ge(0).all()
    no_count_exceeds_cohort = counts.le(counts[0], axis=0).stack().all()

    checks = [
        ("one_cohort_month_per_customer", bool(unique_cohort), True),
        ("cohort_matches_first_purchase_month", bool(cohort_matches_first_purchase), True),
        ("cohort_index_nonnegative", bool(no_negative_index), True),
        ("month_zero_retention_is_100_percent", bool(month_zero_is_one), True),
        ("retention_between_zero_and_one", bool(valid_rates), True),
        ("retention_equals_active_over_initial", bool(rates_match_counts), True),
        ("customer_month_counts_are_distinct_customer_ids", bool(distinct_counts_match), True),
        ("initial_counts_equal_distinct_cohort_customers", bool(initial_counts_match), True),
        ("initial_counts_cover_all_customers", int(counts[0].sum()), sales["CustomerID"].nunique()),
        ("active_count_never_exceeds_cohort_size", bool(no_count_exceeds_cohort), True),
        ("future_months_are_blank", bool(observation_window_matches), True),
    ]
    validation = pd.DataFrame(checks, columns=["Check", "Actual", "Expected"])
    validation["Passed"] = validation["Actual"] == validation["Expected"]
    validation.to_csv(RESULTS_DIR / "validation.csv", index=False, encoding="utf-8-sig")
    if not validation["Passed"].all():
        failed = validation.loc[~validation["Passed"], "Check"].tolist()
        raise ValueError(f"Cohort 验证失败：{failed}。详情见 results/cohort/validation.csv")
    return validation


def plot_retention(counts: pd.DataFrame, retention: pd.DataFrame, last_date: pd.Timestamp) -> None:
    """用 imshow 绘制带百分比标签的 Cohort 留存热力图。"""
    values = retention.to_numpy(dtype="float64")
    latest_month_is_partial = last_date.day < last_date.days_in_month
    cmap = plt.colormaps["YlGnBu"].copy()
    cmap.set_bad("#EEF2F5")
    norm = PowerNorm(gamma=0.65, vmin=0, vmax=1)

    fig, ax = plt.subplots(figsize=(14.2, 8.8), facecolor="white")
    fig.subplots_adjust(left=0.205, right=0.885, top=0.79, bottom=0.15)
    ax.set_facecolor("#EEF2F5")
    image = ax.imshow(np.ma.masked_invalid(values), cmap=cmap, norm=norm, aspect="auto")

    ax.set_xticks(np.arange(retention.shape[1]), [str(index) for index in retention.columns])
    ax.set_yticks(
        np.arange(retention.shape[0]),
        [f"{month}   n={int(counts.loc[month, 0]):,}" for month in retention.index],
    )
    ax.set_xlabel("CohortIndex · months since first purchase", labelpad=14, color="#34495E")
    ax.set_ylabel("CohortMonth · initial customers", labelpad=13, color="#34495E")
    ax.tick_params(axis="both", which="major", length=0, labelsize=10, colors="#34495E")
    ax.set_xticks(np.arange(-0.5, retention.shape[1], 1), minor=True)
    ax.set_yticks(np.arange(-0.5, retention.shape[0], 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=1.5)
    ax.tick_params(which="minor", bottom=False, left=False)
    for spine in ax.spines.values():
        spine.set_visible(False)

    for row in range(values.shape[0]):
        for column in range(values.shape[1]):
            value = values[row, column]
            if np.isfinite(value):
                is_partial_month = (
                    latest_month_is_partial
                    and retention.index[row] + int(retention.columns[column])
                    == last_date.to_period("M")
                )
                ax.text(
                    column, row, f"{value:.0%}{'*' if is_partial_month else ''}",
                    ha="center", va="center",
                    fontsize=9, weight="semibold" if column == 0 else "normal",
                    color="white" if norm(value) >= 0.57 else "#23384A",
                )

    colorbar = fig.colorbar(image, ax=ax, fraction=0.035, pad=0.03)
    colorbar.set_ticks([0, 0.1, 0.2, 0.4, 0.6, 0.8, 1.0])
    colorbar.ax.yaxis.set_major_formatter(PercentFormatter(1))
    colorbar.ax.tick_params(labelsize=9, colors="#34495E", length=0)
    colorbar.outline.set_visible(False)
    colorbar.set_label("Retention rate", color="#34495E", labelpad=10)

    fig.text(
        0.205, 0.935, "Monthly customer retention by first-purchase cohort",
        ha="left", fontsize=19, weight="bold", color="#17324A",
    )
    fig.text(
        0.205, 0.885,
        f"Online Retail  ·  {int(counts[0].sum()):,} unique customers  ·  "
        f"{retention.index.min()} to {last_date.to_period('M')}",
        ha="left", fontsize=11, color="#607487",
    )
    partial_note = (
        f"* Partial month (through {last_date:%d %b %Y})"
        if latest_month_is_partial else f"Observed through {last_date:%d %b %Y}"
    )
    fig.text(
        0.205, 0.065,
        f"Gray = outside observation window  ·  {partial_note}  "
        "·  Counts use distinct customers",
        ha="left", fontsize=9.5, color="#607487",
    )
    fig.savefig(RESULTS_DIR / "cohort_retention.png", dpi=190, facecolor="white")
    plt.close(fig)


def main() -> None:
    """生成、验证并保存第五阶段的留存矩阵与热力图。"""
    source = load_data()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    sales = add_cohort_columns(source)
    counts, retention = build_matrices(sales)
    validation = validate_results(sales, counts, retention)

    counts.to_csv(RESULTS_DIR / "cohort_counts.csv", encoding="utf-8-sig")
    retention.to_csv(RESULTS_DIR / "cohort_retention.csv", encoding="utf-8-sig")
    plot_retention(counts, retention, sales["InvoiceDate"].max())

    print(f"Sales rows: {len(sales):,}; unique customers: {sales['CustomerID'].nunique():,}")
    print(f"Cohorts: {len(counts)}; observation: {sales['InvoiceDate'].min()} to {sales['InvoiceDate'].max()}")
    print(f"Validation: {len(validation)} / {len(validation)} checks passed")
    print(f"Results: {RESULTS_DIR}")


if __name__ == "__main__":
    main()
