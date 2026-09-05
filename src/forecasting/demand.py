"""
Deterministic demand forecasting and inventory runway modeling.
All numerical extrapolations are computed deterministically in Python.
"""
from typing import List, Dict, Any
from src.database.connection import get_db_connection

def calculate_days_of_supply(days_lookback: int = 30) -> List[Dict[str, Any]]:
    """
    Computes average daily sales velocity (units/day) and remaining days of supply.
    Formula: Days of Supply = Stock on Hand / Average Daily Sales Velocity
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    # Calculates daily velocity over recent window
    cursor.execute("""
        SELECT 
            i.store_id,
            i.product_id,
            p.product_name,
            s.store_name,
            i.stock_on_hand,
            COALESCE(SUM(sa.quantity_sold), 0) as total_sold_in_period
        FROM inventory i
        JOIN products p ON i.product_id = p.product_id
        JOIN stores s ON i.store_id = s.store_id
        LEFT JOIN sales sa ON i.store_id = sa.store_id 
                           AND i.product_id = sa.product_id
        GROUP BY i.store_id, i.product_id, p.product_name, s.store_name, i.stock_on_hand;
    """)
    rows = cursor.fetchall()
    conn.close()

    results = []
    lookback = max(1, days_lookback)
    for row in rows:
        sold = row["total_sold_in_period"]
        stock = row["stock_on_hand"]
        daily_velocity = round(sold / float(lookback), 2)
        days_of_supply = round(stock / daily_velocity, 1) if daily_velocity > 0 else 999.0
        
        results.append({
            "store_id": row["store_id"],
            "store_name": row["store_name"],
            "product_id": row["product_id"],
            "product_name": row["product_name"],
            "stock_on_hand": stock,
            "daily_velocity": daily_velocity,
            "days_of_supply": days_of_supply
        })
    return results

def project_stockout_dates() -> List[Dict[str, Any]]:
    """
    Identifies products with critically low runway (< 7 days of supply).
    """
    runway = calculate_days_of_supply()
    at_risk = [item for item in runway if item["days_of_supply"] < 7.0 and item["daily_velocity"] > 0]
    return sorted(at_risk, key=lambda x: x["days_of_supply"])
