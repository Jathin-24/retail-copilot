"""
Deterministic anomaly detection for sales volume spikes, drops, overstock, and stagnant inventory.
Uses deterministic statistical thresholds (percentage deviations / days of supply).
"""
from typing import List, Dict, Any
from src.database.connection import get_db_connection
from src.forecasting.demand import calculate_days_of_supply

def detect_sales_spikes_and_drops(threshold_pct: float = 60.0) -> List[Dict[str, Any]]:
    """
    Detects significant deviations in product sales velocity comparing
    the recent 14-day velocity vs the preceding 30-day velocity.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT MAX(date) FROM sales")
    latest_date_row = cursor.fetchone()
    if not latest_date_row or not latest_date_row[0]:
        conn.close()
        return []
    latest_date = latest_date_row[0]

    # Query 14-day window sales vs preceding 30-day window sales
    cursor.execute("""
        SELECT 
            p.product_id,
            p.product_name,
            p.category,
            COALESCE(SUM(CASE WHEN s.date >= date(?, '-14 days') THEN s.quantity ELSE 0 END), 0) / 14.0 as recent_velocity,
            COALESCE(SUM(CASE WHEN s.date < date(?, '-14 days') AND s.date >= date(?, '-44 days') THEN s.quantity ELSE 0 END), 0) / 30.0 as baseline_velocity
        FROM products p
        JOIN sales s ON p.product_id = s.product_id
        GROUP BY p.product_id, p.product_name, p.category
        HAVING baseline_velocity > 0.5 OR recent_velocity > 0.5;
    """, (latest_date, latest_date, latest_date))
    rows = cursor.fetchall()
    conn.close()

    anomalies = []
    for row in rows:
        recent = round(row["recent_velocity"], 2)
        baseline = round(row["baseline_velocity"], 2)
        if baseline == 0:
            if recent >= 2.0:
                anomalies.append({
                    "product_id": row["product_id"],
                    "product_name": row["product_name"],
                    "category": row["category"],
                    "type": "SPIKE",
                    "recent_velocity": recent,
                    "baseline_velocity": baseline,
                    "percentage_change": 100.0,
                    "severity": "HIGH",
                    "description": f"Surge from zero baseline to {recent} units/day."
                })
            continue

        pct_change = round(((recent - baseline) / baseline) * 100.0, 1)
        if pct_change >= threshold_pct:
            anomalies.append({
                "product_id": row["product_id"],
                "product_name": row["product_name"],
                "category": row["category"],
                "type": "SPIKE",
                "recent_velocity": recent,
                "baseline_velocity": baseline,
                "percentage_change": pct_change,
                "severity": "HIGH" if pct_change > 150 else "MEDIUM",
                "description": f"Sales velocity surged by +{pct_change}% ({recent} vs {baseline} units/day)."
            })
        elif pct_change <= -threshold_pct:
            anomalies.append({
                "product_id": row["product_id"],
                "product_name": row["product_name"],
                "category": row["category"],
                "type": "DROP",
                "recent_velocity": recent,
                "baseline_velocity": baseline,
                "percentage_change": pct_change,
                "severity": "HIGH" if pct_change < -75 else "MEDIUM",
                "description": f"Sales velocity collapsed by {pct_change}% ({recent} vs {baseline} units/day)."
            })

    return sorted(anomalies, key=lambda x: abs(x["percentage_change"]), reverse=True)

def detect_overstocked_items(min_days_of_supply: float = 90.0) -> List[Dict[str, Any]]:
    """
    Detects items sitting on excessive inventory with days of supply > min_days_of_supply.
    """
    all_items = calculate_days_of_supply(days_lookback=30)
    overstocked = []
    for item in all_items:
        if item["stock_on_hand"] >= 40 and item["days_of_supply"] >= min_days_of_supply:
            overstocked.append({
                "store_id": item["store_id"],
                "store_name": item["store_name"],
                "product_id": item["product_id"],
                "product_name": item["product_name"],
                "category": item["category"],
                "stock_on_hand": item["stock_on_hand"],
                "daily_velocity": item["daily_velocity"],
                "days_of_supply": item["days_of_supply"],
                "reason": f"Excessive inventory runway of {item['days_of_supply']} days."
            })
    return sorted(overstocked, key=lambda x: x["days_of_supply"], reverse=True)

def detect_slow_moving_items(max_velocity: float = 0.20) -> List[Dict[str, Any]]:
    """
    Detects slow-moving or stagnant items with stock on hand but velocity <= max_velocity.
    """
    all_items = calculate_days_of_supply(days_lookback=30)
    slow = []
    for item in all_items:
        if item["stock_on_hand"] > 10 and item["daily_velocity"] <= max_velocity:
            slow.append({
                "store_id": item["store_id"],
                "store_name": item["store_name"],
                "product_id": item["product_id"],
                "product_name": item["product_name"],
                "category": item["category"],
                "stock_on_hand": item["stock_on_hand"],
                "daily_velocity": item["daily_velocity"],
                "days_of_supply": item["days_of_supply"],
                "is_dead_stock": item["daily_velocity"] == 0.0
            })
    return sorted(slow, key=lambda x: x["stock_on_hand"], reverse=True)
