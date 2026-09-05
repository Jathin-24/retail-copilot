"""
Deterministic anomaly detection for sales volume spikes, drops, and stagnant inventory.
Uses deterministic statistical thresholds (Z-score / percentage deviations).
"""
from typing import List, Dict, Any
from src.database.connection import get_db_connection

def detect_sales_spikes_and_drops(threshold_pct: float = 50.0) -> List[Dict[str, Any]]:
    """
    Detects significant deviations in product sales velocity comparing
    recent periods vs historical averages.
    """
    # Deterministic calculation stub for initial architecture
    return []

def detect_overstocked_items(min_days_of_supply: float = 90.0) -> List[Dict[str, Any]]:
    """
    Detects items sitting on excessive inventory with very low inventory turn rates.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            i.store_id,
            s.store_name,
            i.product_id,
            p.product_name,
            i.stock_on_hand,
            i.safety_stock,
            (i.stock_on_hand - i.safety_stock) as excess_units
        FROM inventory i
        JOIN products p ON i.product_id = p.product_id
        JOIN stores s ON i.store_id = s.store_id
        WHERE i.stock_on_hand > (i.safety_stock * 3) AND i.stock_on_hand > 50;
    """)
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]
