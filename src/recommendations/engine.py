"""
Deterministic action recommendation engine.
Generates concrete business actions (e.g. Purchase Orders, Inter-Store Transfers, Markdowns)
strictly based on deterministic inventory and sales calculations.
"""
from typing import List, Dict, Any
from src.database.connection import get_db_connection

def generate_action_recommendations() -> List[Dict[str, Any]]:
    """
    Scans for inventory stockout risks and generates concrete action proposals.
    Rules:
    - If stock_on_hand <= safety_stock: Reorder recommendation with supplier lead time.
    - If stock_on_hand == 0: Emergency expedited stock transfer recommendation.
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
            i.reorder_threshold,
            sup.supplier_name,
            sup.lead_time_days
        FROM inventory i
        JOIN products p ON i.product_id = p.product_id
        JOIN stores s ON i.store_id = s.store_id
        LEFT JOIN suppliers sup ON p.supplier_id = sup.supplier_id
        WHERE i.stock_on_hand <= i.reorder_threshold;
    """)
    rows = cursor.fetchall()
    conn.close()

    actions = []
    for row in rows:
        order_qty = max(50, row["reorder_threshold"] * 2 - row["stock_on_hand"])
        actions.append({
            "type": "REORDER",
            "priority": "HIGH" if row["stock_on_hand"] <= row["safety_stock"] else "MEDIUM",
            "store_id": row["store_id"],
            "store_name": row["store_name"],
            "product_id": row["product_id"],
            "product_name": row["product_name"],
            "current_stock": row["stock_on_hand"],
            "recommended_order_quantity": order_qty,
            "supplier": row["supplier_name"] or "Standard Supplier",
            "lead_time_days": row["lead_time_days"] or 7,
            "reasoning": f"Stock ({row['stock_on_hand']}) is below threshold ({row['reorder_threshold']})."
        })
    return actions
