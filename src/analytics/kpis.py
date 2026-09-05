"""
Deterministic KPI calculations for retail sales and inventory.
Python performs all arithmetic, aggregation, filtering, and metrics.
Gemini does not perform math.
"""
from typing import Dict, Any, List
import sqlite3
from src.database.connection import get_db_connection

def calculate_store_performance() -> List[Dict[str, Any]]:
    """
    Deterministically computes sales volume, revenue, and product counts per store.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            s.store_id,
            s.store_name,
            s.location,
            COALESCE(SUM(sa.quantity_sold), 0) as total_units_sold,
            COALESCE(SUM(sa.total_revenue), 0.0) as total_revenue
        FROM stores s
        LEFT JOIN sales sa ON s.store_id = sa.store_id
        GROUP BY s.store_id, s.store_name, s.location
        ORDER BY total_revenue DESC;
    """)
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def calculate_product_performance() -> List[Dict[str, Any]]:
    """
    Deterministically aggregates product sales performance, revenue, and gross profit.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            p.product_id,
            p.product_name,
            p.category,
            p.selling_price,
            p.cost_price,
            COALESCE(SUM(s.quantity_sold), 0) as total_units_sold,
            COALESCE(SUM(s.total_revenue), 0.0) as total_revenue,
            COALESCE(SUM(s.quantity_sold * (p.selling_price - p.cost_price)), 0.0) as total_gross_profit
        FROM products p
        LEFT JOIN sales s ON p.product_id = s.product_id
        GROUP BY p.product_id, p.product_name, p.category, p.selling_price, p.cost_price
        ORDER BY total_units_sold DESC;
    """)
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_inventory_health_summary() -> Dict[str, Any]:
    """
    Deterministically scans current stock levels vs safety stocks across stores.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            COUNT(*) as total_inventory_records,
            SUM(CASE WHEN stock_on_hand <= safety_stock THEN 1 ELSE 0 END) as low_stock_items,
            SUM(CASE WHEN stock_on_hand = 0 THEN 1 ELSE 0 END) as out_of_stock_items,
            SUM(stock_on_hand) as total_units_in_stock
        FROM inventory;
    """)
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return {
        "total_inventory_records": 0,
        "low_stock_items": 0,
        "out_of_stock_items": 0,
        "total_units_in_stock": 0
    }
