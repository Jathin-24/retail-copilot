"""
Deterministic action recommendation engine.
Generates concrete business actions:
- Purchase Orders (Reorders) with supplier lead time & MOQ enforcement.
- Inter-Store Transfers (balancing stockout risk at Store A with overstock at Store B).
- Markdown / Clearance Actions (for stagnant or dead inventory).
"""
from typing import List, Dict, Any
from collections import defaultdict
from src.database.connection import get_db_connection
from src.forecasting.demand import calculate_days_of_supply

def generate_action_recommendations() -> List[Dict[str, Any]]:
    """
    Generates actionable, policy-compliant replenishment, transfer, and markdown recommendations.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT MAX(date) FROM inventory")
    latest_date = cursor.fetchone()[0]

    # Query current stock, ROP, supplier details
    cursor.execute("""
        SELECT 
            i.store_id,
            s.store_name,
            i.product_id,
            p.product_name,
            p.category,
            i.closing_stock,
            p.reorder_point,
            p.cost_price,
            sup.supplier_id,
            sup.supplier_name,
            sup.lead_time_days,
            sup.minimum_order_quantity
        FROM inventory i
        JOIN products p ON i.product_id = p.product_id
        JOIN stores s ON i.store_id = s.store_id
        JOIN suppliers sup ON p.supplier_id = sup.supplier_id
        WHERE i.date = ?;
    """, (latest_date,))
    rows = cursor.fetchall()
    conn.close()

    actions = []
    
    # Track inventory per product across stores for inter-store transfer opportunities
    stock_by_product = defaultdict(list)
    for r in rows:
        stock_by_product[r["product_id"]].append(r)

    # 1. Check for Reorders (Reorder Point Breach)
    for r in rows:
        stock = r["closing_stock"]
        rop = r["reorder_point"]
        moq = r["minimum_order_quantity"]
        
        if stock <= rop:
            needed = max(moq, (rop * 3) - stock)
            priority = "CRITICAL" if stock <= 5 else ("HIGH" if stock <= (rop // 2) else "MEDIUM")
            actions.append({
                "type": "PURCHASE_ORDER",
                "priority": priority,
                "store_id": r["store_id"],
                "store_name": r["store_name"],
                "product_id": r["product_id"],
                "product_name": r["product_name"],
                "current_stock": stock,
                "reorder_point": rop,
                "recommended_quantity": needed,
                "supplier_name": r["supplier_name"],
                "lead_time_days": r["lead_time_days"],
                "estimated_cost_inr": round(needed * r["cost_price"], 2),
                "rationale": f"Stock ({stock}) is below reorder point ({rop}). Recommended order rounds to MOQ ({moq})."
            })

    # 2. Check for Inter-Store Transfer Opportunities (Cross-Store Rebalancing)
    for prod_id, store_records in stock_by_product.items():
        deficit_stores = [rec for rec in store_records if rec["closing_stock"] <= 5]
        surplus_stores = [rec for rec in store_records if rec["closing_stock"] > (rec["reorder_point"] * 2.5) and rec["closing_stock"] >= 40]

        for def_rec in deficit_stores:
            for sur_rec in surplus_stores:
                if def_rec["store_id"] != sur_rec["store_id"]:
                    transfer_qty = min(30, sur_rec["closing_stock"] - sur_rec["reorder_point"])
                    if transfer_qty >= 10:
                        actions.append({
                            "type": "INTER_STORE_TRANSFER",
                            "priority": "HIGH",
                            "origin_store": sur_rec["store_name"],
                            "destination_store": def_rec["store_name"],
                            "origin_store_id": sur_rec["store_id"],
                            "destination_store_id": def_rec["store_id"],
                            "product_id": prod_id,
                            "product_name": def_rec["product_name"],
                            "transfer_quantity": transfer_qty,
                            "lead_time_days": 1, # regional transfer
                            "rationale": f"Fast balancing: Transfer {transfer_qty} units from surplus store ({sur_rec['store_name']}: {sur_rec['closing_stock']} units) to stockout risk store ({def_rec['store_name']}: {def_rec['closing_stock']} units)."
                        })
                        break # Pair with first available surplus store

    return actions
