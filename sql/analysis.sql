-- 第三阶段：SQLite 基础业务分析
-- 数据来源：data/processed/online_retail_clean.csv -> transactions
-- 每行是商品交易明细；一张 InvoiceNo 订单可以包含多行商品。
-- 沿用清洗后正常销售口径，不再筛选、不提前舍入 Revenue。
-- 以下 8 条查询均可独立执行。name 注释用于 Python 脚本识别并导出结果。

-- name: total_revenue
-- 1. 全部清洗后交易一共带来多少销售额？
SELECT SUM(Revenue) AS total_revenue
FROM transactions;

-- name: total_orders
-- 2. 一共有多少张订单？同一订单的多行商品只计一次。
SELECT COUNT(DISTINCT InvoiceNo) AS total_orders
FROM transactions;

-- name: total_customers
-- 3. 一共有多少位下单客户？多次购买的客户只计一次。
SELECT COUNT(DISTINCT CustomerID) AS total_customers
FROM transactions;

-- name: country_revenue
-- 4. 各国家的销售额排名如何？返回全部国家，不只返回前 10 名。
-- CTE 先计算国家销售额，RANK() 再对汇总结果排名。
-- 同销售额并列排名，后续名次跳号；Country 用于并列时稳定排序。
WITH country_sales AS (
    SELECT Country, SUM(Revenue) AS total_revenue
    FROM transactions
    GROUP BY Country
)
SELECT
    RANK() OVER (ORDER BY total_revenue DESC) AS revenue_rank,
    Country,
    total_revenue
FROM country_sales
ORDER BY total_revenue DESC, Country;

-- name: top_customers
-- 5. 累计消费金额最高的 10 位客户是谁？
SELECT CustomerID, SUM(Revenue) AS total_revenue
FROM transactions
GROUP BY CustomerID
ORDER BY total_revenue DESC, CustomerID
LIMIT 10;

-- name: monthly_revenue
-- 6. 每个月的销售额是多少？YearMonth 为 YYYY-MM，可直接按时间排序。
-- 当前数据截至 2011-12-09，2011-12 不是完整月份。
SELECT YearMonth, SUM(Revenue) AS total_revenue
FROM transactions
GROUP BY YearMonth
ORDER BY YearMonth;

-- name: customer_order_summary
-- 7. 每位客户下了多少单、累计消费多少、平均每单消费多少？
-- 必须先按客户和订单汇总；直接 AVG(Revenue) 得到的是商品行均额。
WITH order_totals AS (
    SELECT
        CustomerID,
        InvoiceNo,
        SUM(Revenue) AS order_revenue
    FROM transactions
    GROUP BY CustomerID, InvoiceNo
)
SELECT
    CustomerID,
    COUNT(*) AS order_count,
    SUM(order_revenue) AS total_spend,
    AVG(order_revenue) AS avg_order_value
FROM order_totals
GROUP BY CustomerID
ORDER BY total_spend DESC, CustomerID;

-- name: top_products
-- 8. 累计销售额最高的 10 个商品编码是什么？按销售额而非销量排序。
-- 同一 StockCode 可能有多个 Description，因此只按编码分组。
-- MIN(Description) 选取一个字典序最小的代表名称，不代表最新或官方名称。
-- 沿用 CSV 的全部商品编码，不额外排除邮费等非实物条目。
SELECT
    StockCode,
    MIN(Description) AS product_name,
    SUM(Revenue) AS total_revenue
FROM transactions
GROUP BY StockCode
ORDER BY total_revenue DESC, StockCode
LIMIT 10;
