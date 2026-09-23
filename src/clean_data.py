from pathlib import Path

import pandas as pd


# 项目中的输入、输出路径
BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DATA_PATH = BASE_DIR / "data" / "raw" / "online_retail.xlsx"
PROCESSED_DIR = BASE_DIR / "data" / "processed"
PROCESSED_DATA_PATH = PROCESSED_DIR / "online_retail_clean.csv"

REQUIRED_COLUMNS = {
    "InvoiceNo",
    "StockCode",
    "Description",
    "Quantity",
    "InvoiceDate",
    "UnitPrice",
    "CustomerID",
    "Country",
}


def main() -> None:
    """读取、检查、清洗并保存 Online Retail 数据。"""
    # 若 data/processed 不存在，则自动创建
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    if not RAW_DATA_PATH.exists():
        raise FileNotFoundError(
            f"未找到原始数据文件：{RAW_DATA_PATH}\n"
            "请将 Online Retail Excel 文件放到该路径后重新运行。"
        )

    print("正在读取原始数据……")
    df = pd.read_excel(RAW_DATA_PATH, engine="openpyxl")

    # 先确认数据结构，避免因字段名不一致而在清洗中途失败
    missing_columns = REQUIRED_COLUMNS - set(df.columns)
    if missing_columns:
        raise ValueError(f"原始数据缺少必要字段：{sorted(missing_columns)}")

    print("\n========== 原始数据检查 ==========")
    print(f"数据行数：{len(df):,}")
    print(f"字段名：{df.columns.tolist()}")

    print("\n数据类型：")
    print(df.dtypes.to_string())

    print("\n缺失值数量：")
    print(df.isna().sum().to_string())

    duplicate_count = int(df.duplicated().sum())
    print(f"\n完全重复数据：{duplicate_count:,} 行")

    print("\n========== 开始清洗 ==========")

    # 1. 删除所有字段都相同的重复行
    before_rows = len(df)
    df = df.drop_duplicates().copy()
    print(f"删除完全重复数据：{before_rows - len(df):,} 行")

    # 2. CustomerID 缺失时无法进行客户维度分析，因此删除
    before_rows = len(df)
    df = df.dropna(subset=["CustomerID"]).copy()
    print(f"删除 CustomerID 缺失记录：{before_rows - len(df):,} 行")

    # 3. Quantity <= 0 通常对应退货或取消，本阶段只分析正常销售
    before_rows = len(df)
    df = df.loc[df["Quantity"] > 0].copy()
    print(f"剔除 Quantity <= 0 的记录：{before_rows - len(df):,} 行")

    # 4. 单价小于等于 0 不符合正常销售分析口径
    before_rows = len(df)
    df = df.loc[df["UnitPrice"] > 0].copy()
    print(f"剔除 UnitPrice <= 0 的记录：{before_rows - len(df):,} 行")

    # 5. 统一关键字段的数据类型
    df["InvoiceDate"] = pd.to_datetime(df["InvoiceDate"], errors="raise")
    # Excel 中客户编号会被读成 17850.0，先转为整数再转为字符串
    df["CustomerID"] = df["CustomerID"].astype("Int64").astype("string")

    # 6. 新建后续分析会使用的字段
    df["Revenue"] = df["Quantity"] * df["UnitPrice"]
    df["Year"] = df["InvoiceDate"].dt.year
    df["Month"] = df["InvoiceDate"].dt.month
    df["YearMonth"] = df["InvoiceDate"].dt.strftime("%Y-%m")

    # 清理筛选后留下的旧索引，再写入 CSV
    df = df.reset_index(drop=True)
    df.to_csv(PROCESSED_DATA_PATH, index=False, encoding="utf-8-sig")

    print("\n========== 清洗后复核 ==========")
    print(f"数据行数：{len(df):,}")
    print(f"字段数：{len(df.columns)}")
    print(f"完全重复数据：{int(df.duplicated().sum()):,} 行")
    print(f"CustomerID 缺失：{int(df['CustomerID'].isna().sum()):,} 行")
    print(f"Quantity <= 0：{int((df['Quantity'] <= 0).sum()):,} 行")
    print(f"UnitPrice <= 0：{int((df['UnitPrice'] <= 0).sum()):,} 行")

    print("\n清洗后数据类型：")
    print(df.dtypes.to_string())

    print(f"\n清洗后的数据已保存至：{PROCESSED_DATA_PATH}")


if __name__ == "__main__":
    main()
