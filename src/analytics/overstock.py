"""
Deterministic identification of overstocked products.
Calculates Days of Inventory vs target runway, estimating excess units
and locked-up capital at cost price.
"""
from datetime import datetime, timedelta
import math
from typing import List, Dict, Any, Optional
from src.database.connection import get_db_connection
from src.analytics.models import OverstockResult

def detect_overstocked_products(
    target_days: float = 45.0,
    min_stock: int = 25,
    as_of_date: Optional[str] = None,
    store_id: Optional[str] = None
) -> List[OverstockResult]:
    """
    Deterministically flags inventory records where Days of Inventory exceeds the target runway.
    Computes excess units above target stock and capital tied up in excess inventory.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    if not as_of_date:
        cursor.execute("SELECT MAX(date) FROM inventory")
        row = cursor.fetchone()
        as_of_date = row[0] if row and row[0] else "2024-08-29"

    dt_as_of = datetime.strptime(as_of_date, "%Y-%m-%d")
    date_30d_ago = (dt_as_of - timedelta(days=29)).strftime("%Y-%m-%d")

    query = """
        SELECT 
            i.store_id,
            st.store_name,
            i.product_id,
            p.sku,
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
    query += " GROUP BY i.store_id, st.store_name, i.product_id, p.sku, p.product_name, p.category, p.cost_price, p.selling_price, i.closing_stock"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    results: List[OverstockResult] = []

    for r in rows:
        current_stock = int(r["current_stock"])
        if current_stock < min_stock:
            continue

        units_sold = int(r["units_sold_30d"])
        demand_velocity = round(units_sold / 30.0, 2)

        if demand_velocity > 0:
            days_of_inventory = round(current_stock / demand_velocity, 1)
            target_stock = math.ceil(demand_velocity * target_days)
            excess_units = max(0, current_stock - target_stock)
        else:
            days_of_inventory = 999.0
            target_stock = 0
            excess_units = current_stock

        if days_of_inventory > target_days and excess_units > 0:
            cost_price = float(r["cost_price"])
            excess_value = round(excess_units * cost_price, 2)

            evidence = {
                "evaluation_date": as_of_date,
                "sales_lookback_window": f"{date_30d_ago} to {as_of_date} (30 days)",
                "target_runway_days": target_days,
                "actual_days_of_inventory": days_of_inventory,
                "demand_velocity_units_per_day": demand_velocity,
                "optimal_stock_level": target_stock,
                "excess_units": excess_units,
                "unit_cost_inr": cost_price,
                "capital_tied_up_inr": excess_value,
                "rationale": (
                    f"At current demand velocity of {demand_velocity} units/day, target runway of {target_days} days "
                    f"requires {target_stock} units. Store holds {current_stock} units ({days_of_inventory} days of supply), "
                    f"resulting in {excess_units} excess units (₹{excess_value:,.2f} at cost)."
                )
            }

            results.append(OverstockResult(
                product_id=r["product_id"],
                product_name=r["product_name"],
                sku=r["sku"],
                category=r["category"],
                store_id=r["store_id"],
                store_name=r["store_name"],
                current_stock=current_stock,
                demand_velocity=demand_velocity,
                days_of_inventory=days_of_inventory,
                target_days=target_days,
                target_stock=target_stock,
                excess_units_estimate=excess_units,
                excess_inventory_value=excess_value,
                unit_cost=cost_price,
                evidence=evidence
            ))

    return sorted(results, key=lambda x: x.excess_inventory_value, reverse=True)
