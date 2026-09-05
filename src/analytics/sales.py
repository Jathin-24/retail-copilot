"""
Deterministic sales analytics calculations.
Calculates product performance, store performance, gross margins,
and deterministic period-over-period comparisons without LLM reasoning.
"""
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Union
from src.database.connection import get_db_connection
from src.analytics.models import ProductPerformance, StorePerformance

def _get_date_bounds(conn, start_date: Optional[str], end_date: Optional[str]):
    """Helper to resolve start and end dates against the sales table."""
    cursor = conn.cursor()
    if not start_date or not end_date:
        cursor.execute("SELECT MIN(date), MAX(date) FROM sales")
        min_date, max_date = cursor.fetchone()
        start = start_date or min_date or "2024-06-01"
        end = end_date or max_date or "2024-08-29"
    else:
        start = start_date
        end = end_date
    return start, end

def calculate_product_performance(
    product_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    store_id: Optional[str] = None
) -> Union[ProductPerformance, List[Dict[str, Any]]]:
    """
    Deterministically computes sales, revenue, estimated COGS, gross profit,
    margins, transaction size, and comparison with the previous equal-length period.
    
    If product_id is None, returns an aggregated summary list across all products.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # If called with no arguments at all, provide backwards-compatible global list
    if product_id is None:
        cursor.execute("""
            SELECT 
                p.product_id,
                p.sku,
                p.product_name,
                p.category,
                p.selling_price,
                p.cost_price,
                p.reorder_point,
                COALESCE(SUM(s.quantity), 0) as total_units_sold,
                COALESCE(SUM(s.quantity * s.unit_price - s.discount_amount), 0.0) as total_revenue,
                COALESCE(SUM(s.quantity * (p.selling_price - p.cost_price) - s.discount_amount), 0.0) as total_gross_profit
            FROM products p
            LEFT JOIN sales s ON p.product_id = s.product_id
            GROUP BY p.product_id, p.sku, p.product_name, p.category, p.selling_price, p.cost_price, p.reorder_point
            ORDER BY total_revenue DESC;
        """)
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    start_date, end_date = _get_date_bounds(conn, start_date, end_date)

    # Calculate period length in calendar days
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    period_days = max(1, (end_dt - start_dt).days + 1)

    # Compute preceding period of equal length
    prev_end_dt = start_dt - timedelta(days=1)
    prev_start_dt = prev_end_dt - timedelta(days=period_days - 1)
    prev_start_date = prev_start_dt.strftime("%Y-%m-%d")
    prev_end_date = prev_end_dt.strftime("%Y-%m-%d")

    # Fetch product metadata
    cursor.execute("SELECT product_id, sku, product_name, category, cost_price, selling_price FROM products WHERE product_id = ?", (product_id,))
    prod = cursor.fetchone()
    if not prod:
        conn.close()
        raise ValueError(f"Product ID '{product_id}' not found in database.")

    cost_price = float(prod["cost_price"])

    # Query metrics for current period
    query_current = """
        SELECT 
            COALESCE(SUM(quantity), 0) as units_sold,
            COALESCE(SUM(quantity * unit_price - discount_amount), 0.0) as revenue,
            COALESCE(SUM(quantity * ?), 0.0) as estimated_cogs,
            COUNT(transaction_id) as total_transactions,
            COUNT(DISTINCT date) as number_of_sales_days
        FROM sales
        WHERE product_id = ? AND date BETWEEN ? AND ?
    """
    params_current = [cost_price, product_id, start_date, end_date]
    if store_id:
        query_current += " AND store_id = ?"
        params_current.append(store_id)

    cursor.execute(query_current, params_current)
    curr_row = cursor.fetchone()

    units_sold = int(curr_row["units_sold"])
    revenue = round(float(curr_row["revenue"]), 2)
    estimated_cogs = round(float(curr_row["estimated_cogs"]), 2)
    gross_profit = round(revenue - estimated_cogs, 2)
    gross_margin_percent = round((gross_profit / revenue * 100.0), 2) if revenue > 0 else 0.0
    average_daily_units = round(units_sold / float(period_days), 2)
    total_tx = int(curr_row["total_transactions"])
    average_transaction_value = round(revenue / float(total_tx), 2) if total_tx > 0 else 0.0
    number_of_sales_days = int(curr_row["number_of_sales_days"])

    # Query metrics for previous period
    query_prev = """
        SELECT 
            COALESCE(SUM(quantity), 0) as units_sold,
            COALESCE(SUM(quantity * unit_price - discount_amount), 0.0) as revenue,
            COALESCE(SUM(quantity * ?), 0.0) as estimated_cogs
        FROM sales
        WHERE product_id = ? AND date BETWEEN ? AND ?
    """
    params_prev = [cost_price, product_id, prev_start_date, prev_end_date]
    if store_id:
        query_prev += " AND store_id = ?"
        params_prev.append(store_id)

    cursor.execute(query_prev, params_prev)
    prev_row = cursor.fetchone()
    conn.close()

    prev_units = int(prev_row["units_sold"])
    prev_revenue = round(float(prev_row["revenue"]), 2)
    prev_cogs = round(float(prev_row["estimated_cogs"]), 2)
    prev_gross_profit = round(prev_revenue - prev_cogs, 2)

    # Calculate growth rates safely
    units_growth_pct = round(((units_sold - prev_units) / prev_units * 100.0), 2) if prev_units > 0 else (100.0 if units_sold > 0 else 0.0)
    rev_growth_pct = round(((revenue - prev_revenue) / prev_revenue * 100.0), 2) if prev_revenue > 0 else (100.0 if revenue > 0 else 0.0)
    gp_growth_pct = round(((gross_profit - prev_gross_profit) / abs(prev_gross_profit) * 100.0), 2) if prev_gross_profit != 0 else (100.0 if gross_profit > 0 else 0.0)

    comparison = {
        "previous_period_start": prev_start_date,
        "previous_period_end": prev_end_date,
        "previous_units_sold": prev_units,
        "previous_revenue": prev_revenue,
        "previous_gross_profit": prev_gross_profit,
        "units_growth_percent": units_growth_pct,
        "revenue_growth_percent": rev_growth_pct,
        "gross_profit_growth_percent": gp_growth_pct
    }

    return ProductPerformance(
        product_id=prod["product_id"],
        product_name=prod["product_name"],
        sku=prod["sku"],
        category=prod["category"],
        store_id=store_id,
        start_date=start_date,
        end_date=end_date,
        units_sold=units_sold,
        revenue=revenue,
        estimated_cogs=estimated_cogs,
        gross_profit=gross_profit,
        gross_margin_percent=gross_margin_percent,
        average_daily_units=average_daily_units,
        average_transaction_value=average_transaction_value,
        number_of_sales_days=number_of_sales_days,
        comparison_with_previous_period=comparison
    )

def calculate_store_performance(
    store_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> Union[StorePerformance, List[Dict[str, Any]]]:
    """
    Deterministically computes revenue, units, gross profit, gross margin,
    and period-over-period growth for a specific store.
    
    If store_id is None, returns an overview list across all stores.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    if store_id is None:
        cursor.execute("""
            SELECT 
                s.store_id,
                s.store_name,
                s.city,
                s.state,
                s.store_type,
                COALESCE(SUM(sa.quantity), 0) as total_units_sold,
                COALESCE(SUM(sa.quantity * sa.unit_price - sa.discount_amount), 0.0) as total_revenue,
                COUNT(sa.transaction_id) as total_transactions
            FROM stores s
            LEFT JOIN sales sa ON s.store_id = sa.store_id
            GROUP BY s.store_id, s.store_name, s.city, s.state, s.store_type
            ORDER BY total_revenue DESC;
        """)
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    start_date, end_date = _get_date_bounds(conn, start_date, end_date)

    cursor.execute("SELECT store_id, store_name, city, state, store_type FROM stores WHERE store_id = ?", (store_id,))
    st = cursor.fetchone()
    if not st:
        conn.close()
        raise ValueError(f"Store ID '{store_id}' not found in database.")

    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    period_days = max(1, (end_dt - start_dt).days + 1)

    prev_end_dt = start_dt - timedelta(days=1)
    prev_start_dt = prev_end_dt - timedelta(days=period_days - 1)
    prev_start_date = prev_start_dt.strftime("%Y-%m-%d")
    prev_end_date = prev_end_dt.strftime("%Y-%m-%d")

    # Current period metrics
    cursor.execute("""
        SELECT 
            COALESCE(SUM(s.quantity), 0) as units,
            COALESCE(SUM(s.quantity * s.unit_price - s.discount_amount), 0.0) as revenue,
            COALESCE(SUM(s.quantity * p.cost_price), 0.0) as estimated_cogs
        FROM sales s
        JOIN products p ON s.product_id = p.product_id
        WHERE s.store_id = ? AND s.date BETWEEN ? AND ?;
    """, (store_id, start_date, end_date))
    curr = cursor.fetchone()

    units = int(curr["units"])
    revenue = round(float(curr["revenue"]), 2)
    estimated_cogs = round(float(curr["estimated_cogs"]), 2)
    gross_profit = round(revenue - estimated_cogs, 2)
    gross_margin = round((gross_profit / revenue * 100.0), 2) if revenue > 0 else 0.0

    # Previous period metrics
    cursor.execute("""
        SELECT 
            COALESCE(SUM(s.quantity), 0) as units,
            COALESCE(SUM(s.quantity * s.unit_price - s.discount_amount), 0.0) as revenue,
            COALESCE(SUM(s.quantity * p.cost_price), 0.0) as estimated_cogs
        FROM sales s
        JOIN products p ON s.product_id = p.product_id
        WHERE s.store_id = ? AND s.date BETWEEN ? AND ?;
    """, (store_id, prev_start_date, prev_end_date))
    prev = cursor.fetchone()
    conn.close()

    prev_units = int(prev["units"])
    prev_revenue = round(float(prev["revenue"]), 2)
    prev_cogs = round(float(prev["estimated_cogs"]), 2)
    prev_gross_profit = round(prev_revenue - prev_cogs, 2)

    rev_growth = round(((revenue - prev_revenue) / prev_revenue * 100.0), 2) if prev_revenue > 0 else (100.0 if revenue > 0 else 0.0)
    units_growth = round(((units - prev_units) / prev_units * 100.0), 2) if prev_units > 0 else (100.0 if units > 0 else 0.0)
    gp_growth = round(((gross_profit - prev_gross_profit) / abs(prev_gross_profit) * 100.0), 2) if prev_gross_profit != 0 else (100.0 if gross_profit > 0 else 0.0)

    growth = {
        "previous_period_start": prev_start_date,
        "previous_period_end": prev_end_date,
        "previous_revenue": prev_revenue,
        "previous_units": prev_units,
        "previous_gross_profit": prev_gross_profit,
        "revenue_growth_percent": rev_growth,
        "units_growth_percent": units_growth,
        "gross_profit_growth_percent": gp_growth
    }

    return StorePerformance(
        store_id=st["store_id"],
        store_name=st["store_name"],
        start_date=start_date,
        end_date=end_date,
        revenue=revenue,
        units=units,
        estimated_cogs=estimated_cogs,
        gross_profit=gross_profit,
        gross_margin=gross_margin,
        growth_vs_previous_period=growth
    )
