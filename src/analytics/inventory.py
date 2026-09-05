"""
Deterministic inventory health and turnover calculations.
Computes daily velocity over 7d and 30d windows, Days of Inventory,
valuations at cost and retail, sell-through rate, and inventory turnover.
"""
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from src.database.connection import get_db_connection
from src.analytics.models import InventoryHealth, InventoryTurnover

def calculate_inventory_health(
    store_id: str,
    product_id: str,
    as_of_date: Optional[str] = None
) -> InventoryHealth:
    """
    Deterministically computes stock health, velocity (7d and 30d),
    runway (Days of Inventory), valuation at cost and retail,
    and sell-through rate for a store-product pair.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    if not as_of_date:
        cursor.execute("SELECT MAX(date) FROM inventory")
        row = cursor.fetchone()
        as_of_date = row[0] if row and row[0] else "2024-08-29"

    # Get product pricing and reorder point
    cursor.execute("""
        SELECT product_id, product_name, cost_price, selling_price, reorder_point
        FROM products WHERE product_id = ?;
    """, (product_id,))
    prod = cursor.fetchone()
    if not prod:
        conn.close()
        raise ValueError(f"Product ID '{product_id}' not found.")

    # Get current closing stock as of date
    cursor.execute("""
        SELECT closing_stock FROM inventory
        WHERE store_id = ? AND product_id = ? AND date = ?;
    """, (store_id, product_id, as_of_date))
    inv_row = cursor.fetchone()
    current_stock = int(inv_row["closing_stock"]) if inv_row else 0

    # Calculate 7-day average daily sales
    dt_as_of = datetime.strptime(as_of_date, "%Y-%m-%d")
    date_7d_ago = (dt_as_of - timedelta(days=6)).strftime("%Y-%m-%d")
    date_30d_ago = (dt_as_of - timedelta(days=29)).strftime("%Y-%m-%d")

    cursor.execute("""
        SELECT COALESCE(SUM(quantity), 0) as sold_7d
        FROM sales
        WHERE store_id = ? AND product_id = ? AND date BETWEEN ? AND ?;
    """, (store_id, product_id, date_7d_ago, as_of_date))
    sold_7d = cursor.fetchone()["sold_7d"]
    ads_7d = round(sold_7d / 7.0, 2)

    # Calculate 30-day average daily sales
    cursor.execute("""
        SELECT COALESCE(SUM(quantity), 0) as sold_30d
        FROM sales
        WHERE store_id = ? AND product_id = ? AND date BETWEEN ? AND ?;
    """, (store_id, product_id, date_30d_ago, as_of_date))
    sold_30d = cursor.fetchone()["sold_30d"]
    ads_30d = round(sold_30d / 30.0, 2)

    # Days of Inventory (using 30d ADS as primary demand velocity; fallback to 7d if 30d is 0)
    velocity = ads_30d if ads_30d > 0 else ads_7d
    if velocity > 0:
        days_of_inventory = round(current_stock / velocity, 1)
    else:
        days_of_inventory = 999.0 if current_stock > 0 else 0.0

    cost_price = float(prod["cost_price"])
    selling_price = float(prod["selling_price"])
    inventory_value_at_cost = round(current_stock * cost_price, 2)
    inventory_value_at_retail = round(current_stock * selling_price, 2)

    # Sell-through rate over 30-day window:
    # Sell-Through Rate = Units Sold / (Beginning Stock + Received Stock during period) * 100
    cursor.execute("""
        SELECT opening_stock FROM inventory
        WHERE store_id = ? AND product_id = ? AND date = ?;
    """, (store_id, product_id, date_30d_ago))
    start_inv_row = cursor.fetchone()
    opening_stock_30d = start_inv_row["opening_stock"] if start_inv_row else current_stock

    cursor.execute("""
        SELECT COALESCE(SUM(received_quantity), 0) as received_30d
        FROM inventory
        WHERE store_id = ? AND product_id = ? AND date BETWEEN ? AND ?;
    """, (store_id, product_id, date_30d_ago, as_of_date))
    received_30d = cursor.fetchone()["received_30d"]

    available_pool = opening_stock_30d + received_30d
    if available_pool > 0:
        sell_through_rate = round(min(100.0, (sold_30d / float(available_pool)) * 100.0), 2)
    else:
        sell_through_rate = 0.0 if sold_30d == 0 else 100.0

    reorder_point = int(prod["reorder_point"])
    if current_stock == 0:
        status = "OUT_OF_STOCK"
    elif current_stock <= reorder_point:
        status = "LOW_STOCK"
    elif days_of_inventory > 90.0 and current_stock >= 30:
        status = "OVERSTOCKED"
    else:
        status = "HEALTHY"

    conn.close()

    return InventoryHealth(
        store_id=store_id,
        product_id=product_id,
        product_name=prod["product_name"],
        as_of_date=as_of_date,
        current_stock=current_stock,
        average_daily_sales_7d=ads_7d,
        average_daily_sales_30d=ads_30d,
        days_of_inventory=days_of_inventory,
        inventory_value_at_cost=inventory_value_at_cost,
        inventory_value_at_retail=inventory_value_at_retail,
        sell_through_rate=sell_through_rate,
        reorder_point=reorder_point,
        status=status
    )

def calculate_inventory_turnover(
    product_id: Optional[str] = None,
    store_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> InventoryTurnover:
    """
    Calculates deterministic Inventory Turnover:
    Formula: Inventory Turnover = COGS / Average Inventory Cost
    
    Clearly documents the evaluated date window, calendar days,
    aggregate COGS, mean daily inventory valuation, and annualized turnover.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    if not start_date or not end_date:
        cursor.execute("SELECT MIN(date), MAX(date) FROM inventory")
        min_date, max_date = cursor.fetchone()
        start = start_date or min_date or "2024-06-01"
        end = end_date or max_date or "2024-08-29"
    else:
        start = start_date
        end = end_date

    dt_start = datetime.strptime(start, "%Y-%m-%d")
    dt_end = datetime.strptime(end, "%Y-%m-%d")
    period_days = max(1, (dt_end - dt_start).days + 1)

    # 1. Compute total COGS in period: sum(sales.quantity * products.cost_price)
    cogs_query = """
        SELECT COALESCE(SUM(s.quantity * p.cost_price), 0.0) as total_cogs
        FROM sales s
        JOIN products p ON s.product_id = p.product_id
        WHERE s.date BETWEEN ? AND ?
    """
    cogs_params = [start, end]
    if product_id:
        cogs_query += " AND s.product_id = ?"
        cogs_params.append(product_id)
    if store_id:
        cogs_query += " AND s.store_id = ?"
        cogs_params.append(store_id)

    cursor.execute(cogs_query, cogs_params)
    total_cogs = round(float(cursor.fetchone()["total_cogs"]), 2)

    # 2. Compute Average Inventory Cost:
    # Daily average closing stock valuation across the evaluation period:
    # sum(inventory.closing_stock * products.cost_price) / number_of_days
    inv_query = """
        SELECT COALESCE(SUM(i.closing_stock * p.cost_price), 0.0) as total_inventory_value_sum
        FROM inventory i
        JOIN products p ON i.product_id = p.product_id
        WHERE i.date BETWEEN ? AND ?
    """
    inv_params = [start, end]
    if product_id:
        inv_query += " AND i.product_id = ?"
        inv_params.append(product_id)
    if store_id:
        inv_query += " AND i.store_id = ?"
        inv_params.append(store_id)

    cursor.execute(inv_query, inv_params)
    inv_sum = float(cursor.fetchone()["total_inventory_value_sum"])
    avg_inventory_cost = round(inv_sum / float(period_days), 2)

    # 3. Inventory Turnover = COGS / Average Inventory Cost
    if avg_inventory_cost > 0:
        turnover = round(total_cogs / avg_inventory_cost, 2)
    else:
        turnover = 0.0

    # Annualized turnover = Turnover * (365 / period_days)
    annualized_turnover = round(turnover * (365.0 / float(period_days)), 2)

    period_desc = (
        f"Calculation period from {start} to {end} ({period_days} calendar days). "
        f"COGS (₹{total_cogs:,.2f}) reflects cumulative cost of goods sold during the window. "
        f"Average Inventory Cost (₹{avg_inventory_cost:,.2f}) is the arithmetic mean of daily closing inventory valuations."
    )

    conn.close()

    return InventoryTurnover(
        product_id=product_id,
        store_id=store_id,
        start_date=start,
        end_date=end,
        calculation_period_days=period_days,
        cogs=total_cogs,
        average_inventory_cost=avg_inventory_cost,
        inventory_turnover=turnover,
        annualized_turnover=annualized_turnover,
        calculation_period_description=period_desc
    )

