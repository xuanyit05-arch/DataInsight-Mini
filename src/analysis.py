from pathlib import Path

import matplotlib

# 使用非交互式后端，确保脚本在没有图形界面的环境中也能保存图片。
matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.ticker import StrMethodFormatter


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_PATH = BASE_DIR / "data" / "processed" / "online_retail_clean.csv"
RESULTS_DIR = BASE_DIR / "results"
SUMMARY_PATH = RESULTS_DIR / "business_summary.csv"
COUNTRY_CHART_PATH = RESULTS_DIR / "country_revenue.png"
MONTHLY_CHART_PATH = RESULTS_DIR / "monthly_revenue.png"

REQUIRED_COLUMNS = {
    "InvoiceNo",
    "InvoiceDate",
    "CustomerID",
    "Country",
    "Revenue",
    "YearMonth",
}


def load_data() -> pd.DataFrame:
    """读取清洗后的数据，并统一分析需要的数据类型。"""
    if not DATA_PATH.exists():
        raise FileNotFoundError(
            f"未找到清洗后的数据文件：{DATA_PATH}\n"
            "请先运行 src/clean_data.py。"
        )

    # 在读取阶段指定 string，避免 CustomerID 被推断成数值类型。
    df = pd.read_csv(DATA_PATH, dtype={"CustomerID": "string"})

    missing_columns = REQUIRED_COLUMNS - set(df.columns)
    if missing_columns:
        raise ValueError(f"清洗后的数据缺少必要字段：{sorted(missing_columns)}")

    # CSV 不会保留 datetime 类型，因此读取后需要显式转换。
    df["InvoiceDate"] = pd.to_datetime(df["InvoiceDate"], errors="raise")
    return df


def calculate_kpis(df: pd.DataFrame) -> dict[str, float | int]:
    """计算总销售额、订单数、客户数和平均客单价。"""
    total_revenue = float(df["Revenue"].sum())
    total_orders = int(df["InvoiceNo"].nunique())
    total_customers = int(df["CustomerID"].nunique())

    # 一张订单可能包含多行商品，先汇总为“一张订单一行”。
    order_revenue = (
        df.groupby("InvoiceNo", as_index=False)["Revenue"]
        .sum()
        .rename(columns={"Revenue": "OrderRevenue"})
    )

    if total_orders == 0:
        raise ValueError("数据中没有可用于计算 AOV 的订单。")
    if len(order_revenue) != total_orders:
        raise ValueError("按 InvoiceNo 汇总后的订单数与唯一订单数不一致。")

    aov = total_revenue / total_orders

    return {
        "Total Revenue": total_revenue,
        "Total Orders": total_orders,
        "Total Customers": total_customers,
        "AOV": aov,
    }


def calculate_country_revenue(df: pd.DataFrame) -> pd.DataFrame:
    """按销售额从高到低返回前 10 个国家。"""
    return (
        df.groupby("Country", as_index=False)["Revenue"]
        .sum()
        .sort_values("Revenue", ascending=False)
        .head(10)
        .reset_index(drop=True)
    )


def calculate_monthly_revenue(df: pd.DataFrame) -> pd.DataFrame:
    """按 YearMonth 汇总并按自然月份排序。"""
    monthly_revenue = df.groupby("YearMonth", as_index=False)["Revenue"].sum()
    monthly_revenue["MonthStart"] = pd.to_datetime(
        monthly_revenue["YearMonth"], format="%Y-%m", errors="raise"
    )
    return monthly_revenue.sort_values("MonthStart").reset_index(drop=True)


def save_business_summary(metrics: dict[str, float | int]) -> None:
    """将四个核心指标保存为单行 CSV，并保留计算精度。"""
    summary = pd.DataFrame(
        [
            {
                "Total Revenue": float(metrics["Total Revenue"]),
                "Total Orders": int(metrics["Total Orders"]),
                "Total Customers": int(metrics["Total Customers"]),
                "AOV": float(metrics["AOV"]),
            }
        ]
    )
    summary.to_csv(SUMMARY_PATH, index=False, encoding="utf-8-sig")


def plot_country_revenue(country_revenue: pd.DataFrame) -> None:
    """生成销售额最高的 10 个国家横向柱状图。"""
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.barh(
        country_revenue["Country"],
        country_revenue["Revenue"],
        color="#4C78A8",
    )
    ax.invert_yaxis()
    ax.set_title("Top 10 Countries by Revenue")
    ax.set_xlabel("Revenue")
    ax.set_ylabel("Country")
    ax.xaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
    ax.grid(axis="x", linestyle="--", alpha=0.3)
    fig.tight_layout()
    fig.savefig(COUNTRY_CHART_PATH, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_monthly_revenue(
    monthly_revenue: pd.DataFrame, data_end_date: pd.Timestamp
) -> None:
    """生成月度销售额折线图。"""
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(
        monthly_revenue["MonthStart"],
        monthly_revenue["Revenue"],
        marker="o",
        linewidth=2,
        color="#4C78A8",
    )
    ax.set_title("Monthly Revenue Trend")
    ax.set_xlabel("Month")
    ax.set_ylabel("Revenue")
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.yaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
    ax.grid(axis="y", linestyle="--", alpha=0.3)
    fig.autofmt_xdate(rotation=45)

    # 最后一个月不是完整自然月时，在图中明确提示，避免误读趋势。
    if data_end_date.day < data_end_date.days_in_month:
        fig.text(
            0.99,
            0.01,
            f"Note: final month is partial (data through {data_end_date:%Y-%m-%d}).",
            ha="right",
            fontsize=9,
            color="#555555",
        )

    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(MONTHLY_CHART_PATH, dpi=150, bbox_inches="tight")
    plt.close(fig)


def print_results(
    metrics: dict[str, float | int],
    country_revenue: pd.DataFrame,
    monthly_revenue: pd.DataFrame,
) -> None:
    """在终端打印核心 KPI 和两项分组分析结果。"""
    print("========== Core Business KPIs ==========")
    print(f"Total Revenue: {metrics['Total Revenue']:,.2f}")
    print(f"Total Orders: {metrics['Total Orders']:,}")
    print(f"Total Customers: {metrics['Total Customers']:,}")
    print(f"AOV: {metrics['AOV']:,.2f}")

    print("\n========== Top 10 Countries by Revenue ==========")
    print(
        country_revenue.to_string(
            index=False,
            formatters={"Revenue": lambda value: f"{value:,.2f}"},
        )
    )

    print("\n========== Monthly Revenue ==========")
    print(
        monthly_revenue[["YearMonth", "Revenue"]].to_string(
            index=False,
            formatters={"Revenue": lambda value: f"{value:,.2f}"},
        )
    )


def main() -> None:
    """运行第二阶段的基础业务分析。"""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    df = load_data()
    metrics = calculate_kpis(df)
    country_revenue = calculate_country_revenue(df)
    monthly_revenue = calculate_monthly_revenue(df)

    print_results(metrics, country_revenue, monthly_revenue)
    save_business_summary(metrics)
    plot_country_revenue(country_revenue)
    plot_monthly_revenue(monthly_revenue, df["InvoiceDate"].max())

    print("\nGenerated files:")
    print(f"- {SUMMARY_PATH}")
    print(f"- {COUNTRY_CHART_PATH}")
    print(f"- {MONTHLY_CHART_PATH}")


if __name__ == "__main__":
    main()
