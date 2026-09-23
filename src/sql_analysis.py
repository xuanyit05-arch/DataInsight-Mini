"""导入清洗后的 CSV，运行 analysis.sql，并核对 Python / SQLite 指标。"""

from contextlib import closing
from math import isclose
from pathlib import Path
import re
import sqlite3

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_PATH = BASE_DIR / "data" / "processed" / "online_retail_clean.csv"
DB_PATH = BASE_DIR / "data" / "retail.db"
SQL_PATH = BASE_DIR / "sql" / "analysis.sql"
RESULTS_DIR = BASE_DIR / "results" / "sql"

COLUMNS = [
    "InvoiceNo", "StockCode", "Description", "Quantity", "InvoiceDate",
    "UnitPrice", "CustomerID", "Country", "Revenue", "Year", "Month", "YearMonth",
]
QUERY_NAMES = [
    "total_revenue", "total_orders", "total_customers", "country_revenue",
    "top_customers", "monthly_revenue", "customer_order_summary", "top_products",
]


def load_data() -> pd.DataFrame:
    """编号按字符串读取；CSV 不保存类型，因此日期需显式转换。"""
    if not DATA_PATH.exists():
        raise FileNotFoundError(f"未找到清洗后的 CSV：{DATA_PATH}")

    df = pd.read_csv(
        DATA_PATH,
        dtype={
            "CustomerID": "string",
            "InvoiceNo": "string",
            "StockCode": "string",
            "YearMonth": "string",
        },
    )
    missing = set(COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"CSV 缺少必要字段：{sorted(missing)}")
    if df.empty or df[COLUMNS].isna().any().any():
        raise ValueError("清洗后的 CSV 为空或含缺失值，请检查输入数据。")

    df["InvoiceDate"] = pd.to_datetime(df["InvoiceDate"], errors="raise")
    if not df["YearMonth"].eq(df["InvoiceDate"].dt.strftime("%Y-%m")).all():
        raise ValueError("YearMonth 与 InvoiceDate 不一致，请检查输入数据。")
    return df


def import_transactions(connection: sqlite3.Connection, df: pd.DataFrame) -> None:
    """用 sqlite3 参数化批量写入；重复运行时刷新 transactions，避免重复累加。"""
    records = df[COLUMNS].copy()
    # SQLite 无专用 datetime 类型，采用可读、可排序的 ISO 格式 TEXT。
    records["InvoiceDate"] = records["InvoiceDate"].dt.strftime("%Y-%m-%d %H:%M:%S")

    # DELETE 和 INSERT 在同一事务中，失败时回滚，保留上一次成功导入的数据。
    with connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                InvoiceNo TEXT NOT NULL,
                StockCode TEXT NOT NULL,
                Description TEXT NOT NULL,
                Quantity INTEGER NOT NULL,
                InvoiceDate TEXT NOT NULL,
                UnitPrice REAL NOT NULL,
                CustomerID TEXT NOT NULL,
                Country TEXT NOT NULL,
                Revenue REAL NOT NULL,
                Year INTEGER NOT NULL,
                Month INTEGER NOT NULL,
                YearMonth TEXT NOT NULL
            )
            """
        )
        connection.execute("DELETE FROM transactions")
        connection.executemany(
            """
            INSERT INTO transactions (
                InvoiceNo, StockCode, Description, Quantity, InvoiceDate,
                UnitPrice, CustomerID, Country, Revenue, Year, Month, YearMonth
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            records.itertuples(index=False, name=None),
        )
        imported_rows = connection.execute(
            "SELECT COUNT(*) FROM transactions"
        ).fetchone()[0]
        if imported_rows != len(df):
            raise ValueError(f"导入行数不一致：CSV={len(df)}，SQLite={imported_rows}")


def load_queries() -> dict[str, str]:
    """根据 name 注释读取 8 条 SQL，实际执行的查询均来自 analysis.sql。"""
    sql_text = SQL_PATH.read_text(encoding="utf-8-sig")
    parts = re.split(r"^-- name: ([a-z_]+)[ \t]*$", sql_text, flags=re.MULTILINE)
    names = parts[1::2]
    if names != QUERY_NAMES:
        raise ValueError("analysis.sql 的查询名称或顺序与预期的 8 项分析不符。")
    return dict(zip(names, parts[2::2]))


def validate_kpis(
    df: pd.DataFrame, results: dict[str, pd.DataFrame]
) -> pd.DataFrame:
    """直接从 CSV 用 pandas 独立重算，记录原始值和绝对差，不用舍入掩盖差异。"""
    python_metrics = {
        "total_revenue": float(df["Revenue"].sum()),
        "total_orders": int(df["InvoiceNo"].nunique()),
        "total_customers": int(df["CustomerID"].nunique()),
    }
    comparisons = []
    for metric, python_value in python_metrics.items():
        sql_value = results[metric].iloc[0, 0].item()
        difference = abs(python_value - sql_value)
        # pandas 浮点数与 SQLite REAL 的求和顺序可能造成末位舍入误差。
        # 仅金额允许小于 0.0000001 的绝对误差，远小于源数据的 0.001 精度。
        matches = (
            isclose(python_value, sql_value, rel_tol=0, abs_tol=1e-7)
            if metric == "total_revenue"
            else python_value == sql_value
        )
        comparisons.append(
            {
                "metric": metric,
                "python_value": python_value,
                "sql_value": sql_value,
                "absolute_difference": difference,
                "exactly_equal": python_value == sql_value,
                "matches": matches,
            }
        )

    comparison = pd.DataFrame(comparisons)
    comparison.to_csv(RESULTS_DIR / "validation.csv", index=False, encoding="utf-8-sig")
    if not comparison["matches"].all():
        raise ValueError(
            "Python 与 SQL 的核心指标不一致，详情见 results/sql/validation.csv。"
        )
    return comparison


def main() -> None:
    """建库、导入、执行查询、校验，并导出全部结果。"""
    df = load_data()
    queries = load_queries()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    with closing(sqlite3.connect(DB_PATH)) as connection:
        import_transactions(connection, df)
        results = {
            name: pd.read_sql_query(query, connection)
            for name, query in queries.items()
        }
        comparison = validate_kpis(df, results)

    print(f"Database: {DB_PATH}")
    print(f"Imported rows: {len(df):,}")
    print(f"Data period: {df['InvoiceDate'].min()} to {df['InvoiceDate'].max()}")
    for number, (name, result) in enumerate(results.items(), start=1):
        # CSV 保留计算精度，终端金额只显示两位小数。
        result.to_csv(RESULTS_DIR / f"{name}.csv", index=False, encoding="utf-8-sig")
        preview = result if name == "monthly_revenue" else result.head(10)
        print(f"\n{number}. {name} ({len(result):,} rows)")
        print(preview.to_string(index=False, float_format=lambda value: f"{value:,.2f}"))
        if len(result) > len(preview):
            print(f"Showing first {len(preview)} rows; full result: {RESULTS_DIR / f'{name}.csv'}")

    print("\nPython / SQL validation (unrounded values):")
    print(comparison.to_string(index=False, float_format=lambda value: f"{value:.17g}"))
    print("All three KPIs match (counts exactly; revenue within 1e-7).")
    print(f"Results: {RESULTS_DIR}")


if __name__ == "__main__":
    main()