def get_inventory_health_summary(as_of_date: Optional[str] = None) -> Dict[str, Any]:
    """
    Global inventory status summary across the entire network.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    if not as_of_date:
        cursor.execute("SELECT MAX(date) FROM inventory")
        row = cursor.fetchone()
        as_of_date = row[0] if row and row[0] else ""

    cursor.execute("""
        SELECT 
            COUNT(*) as total_inventory_records,
            SUM(CASE WHEN i.closing_stock <= p.reorder_point THEN 1 ELSE 0 END) as low_stock_items,
            SUM(CASE WHEN i.closing_stock = 0 THEN 1 ELSE 0 END) as out_of_stock_items,
            SUM(i.closing_stock) as total_units_in_stock,
            SUM(i.closing_stock * p.cost_price) as total_inventory_valuation
        FROM inventory i
        JOIN products p ON i.product_id = p.product_id
        WHERE i.date = ?;
    """, (as_of_date,))
    row = cursor.fetchone()
    conn.close()
    if row:
        res = dict(row)
        res["as_of_date"] = as_of_date
        return res
    return {
        "as_of_date": as_of_date,
        "total_inventory_records": 0,
        "low_stock_items": 0,
        "out_of_stock_items": 0,
        "total_units_in_stock": 0,
        "total_inventory_valuation": 0.0
    }
