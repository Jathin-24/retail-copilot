"""
Deterministic identification of slow-moving inventory.
Rules:
1. Sales velocity is below a configurable threshold (e.g. <= 0.20 units/day over 30 days)
2. Current inventory remains above a configurable threshold (e.g. >= 15 units on hand)
3. Product is NOT newly launched (active in catalog for >= min_catalog_age_days)
Returns comprehensive source evidence for every flagged item.
"""
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from src.database.connection import get_db_connection
from src.analytics.models import SlowMoverResult

def detect_slow_moving_products(
    sales_threshold_daily: float = 0.20,
    inventory_threshold_units: int = 15,
    min_catalog_age_days: int = 21,
    as_of_date: Optional[str] = None,
    store_id: Optional[str] = None
) -> List[SlowMoverResult]:
    """
    Deterministically scans product inventory across stores to flag slow-moving items
    with documented quantitative evidence.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    if not as_of_date:
        cursor.execute("SELECT MAX(date) FROM inventory")
        row = cursor.fetchone()
        as_of_date = row[0] if row and row[0] else "2024-08-29"

    dt_as_of = datetime.strptime(as_of_date, "%Y-%m-%d")
    date_30d_ago = (dt_as_of - timedelta(days=29)).strftime("%Y-%m-%d")

    # Find earliest recorded date per product in the entire system to determine catalog age
    cursor.execute("SELECT product_id, MIN(date) as first_date FROM inventory GROUP BY product_id")
    first_dates = {r["product_id"]: r["first_date"] for r in cursor.fetchall()}

    # Query current closing stock on as_of_date and 30-day sales volume
    query = """
        SELECT 
            i.store_id,
            st.store_name,
            i.product_id,
            p.product_name,
            p.category,
            p.cost_price,
            p.selling_price,
            i.closing_stock as current_stock,
            COALESCE(SUM(sa.quantity), 0) as units_sold_30d
        FROM inventory i
        JOIN stores st ON i.store_id = st.store_id
        JOIN products p ON i.product_id = p.product_id
        LEFT JOIN sales sa ON i.store_id = sa.store_id 
                           AND i.product_id = sa.product_id 
                           AND sa.date BETWEEN ? AND ?
        WHERE i.date = ?
    """
    params = [date_30d_ago, as_of_date, as_of_date]
    if store_id:
        query += " AND i.store_id = ?"
        params.append(store_id)
    query += " GROUP BY i.store_id, st.store_name, i.product_id, p.product_name, p.category, p.cost_price, p.selling_price, i.closing_stock"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    results: List[SlowMoverResult] = []

    for r in rows:
        prod_id = r["product_id"]
        current_stock = int(r["current_stock"])
        units_sold = int(r["units_sold_30d"])
        daily_velocity = round(units_sold / 30.0, 2)
        days_of_inventory = round(current_stock / daily_velocity, 1) if daily_velocity > 0 else 999.0

        first_date = first_dates.get(prod_id, as_of_date)
        dt_first = datetime.strptime(first_date, "%Y-%m-%d")
        catalog_age_days = (dt_as_of - dt_first).days + 1
        is_new_launch = catalog_age_days < min_catalog_age_days

        # Deterministic criteria evaluation
        sales_condition = daily_velocity <= sales_threshold_daily
        inventory_condition = current_stock >= inventory_threshold_units
        age_condition = not is_new_launch

        if sales_condition and inventory_condition and age_condition:
            evidence = {
                "sales_threshold_daily": sales_threshold_daily,
                "actual_daily_velocity": daily_velocity,
                "units_sold_30d": units_sold,
                "inventory_threshold_units": inventory_threshold_units,
                "actual_stock": current_stock,
                "min_catalog_age_days": min_catalog_age_days,
                "actual_catalog_age_days": catalog_age_days,
                "first_recorded_date": first_date,
                "evaluation_window": f"{date_30d_ago} to {as_of_date} (30 days)",
                "holding_value_cost": round(current_stock * float(r["cost_price"]), 2),
                "holding_value_retail": round(current_stock * float(r["selling_price"]), 2),
                "rule_justification": (
                    f"Product '{r['product_name']}' has been active for {catalog_age_days} days (not newly launched). "
                    f"Over the last 30 days, it sold only {units_sold} units ({daily_velocity} units/day <= threshold {sales_threshold_daily}), "
                    f"yet maintains {current_stock} units in inventory (>= threshold {inventory_threshold_units}), "
                    f"representing {days_of_inventory} days of supply."
                )
            }

            results.append(SlowMoverResult(
                product_id=prod_id,
                product_name=r["product_name"],
                category=r["category"],
                store_id=r["store_id"],
                store_name=r["store_name"],
                current_stock=current_stock,
                daily_sales_velocity=daily_velocity,
                units_sold_in_period=units_sold,
                days_of_inventory=days_of_inventory,
                first_recorded_date=first_date,
                catalog_age_days=catalog_age_days,
                is_newly_launched=is_new_launch,
                is_slow_moving=True,
                evidence=evidence
            ))

    # Sort by lowest velocity, then highest inventory valuation
    return sorted(results, key=lambda x: (x.daily_sales_velocity, -x.current_stock))
