"""
Deterministic stockout risk assessment using a transparent baseline supply-chain model.
Formulas:
- lead_time_demand = average_daily_demand * supplier_lead_time_days
- reorder_requirement = lead_time_demand + safety_stock
- inventory_position = current_stock + incoming_open_purchase_orders - reserved_quantity
- risk considers whether inventory_position is insufficient for expected demand during lead time.
Note: risk_score is a deterministic heuristic on a 0-100 scale, NOT a calibrated probability.
"""
from datetime import datetime, timedelta
import math
from typing import List, Dict, Any, Optional
from src.database.connection import get_db_connection
from src.analytics.models import StockoutRiskResult

def calculate_stockout_risk(
    store_id: str,
    product_id: str,
    as_of_date: Optional[str] = None,
    lookback_days: int = 14
) -> StockoutRiskResult:
    """
    Deterministically computes stockout risk metrics for a single store-product pair.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    if not as_of_date:
        cursor.execute("SELECT MAX(date) FROM inventory")
        row = cursor.fetchone()
        as_of_date = row[0] if row and row[0] else "2024-08-29"

    dt_as_of = datetime.strptime(as_of_date, "%Y-%m-%d")
    date_lookback = (dt_as_of - timedelta(days=lookback_days - 1)).strftime("%Y-%m-%d")

    # Product and supplier lead time
    cursor.execute("""
        SELECT 
            p.product_id, 
            p.product_name, 
            p.reorder_point,
            s.lead_time_days,
            st.store_name
        FROM products p
        JOIN suppliers s ON p.supplier_id = s.supplier_id
        JOIN stores st ON st.store_id = ?
        WHERE p.product_id = ?;
    """, (store_id, product_id))
    meta = cursor.fetchone()
    if not meta:
        conn.close()
        raise ValueError(f"Product '{product_id}' or Store '{store_id}' not found.")

    lead_time_days = int(meta["lead_time_days"])
    store_name = meta["store_name"]
    product_name = meta["product_name"]

    # Current closing stock
    cursor.execute("""
        SELECT closing_stock FROM inventory
        WHERE store_id = ? AND product_id = ? AND date = ?;
    """, (store_id, product_id, as_of_date))
    inv_row = cursor.fetchone()
    current_stock = int(inv_row["closing_stock"]) if inv_row else 0

    # Demand velocity (average daily demand over lookback window)
    cursor.execute("""
        SELECT date, SUM(quantity) as daily_sold
        FROM sales
        WHERE store_id = ? AND product_id = ? AND date BETWEEN ? AND ?
        GROUP BY date;
    """, (store_id, product_id, date_lookback, as_of_date))
    daily_sales_rows = cursor.fetchall()
    daily_sales_map = {r["date"]: r["daily_sold"] for r in daily_sales_rows}

    # Populate all lookback days to compute empirical daily sales mean and standard deviation
    daily_values = []
    curr_iter = dt_as_of - timedelta(days=lookback_days - 1)
    while curr_iter <= dt_as_of:
        d_str = curr_iter.strftime("%Y-%m-%d")
        daily_values.append(daily_sales_map.get(d_str, 0))
        curr_iter += timedelta(days=1)

    total_sold_lookback = sum(daily_values)
    demand_velocity = round(total_sold_lookback / float(lookback_days), 2)

    # Adjust for stockout-induced zero sales:
    # If recent sales are zero but product has historical demand and was out-of-stock,
    # compute unconstrained demand velocity from in-stock active days.
    stockout_adjusted = False
    if demand_velocity == 0:
        cursor.execute("""
            SELECT 
                COALESCE(SUM(sold_quantity), 0) as total_sold,
                COUNT(CASE WHEN (closing_stock > 0 OR sold_quantity > 0) THEN 1 END) as in_stock_days
            FROM inventory
            WHERE store_id = ? AND product_id = ? AND date <= ?;
        """, (store_id, product_id, as_of_date))
        hist = cursor.fetchone()
        if hist and hist["in_stock_days"] and hist["in_stock_days"] > 0 and hist["total_sold"] > 0:
            demand_velocity = round(hist["total_sold"] / float(hist["in_stock_days"]), 2)
            stockout_adjusted = True

    # Calculate standard deviation of daily demand
    if lookback_days > 1 and not stockout_adjusted:
        variance = sum((v - demand_velocity) ** 2 for v in daily_values) / float(lookback_days - 1)
        std_dev = math.sqrt(variance)
    else:
        std_dev = max(1.0, round(demand_velocity * 0.3, 2))

    # Lead Time Demand = average_daily_demand * supplier_lead_time_days
    lead_time_demand = round(demand_velocity * lead_time_days, 2)

    # Safety Stock: transparent calculation using service factor z=1.65 (95% service level)
    # safety_stock = ceil(z * std_dev * sqrt(lead_time_days))
    if demand_velocity > 0:
        stat_ss = 1.65 * std_dev * math.sqrt(lead_time_days)
        safety_stock = float(max(2, math.ceil(stat_ss)))
    else:
        safety_stock = 0.0

    # Reorder Requirement = lead_time_demand + safety_stock
    reorder_requirement = round(lead_time_demand + safety_stock, 2)

    # Incoming open purchase orders
    cursor.execute("""
        SELECT COALESCE(SUM(ordered_quantity - received_quantity), 0) as incoming_qty
        FROM purchase_orders
        WHERE store_id = ? AND product_id = ? AND status IN ('ORDERED', 'PENDING', 'IN_TRANSIT');
    """, (store_id, product_id))
    incoming_quantity = int(cursor.fetchone()["incoming_qty"])

    # Reserved quantity (baseline = 0)
    reserved_quantity = 0

    # Inventory Position = current_stock + incoming_open_purchase_orders - reserved_quantity
    inventory_position = float(current_stock + incoming_quantity - reserved_quantity)

    # Days of Inventory
    if demand_velocity > 0:
        days_of_inventory = round(current_stock / demand_velocity, 1)
    else:
        days_of_inventory = 999.0 if current_stock > 0 else 0.0

    # Risk evaluation
    explanation_factors = []
    if stockout_adjusted:
        explanation_factors.append(
            f"Adjusted for stockout-induced zero sales: recent 14-day sales were 0 due to stockout. "
            f"Unconstrained demand velocity is estimated at {demand_velocity} units/in-stock day based on historical sales."
        )
    explanation_factors.append(f"Current on-hand stock: {current_stock} units ({days_of_inventory} days of supply).")
    explanation_factors.append(f"Recent demand velocity: {demand_velocity} units/day (over past {lookback_days} days).")
    explanation_factors.append(f"Supplier lead time: {lead_time_days} days. Expected lead-time demand: {lead_time_demand} units.")
    explanation_factors.append(f"Calculated safety stock buffer: {safety_stock} units. Total reorder requirement: {reorder_requirement} units.")
    explanation_factors.append(f"Incoming open purchase orders: {incoming_quantity} units. Net inventory position: {inventory_position} units.")
    explanation_factors.append("Note: Risk score is a deterministic heuristic index (0-100 scale), not a calibrated probability.")

    if demand_velocity == 0:
        risk_level = "LOW"
        risk_score = 0.0
        explanation_factors.append("Zero sales velocity observed; no stockout risk.")
    elif current_stock == 0:
        risk_level = "CRITICAL"
        if incoming_quantity == 0:
            risk_score = 100.0
            explanation_factors.append("URGENT: Stock is currently 0 with zero open purchase orders in pipeline.")
        else:
            risk_score = 88.0
            explanation_factors.append(f"Stock is currently 0, but {incoming_quantity} units are in transit.")
    elif inventory_position < lead_time_demand:
        risk_level = "CRITICAL"
        deficit = lead_time_demand - inventory_position
        pct_deficit = deficit / max(1.0, lead_time_demand)
        risk_score = min(98.0, round(60.0 + (pct_deficit * 35.0), 1))
        explanation_factors.append(
            f"CRITICAL: Net inventory position ({inventory_position}) is less than expected lead-time demand ({lead_time_demand}). "
            f"Stockout is projected before incoming shipments can arrive."
        )
    elif inventory_position < reorder_requirement:
        risk_level = "HIGH" if days_of_inventory <= lead_time_days else "MEDIUM"
        deficit = reorder_requirement - inventory_position
        pct_deficit = deficit / max(1.0, reorder_requirement)
        risk_score = min(80.0, round(30.0 + (pct_deficit * 45.0), 1))
        explanation_factors.append(
            f"WARNING: Inventory position ({inventory_position}) is below the reorder requirement ({reorder_requirement}), "
            f"eroding the {safety_stock}-unit safety buffer."
        )
    else:
        risk_level = "LOW"
        risk_score = max(0.0, round(15.0 * max(0.0, 1.0 - (inventory_position / max(1.0, reorder_requirement * 2.0))), 1))
        explanation_factors.append(f"Inventory position ({inventory_position}) provides adequate coverage for lead-time demand plus safety stock.")

    conn.close()

    return StockoutRiskResult(
        store_id=store_id,
        store_name=store_name,
        product_id=product_id,
        product_name=product_name,
        current_stock=current_stock,
        demand_velocity=demand_velocity,
        lead_time_days=lead_time_days,
        lead_time_demand=lead_time_demand,
        safety_stock=safety_stock,
        incoming_quantity=incoming_quantity,
        inventory_position=inventory_position,
        days_of_inventory=days_of_inventory,
        risk_level=risk_level,
        risk_score=risk_score,
        explanation_factors=explanation_factors
    )

def assess_all_stockout_risks(
    store_id: Optional[str] = None,
    as_of_date: Optional[str] = None,
    min_risk_score: float = 20.0
) -> List[StockoutRiskResult]:
    """
    Evaluates stockout risk across all store-product combinations.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    if not as_of_date:
        cursor.execute("SELECT MAX(date) FROM inventory")
        row = cursor.fetchone()
        as_of_date = row[0] if row and row[0] else "2024-08-29"

    query = "SELECT DISTINCT store_id, product_id FROM inventory WHERE date = ?"
    params = [as_of_date]
    if store_id:
        query += " AND store_id = ?"
        params.append(store_id)

    cursor.execute(query, params)
    pairs = cursor.fetchall()
    conn.close()

    results = []
    for p in pairs:
        res = calculate_stockout_risk(p["store_id"], p["product_id"], as_of_date=as_of_date)
        if res.risk_score >= min_risk_score:
            results.append(res)

    return sorted(results, key=lambda x: x.risk_score, reverse=True)
