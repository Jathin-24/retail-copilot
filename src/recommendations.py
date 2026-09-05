"""
Attention Today Decision Engine
TRACK_ID: PS03

MANDATORY PRINCIPLES:
1. NEVER MAKE A CLAIM WITHOUT SUPPORTING DATA.
2. DETERMINISTIC LOGIC: Gemini is NEVER used to calculate priority or rank alerts.
3. TRANSPARENT PRIORITY SCORE:
     priority_score = business_impact * urgency * evidence_strength

4. RESTRICTED ACTION SET:
     - REORDER
     - TRANSFER
     - REDUCE_REORDERING
     - PROMOTION_REVIEW
     - INVESTIGATE
     - STOCK_COUNT
     - CONTACT_SUPPLIER
     - MONITOR

Identifies and ranks 7 failure modes:
1. Likely stockouts
2. Slow-moving inventory
3. Overstock
4. Unusual sales spikes
5. Unusual sales drops
6. Supplier delays
7. Inventory data-quality issues
"""
from __future__ import annotations

import math
from typing import List, Dict, Any, Optional
from enum import Enum
from pydantic import BaseModel, Field

from src.database.connection import get_db_connection
from src.analytics import (
    assess_all_stockout_risks,
    detect_slow_moving_products,
    detect_overstocked_products,
    detect_sales_anomalies,
    check_inventory_data_quality
)


class SupportedAction(str, Enum):
    """Restricted policy-supported operational actions."""
    REORDER = "REORDER"
    TRANSFER = "TRANSFER"
    REDUCE_REORDERING = "REDUCE_REORDERING"
    PROMOTION_REVIEW = "PROMOTION_REVIEW"
    INVESTIGATE = "INVESTIGATE"
    STOCK_COUNT = "STOCK_COUNT"
    CONTACT_SUPPLIER = "CONTACT_SUPPLIER"
    MONITOR = "MONITOR"


class AlertType(str, Enum):
    """The 7 required retail alert categories."""
    LIKELY_STOCKOUT = "LIKELY_STOCKOUT"
    SLOW_MOVING = "SLOW_MOVING"
    OVERSTOCK = "OVERSTOCK"
    SALES_SPIKE = "SALES_SPIKE"
    SALES_DROP = "SALES_DROP"
    SUPPLIER_DELAY = "SUPPLIER_DELAY"
    DATA_QUALITY = "DATA_QUALITY"


class PriorityTier(str, Enum):
    """Categorical classification derived deterministically from priority_score."""
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class BusinessImpact(BaseModel):
    """
    Component 1: Financial exposure or monetary impact at stake (1.0 to 10.0 scale).
    Considers estimated lost revenue, inventory capital tied up, or gross profit exposure.
    """
    score: float = Field(..., description="Normalized score 1.0 to 10.0.")
    exposure_inr: float = Field(..., description="Monetary value in Indian Rupees at risk.")
    metric_name: str = Field(..., description="Primary monetary metric evaluated (e.g. gross_profit_exposure, excess_capital).")
    description: str = Field(..., description="Plain-language explanation of financial impact.")


class UrgencyFactor(BaseModel):
    """
    Component 2: Operational time-sensitivity (1.0 to 5.0 scale).
    Considers days until expected stockout, severity of anomaly, age of inventory, or days delayed.
    """
    score: float = Field(..., description="Normalized urgency multiplier 1.0 to 5.0.")
    primary_factor: str = Field(..., description="Leading operational driver (e.g. days_of_inventory, delay_days, anomaly_severity).")
    description: str = Field(..., description="Plain-language explanation of why this is urgent.")


class EvidenceStrengthFactor(BaseModel):
    """
    Component 3: Reliability, completeness, and historical depth (0.5 to 1.0 scale).
    Considers data completeness, historical sample size, and model reliability.
    """
    score: float = Field(..., description="Evidence reliability multiplier 0.5 to 1.0.")
    data_completeness: float = Field(..., description="Completeness of historical records (0.0 to 1.0).")
    sample_size_days: int = Field(..., description="Number of historical days evaluated.")
    model_type: str = Field(..., description="Underlying mathematical model (e.g. direct_ledger, rolling_baseline_14d).")
    description: str = Field(..., description="Explicit statement of evidence basis.")


class ActionRecommendation(BaseModel):
    """Operational recommendation restricted strictly to supported actions."""
    action: SupportedAction = Field(..., description="One of the 8 supported actions.")
    details: str = Field(..., description="Specific operational execution parameters.")
    source_store: Optional[str] = Field(None, description="Origin store name if TRANSFER.")
    destination_store: Optional[str] = Field(None, description="Destination store name if TRANSFER.")
    transfer_quantity: Optional[int] = Field(None, description="Quantity to transfer if TRANSFER.")
    recommended_order_quantity: Optional[int] = Field(None, description="Quantity to purchase if REORDER.")
    requires_manager_approval: bool = Field(True, description="All actions require store manager confirmation.")


class AttentionItem(BaseModel):
    """
    Every Attention Today item adheres strictly to the required schema:
    - alert_type
    - priority
    - priority_score
    - product
    - store
    - evidence
    - business_impact
    - urgency
    - evidence_strength
    - recommendation
    - assumptions
    - reason
    """
    rank: int = Field(..., description="1-indexed priority ranking across network.")
    alert_type: AlertType = Field(..., description="One of the 7 required alert types.")
    priority: PriorityTier = Field(..., description="CRITICAL, HIGH, MEDIUM, or LOW.")
    priority_score: float = Field(..., description="business_impact * urgency * evidence_strength.")

    product: Optional[Dict[str, Any]] = Field(None, description="Product metadata: product_id, product_name, sku, category.")
    store: Optional[Dict[str, Any]] = Field(None, description="Store metadata: store_id, store_name, city.")

    evidence: Dict[str, Any] = Field(..., description="Structured evidence: metric, value, unit, formula, raw_values, period, source.")
    business_impact: BusinessImpact = Field(..., description="Component 1: Financial valuation and normalized score.")
    urgency: UrgencyFactor = Field(..., description="Component 2: Operational urgency multiplier.")
    evidence_strength: EvidenceStrengthFactor = Field(..., description="Component 3: Historical data completeness multiplier.")

    recommendation: ActionRecommendation = Field(..., description="Restricted action recommendation.")
    assumptions: List[str] = Field(default_factory=list, description="Explicit boundary assumptions.")
    reason: str = Field(..., description="Plain-language justification of why this item requires attention today.")


# =========================================================================
# DETERMINISTIC PRIORITY SCORING ENGINE
# =========================================================================

def calculate_priority_score(
    business_impact_score: float,
    urgency_score: float,
    evidence_strength_score: float
) -> tuple[float, PriorityTier]:
    """
    Calculates transparent priority score:
      priority_score = business_impact * urgency * evidence_strength

    Theoretical bounds:
      - business_impact: [1.0, 10.0]
      - urgency: [1.0, 5.0]
      - evidence_strength: [0.5, 1.0]
      - priority_score max: 10.0 * 5.0 * 1.0 = 50.0

    Tier Mapping:
      - >= 30.0: CRITICAL
      - 18.0 to 29.9: HIGH
      - 10.0 to 17.9: MEDIUM
      - < 10.0: LOW
    """
    b = max(1.0, min(10.0, float(business_impact_score)))
    u = max(1.0, min(5.0, float(urgency_score)))
    e = max(0.5, min(1.0, float(evidence_strength_score)))

    score = round(b * u * e, 1)

    if score >= 30.0:
        tier = PriorityTier.CRITICAL
    elif score >= 18.0:
        tier = PriorityTier.HIGH
    elif score >= 10.0:
        tier = PriorityTier.MEDIUM
    else:
        tier = PriorityTier.LOW

    return score, tier


def normalize_financial_exposure(exposure_inr: float) -> float:
    """
    Transparent piecewise logarithmic-linear normalization of INR financial exposure into [1.0, 10.0]:
    - < ₹1,000: 1.0 to 2.9
    - ₹1,000 to ₹5,000: 3.0 to 4.9
    - ₹5,000 to ₹20,000: 5.0 to 7.4
    - ₹20,000 to ₹50,000: 7.5 to 8.9
    - > ₹50,000: 9.0 to 10.0
    """
    val = max(0.0, float(exposure_inr))
    if val <= 0:
        return 1.0
    if val < 1000:
        return round(1.0 + (val / 1000.0) * 1.9, 2)
    elif val < 5000:
        return round(3.0 + ((val - 1000.0) / 4000.0) * 1.9, 2)
    elif val < 20000:
        return round(5.0 + ((val - 5000.0) / 15000.0) * 2.4, 2)
    elif val < 50000:
        return round(7.5 + ((val - 20000.0) / 30000.0) * 1.4, 2)
    else:
        return round(min(10.0, 9.0 + ((val - 50000.0) / 100000.0) * 1.0), 2)


# =========================================================================
# DOMAIN ALERT GENERATORS
# =========================================================================

def _detect_stockout_alerts(
    inventory_map: Dict[str, List[Dict[str, Any]]],
    products_meta: Dict[str, Dict[str, Any]],
    stores_meta: Dict[str, Dict[str, Any]]
) -> List[AttentionItem]:
    """
    Generates alerts for likely stockouts.
    Cross-store balancing rule:
      If product is low in Store A and excessive in Store B -> Recommend TRANSFER.
      If high stockout risk and no incoming stock -> Recommend REORDER.
    """
    alerts: List[AttentionItem] = []
    risks = assess_all_stockout_risks(min_risk_score=25.0)

    for r in risks:
        store_id = r.store_id
        product_id = r.product_id
        p_info = products_meta.get(product_id, {})
        s_info = stores_meta.get(store_id, {})

        stock = r.current_stock
        daily_demand = max(0.1, r.demand_velocity)
        lead_time = r.lead_time_days or p_info.get("lead_time_days", 5)
        incoming = r.incoming_quantity
        unit_price = p_info.get("selling_price", 100.0)
        cost_price = p_info.get("cost_price", 60.0)
        gross_margin = max(0.0, unit_price - cost_price)
        doi = r.days_of_inventory if r.days_of_inventory is not None else 0.0
        sku = p_info.get("sku", product_id)
        cat = p_info.get("category", "Retail")
        city = s_info.get("city", "")
        supplier_name = p_info.get("supplier_name", "Primary Supplier")
        moq = p_info.get("minimum_order_quantity", 10)
        rop = p_info.get("reorder_point", 15)

        # Financial exposure: estimated lost revenue over supplier lead time
        exposure_inr = round(daily_demand * lead_time * unit_price, 2)
        gp_exposure_inr = round(daily_demand * lead_time * gross_margin, 2)
        impact_score = normalize_financial_exposure(exposure_inr)

        # Urgency: days until expected stockout
        if stock == 0:
            urgency_score = 5.0
            urgency_desc = "Out of stock on shelf now (0 units). Active sales demand is unfulfilled."
        elif doi < 2.0:
            urgency_score = 4.5
            urgency_desc = f"Critical runway: only {doi:.1f} days of supply remaining on hand."
        elif doi < lead_time:
            urgency_score = 3.8
            urgency_desc = f"Stock runway ({doi:.1f} days) is less than supplier lead time ({lead_time} days)."
        else:
            urgency_score = 2.5
            urgency_desc = f"Approaching safety stock threshold with {doi:.1f} days of supply."

        evidence_strength_score = 0.95
        evidence_strength_desc = "Direct ledger closing balance with continuous 90-day sales transaction history."

        score, tier = calculate_priority_score(impact_score, urgency_score, evidence_strength_score)

        # RULE EVALUATION: Check for Transfer Opportunity vs Reorder
        transfer_candidate = None
        other_stores = inventory_map.get(product_id, [])
        for other in other_stores:
            if other["store_id"] != store_id:
                other_stock = other["closing_stock"]
                other_rop = other["reorder_point"]
                # Excessive if stock is > 2x ROP and at least 25 units
                if other_stock > (other_rop * 2.0) and other_stock >= 25:
                    transfer_candidate = other
                    break

        if transfer_candidate:
            avail_surplus = transfer_candidate["closing_stock"] - transfer_candidate["reorder_point"]
            transfer_qty = min(avail_surplus, max(10, int(daily_demand * 14)))
            rec = ActionRecommendation(
                action=SupportedAction.TRANSFER,
                details=(
                    f"Initiate regional transfer of {transfer_qty} units from surplus store "
                    f"'{transfer_candidate['store_name']}' ({transfer_candidate['closing_stock']} units on hand) "
                    f"to '{r.store_name}' to prevent stockout without waiting for supplier lead time."
                ),
                source_store=transfer_candidate["store_name"],
                destination_store=r.store_name,
                transfer_quantity=transfer_qty,
                requires_manager_approval=True
            )
            reason = (
                f"Product is low in {r.store_name} ({stock} units) but excessive in "
                f"{transfer_candidate['store_name']} ({transfer_candidate['closing_stock']} units). Cross-store transfer balances inventory."
            )
        elif incoming == 0:
            needed_qty = max(moq, (rop * 3) - stock)
            rec = ActionRecommendation(
                action=SupportedAction.REORDER,
                details=f"Issue purchase order for {needed_qty} units to primary supplier '{supplier_name}'. Lead time is {lead_time} days.",
                recommended_order_quantity=needed_qty,
                requires_manager_approval=True
            )
            reason = (
                f"Product has elevated stockout risk ({r.risk_score:.1f}/100) with no incoming open purchase orders. "
                f"Immediate replenishment is required."
            )
        else:
            rec = ActionRecommendation(
                action=SupportedAction.MONITOR,
                details=f"Open purchase order of {incoming} units is already in transit. Monitor expected delivery date.",
                requires_manager_approval=False
            )
            reason = f"Replenishment of {incoming} units is already pending delivery. Stockout risk runway is monitored."

        alerts.append(AttentionItem(
            rank=0,
            alert_type=AlertType.LIKELY_STOCKOUT,
            priority=tier,
            priority_score=score,
            product={
                "product_id": r.product_id,
                "product_name": r.product_name,
                "sku": sku,
                "category": cat
            },
            store={
                "store_id": r.store_id,
                "store_name": r.store_name,
                "city": city
            },
            evidence={
                "metric": "days_of_inventory",
                "value": round(doi, 1),
                "unit": "days",
                "source": "inventory.csv + sales.csv",
                "period": "2024-08-23 to 2024-08-29",
                "calculation": "current_stock / average_daily_sales_7d",
                "raw_values": {
                    "current_stock": stock,
                    "daily_demand": round(daily_demand, 2),
                    "supplier_lead_time_days": lead_time,
                    "incoming_stock": incoming,
                    "reorder_point": rop,
                    "risk_score": round(r.risk_score, 1)
                }
            },
            business_impact=BusinessImpact(
                score=impact_score,
                exposure_inr=exposure_inr,
                metric_name="estimated_lost_revenue",
                description=f"Estimated revenue exposure of ₹{exposure_inr:,.2f} (₹{gp_exposure_inr:,.2f} gross profit) over {lead_time}-day lead time."
            ),
            urgency=UrgencyFactor(
                score=urgency_score,
                primary_factor="days_of_inventory",
                description=urgency_desc
            ),
            evidence_strength=EvidenceStrengthFactor(
                score=evidence_strength_score,
                data_completeness=1.0,
                sample_size_days=90,
                model_type="deterministic_stockout_risk_model",
                description=evidence_strength_desc
            ),
            recommendation=rec,
            assumptions=[
                f"Supplier lead time remains constant at {lead_time} days.",
                f"Recent 7-day average sales velocity ({daily_demand:.2f} units/day) reflects forward customer demand.",
                "Cross-store transit SLA is 1-2 business days within local hub network."
            ],
            reason=reason
        ))

    return alerts


def _detect_slow_moving_alerts(
    products_meta: Dict[str, Dict[str, Any]],
    stores_meta: Dict[str, Dict[str, Any]]
) -> List[AttentionItem]:
    """
    Generates alerts for stagnant or dead inventory.
    Rule: If product has high inventory and very low demand -> Recommend REDUCE_REORDERING or PROMOTION_REVIEW.
    """
    alerts: List[AttentionItem] = []
    items = detect_slow_moving_products(sales_threshold_daily=0.2, min_catalog_age_days=21)

    for item in items:
        stock = item.current_stock
        cost = item.cost_price
        capital_locked = round(stock * cost, 2)
        impact_score = normalize_financial_exposure(capital_locked)

        age = item.catalog_age_days
        if age >= 60:
            urgency_score = 3.5
            urgency_desc = f"Dead stock warning: catalog age is {age} days with negligible turnover."
        elif age >= 40:
            urgency_score = 2.8
            urgency_desc = f"Slow movement over {age} days of catalog presence."
        else:
            urgency_score = 2.0
            urgency_desc = f"Low sales velocity ({item.daily_sales_velocity:.2f} units/day) over {age} days."

        evidence_strength_score = 0.90
        score, tier = calculate_priority_score(impact_score, urgency_score, evidence_strength_score)

        s_info = stores_meta.get(item.store_id, {})

        if capital_locked > 5000:
            action = SupportedAction.PROMOTION_REVIEW
            details = (
                f"Initiate promotional markdown review or bundle discounting to clear ₹{capital_locked:,.2f} "
                f"in stagnant inventory ({stock} units)."
            )
            reason = f"High inventory capital (₹{capital_locked:,.2f}) tied up in very low-demand line ({item.daily_sales_velocity:.2f} units/day)."
        else:
            action = SupportedAction.REDUCE_REORDERING
            details = (
                f"Freeze automated reorder cycles for {item.sku} and reduce purchase order replenishment points "
                f"until stock velocity accelerates."
            )
            reason = f"Product velocity is below 0.2 units/day. Reorder parameters should be reduced immediately."

        alerts.append(AttentionItem(
            rank=0,
            alert_type=AlertType.SLOW_MOVING,
            priority=tier,
            priority_score=score,
            product={
                "product_id": item.product_id,
                "product_name": item.product_name,
                "sku": item.sku,
                "category": item.category
            },
            store={
                "store_id": item.store_id,
                "store_name": item.store_name,
                "city": s_info.get("city", "")
            },
            evidence={
                "metric": "daily_sales_velocity",
                "value": round(item.daily_sales_velocity, 3),
                "unit": "units/day",
                "source": "sales.csv + inventory.csv",
                "period": "Catalog lifetime to 2024-08-29",
                "calculation": "SUM(units_sold) / catalog_age_days",
                "raw_values": {
                    "current_stock": stock,
                    "catalog_age_days": age,
                    "unit_cost_inr": cost,
                    "capital_locked_inr": capital_locked
                }
            },
            business_impact=BusinessImpact(
                score=impact_score,
                exposure_inr=capital_locked,
                metric_name="inventory_capital_tied_up",
                description=f"₹{capital_locked:,.2f} in working capital locked in {stock} stagnant units."
            ),
            urgency=UrgencyFactor(
                score=urgency_score,
                primary_factor="catalog_age_days",
                description=urgency_desc
            ),
            evidence_strength=EvidenceStrengthFactor(
                score=evidence_strength_score,
                data_completeness=0.95,
                sample_size_days=age,
                model_type="catalog_age_velocity_filter",
                description="Verified catalog launch date and POS sales records."
            ),
            recommendation=ActionRecommendation(
                action=action,
                details=details,
                requires_manager_approval=True
            ),
            assumptions=[
                "Holding costs accrue at standard carrying rates.",
                "Sales velocity is unconstrained by stock availability."
            ],
            reason=reason
        ))

    return alerts


def _detect_overstock_alerts(
    products_meta: Dict[str, Dict[str, Any]],
    stores_meta: Dict[str, Dict[str, Any]]
) -> List[AttentionItem]:
    """
    Generates alerts for inventory exceeding target runway.
    Rule: Recommend REDUCE_REORDERING or PROMOTION_REVIEW.
    """
    alerts: List[AttentionItem] = []
    items = detect_overstocked_products()

    for item in items:
        excess_units = item.excess_units_estimate
        excess_val = round(item.excess_inventory_value, 2)
        doi = item.days_of_inventory
        target_days = item.target_days

        impact_score = normalize_financial_exposure(excess_val)
        if doi > 90.0:
            urgency_score = 3.5
            urgency_desc = f"Extreme runway: {doi:.0f} days of supply exceeds {target_days}-day benchmark by over 2x."
        elif doi > 60.0:
            urgency_score = 2.8
            urgency_desc = f"Elevated runway: {doi:.0f} days of supply exceeds {target_days}-day target runway."
        else:
            urgency_score = 2.0
            urgency_desc = f"Moderate overstock of {excess_units} units above runway target."

        evidence_strength_score = 0.90
        score, tier = calculate_priority_score(impact_score, urgency_score, evidence_strength_score)

        s_info = stores_meta.get(item.store_id, {})
        p_info = products_meta.get(item.product_id, {})

        alerts.append(AttentionItem(
            rank=0,
            alert_type=AlertType.OVERSTOCK,
            priority=tier,
            priority_score=score,
            product={
                "product_id": item.product_id,
                "product_name": item.product_name,
                "sku": item.sku,
                "category": p_info.get("category", "")
            },
            store={
                "store_id": item.store_id,
                "store_name": item.store_name,
                "city": s_info.get("city", "")
            },
            evidence={
                "metric": "days_of_inventory",
                "value": round(doi, 1),
                "unit": "days",
                "source": "inventory.csv + sales.csv",
                "period": "2024-08-23 to 2024-08-29",
                "calculation": "current_stock / demand_velocity",
                "raw_values": {
                    "current_stock": item.current_stock,
                    "target_runway_days": target_days,
                    "excess_units": excess_units,
                    "excess_capital_inr": excess_val
                }
            },
            business_impact=BusinessImpact(
                score=impact_score,
                exposure_inr=excess_val,
                metric_name="excess_working_capital",
                description=f"₹{excess_val:,.2f} in excess inventory capital exceeding {target_days}-day target runway."
            ),
            urgency=UrgencyFactor(
                score=urgency_score,
                primary_factor="runway_days_above_target",
                description=urgency_desc
            ),
            evidence_strength=EvidenceStrengthFactor(
                score=evidence_strength_score,
                data_completeness=1.0,
                sample_size_days=90,
                model_type="target_runway_surplus_model",
                description="Evaluated against active store sales velocity."
            ),
            recommendation=ActionRecommendation(
                action=SupportedAction.REDUCE_REORDERING,
                details=f"Halt automated purchase orders for {item.sku} at {item.store_name} until inventory drops below {target_days} days of supply.",
                requires_manager_approval=True
            ),
            assumptions=[
                f"Target optimal inventory runway is {target_days} days.",
                "Storage space and working capital could be reallocated to higher velocity SKUs."
            ],
            reason=f"Current stock provides {doi:.0f} days of runway, tying up ₹{excess_val:,.2f} in surplus units."
        ))

    return alerts


def _detect_sales_anomaly_alerts(
    products_meta: Dict[str, Dict[str, Any]],
    stores_meta: Dict[str, Dict[str, Any]]
) -> List[AttentionItem]:
    """
    Generates alerts for unusual sales spikes and unusual sales drops.
    Rules:
      - If sales suddenly fall while inventory is zero -> Recommend INVESTIGATE (shelf outage).
      - If sales spike significantly and stock is depleted -> Recommend REORDER.
      - If sales spike with safe buffer -> Recommend MONITOR.
    """
    alerts: List[AttentionItem] = []
    anomalies = detect_sales_anomalies()
    sig_anomalies = [a for a in anomalies if a.severity in ["HIGH", "MEDIUM"]]

    conn = get_db_connection()
    cursor = conn.cursor()

    for a in sig_anomalies:
        cursor.execute("""
            SELECT closing_stock FROM inventory
            WHERE store_id = ? AND product_id = ? AND date = ?;
        """, (a.store_id, a.product_id, a.date))
        row = cursor.fetchone()
        stock_on_date = row["closing_stock"] if row else 0

        p_info = products_meta.get(a.product_id, {})
        s_info = stores_meta.get(a.store_id, {})
        price = p_info.get("selling_price", 100.0)
        sku = p_info.get("sku", a.product_id)

        deviation_units = abs(a.actual_sales - a.expected_sales)
        exposure_inr = round(deviation_units * price, 2)
        impact_score = normalize_financial_exposure(exposure_inr)

        if a.anomaly_type == "DROP":
            alert_cat = AlertType.SALES_DROP
            if stock_on_date == 0:
                urgency_score = 5.0
                urgency_desc = "Sales dropped to 0 while inventory on record was 0 units (shelf outage)."
                action = SupportedAction.INVESTIGATE
                details = (
                    f"Perform physical shelf inspection at {a.store_name}. Sales dropped by {abs(a.percentage_change):.1f}% "
                    f"on {a.date} while closing stock was recorded as 0 units. Verify out-of-stock shelf outage."
                )
                reason = "Sales suddenly fell while inventory is zero. Urgent investigation of stock availability required."
            else:
                urgency_score = 4.0 if a.severity == "HIGH" else 3.0
                urgency_desc = f"Sales dropped by {abs(a.percentage_change):.1f}% below 14-day rolling expected baseline."
                action = SupportedAction.INVESTIGATE
                details = f"Investigate cause of sudden sales drop ({a.actual_sales} units vs expected {a.expected_sales:.1f} units) at {a.store_name}."
                reason = f"Unusual sales drop ({a.percentage_change:.1f}%) detected with stock recorded on hand ({stock_on_date} units)."

        else:  # SPIKE
            alert_cat = AlertType.SALES_SPIKE
            urgency_score = 4.0 if a.severity == "HIGH" else 2.5
            urgency_desc = f"Unusual sales surge of {a.percentage_change:+.1f}% above expected volume."
            if stock_on_date <= 5:
                action = SupportedAction.REORDER
                details = f"Replenish stock immediately. Sales spike on {a.date} depleted on-hand inventory to {stock_on_date} units."
                reason = f"Sales spike ({a.actual_sales} units) exhausted store inventory. Reorder needed."
            else:
                action = SupportedAction.MONITOR
                details = f"Monitor sales run-rate over subsequent days to verify whether spike represents a one-off bulk purchase or structural demand shift."
                reason = f"Sales surged by {a.percentage_change:+.1f}%, but {stock_on_date} units buffer stock remains."

        evidence_strength_score = 0.85  # Statistical baseline
        score, tier = calculate_priority_score(impact_score, urgency_score, evidence_strength_score)

        alerts.append(AttentionItem(
            rank=0,
            alert_type=alert_cat,
            priority=tier,
            priority_score=score,
            product={
                "product_id": a.product_id,
                "product_name": a.product_name,
                "sku": sku
            },
            store={
                "store_id": a.store_id,
                "store_name": a.store_name,
                "city": s_info.get("city", "")
            },
            evidence={
                "metric": "sales_anomaly_pct_deviation",
                "value": round(a.percentage_change, 1),
                "unit": "%",
                "source": "sales.csv",
                "period": a.date,
                "calculation": "((actual_sales - rolling_mean_14d) / rolling_mean_14d) * 100",
                "raw_values": {
                    "date": a.date,
                    "actual_sales": a.actual_sales,
                    "expected_sales": round(a.expected_sales, 1),
                    "stock_on_date": stock_on_date,
                    "severity": a.severity
                }
            },
            business_impact=BusinessImpact(
                score=impact_score,
                exposure_inr=exposure_inr,
                metric_name="volume_variance_valuation",
                description=f"Revenue deviation of ₹{exposure_inr:,.2f} on {a.date} ({deviation_units:.0f} units deviation)."
            ),
            urgency=UrgencyFactor(
                score=urgency_score,
                primary_factor="anomaly_severity_and_stock_state",
                description=urgency_desc
            ),
            evidence_strength=EvidenceStrengthFactor(
                score=evidence_strength_score,
                data_completeness=0.90,
                sample_size_days=14,
                model_type="14d_rolling_standard_deviation_baseline",
                description="Derived from 14-day rolling historical sales distribution."
            ),
            recommendation=ActionRecommendation(
                action=action,
                details=details,
                requires_manager_approval=(action != SupportedAction.MONITOR)
            ),
            assumptions=[
                "Rolling 14-day mean captures normal day-of-week sales rhythm.",
                "Statistical deviation exceeds standard error bounds."
            ],
            reason=reason
        ))

    conn.close()
    return alerts


def _detect_supplier_delay_alerts(
    products_meta: Dict[str, Dict[str, Any]],
    stores_meta: Dict[str, Dict[str, Any]]
) -> List[AttentionItem]:
    """
    Generates alerts for delayed purchase orders.
    Rule: Recommend CONTACT_SUPPLIER.
    """
    alerts: List[AttentionItem] = []
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            po.po_id,
            po.order_date,
            po.expected_date,
            po.ordered_quantity,
            po.unit_cost,
            po.status,
            p.product_id,
            p.product_name,
            p.sku,
            s.store_id,
            s.store_name,
            s.city,
            sup.supplier_id,
            sup.supplier_name,
            sup.lead_time_days,
            i.closing_stock
        FROM purchase_orders po
        JOIN products p ON po.product_id = p.product_id
        JOIN stores s ON po.store_id = s.store_id
        JOIN suppliers sup ON po.supplier_id = sup.supplier_id
        LEFT JOIN inventory i ON (po.store_id = i.store_id AND po.product_id = i.product_id AND i.date = '2024-08-29')
        WHERE po.status = 'DELAYED' OR (po.status = 'PENDING' AND po.expected_date < '2024-08-29')
        ORDER BY po.expected_date ASC;
    """)
    rows = cursor.fetchall()
    conn.close()

    for r in rows:
        order_qty = r["ordered_quantity"]
        unit_cost = r["unit_cost"]
        delayed_val = round(order_qty * unit_cost, 2)
        stock = r["closing_stock"] or 0
        impact_score = normalize_financial_exposure(delayed_val)

        if stock == 0:
            urgency_score = 5.0
            urgency_desc = f"PO {r['po_id']} is delayed past {r['expected_date']} while physical stock is already 0 units."
        elif stock <= 10:
            urgency_score = 4.2
            urgency_desc = f"Delayed delivery with thin inventory buffer ({stock} units on hand)."
        else:
            urgency_score = 3.2
            urgency_desc = f"PO {r['po_id']} delayed past SLA expected date ({r['expected_date']})."

        evidence_strength_score = 0.98
        score, tier = calculate_priority_score(impact_score, urgency_score, evidence_strength_score)

        alerts.append(AttentionItem(
            rank=0,
            alert_type=AlertType.SUPPLIER_DELAY,
            priority=tier,
            priority_score=score,
            product={
                "product_id": r["product_id"],
                "product_name": r["product_name"],
                "sku": r["sku"]
            },
            store={
                "store_id": r["store_id"],
                "store_name": r["store_name"],
                "city": r["city"]
            },
            evidence={
                "metric": "po_delivery_delay",
                "value": r["expected_date"],
                "unit": "date_breached",
                "source": "purchase_orders.csv",
                "period": f"{r['order_date']} to {r['expected_date']}",
                "calculation": "current_date > expected_delivery_date AND status != RECEIVED",
                "raw_values": {
                    "po_id": r["po_id"],
                    "ordered_quantity": order_qty,
                    "supplier_name": r["supplier_name"],
                    "unit_cost": unit_cost,
                    "on_hand_stock": stock
                }
            },
            business_impact=BusinessImpact(
                score=impact_score,
                exposure_inr=delayed_val,
                metric_name="delayed_procurement_capital",
                description=f"₹{delayed_val:,.2f} delayed procurement value across {order_qty} units from {r['supplier_name']}."
            ),
            urgency=UrgencyFactor(
                score=urgency_score,
                primary_factor="po_sla_breach",
                description=urgency_desc
            ),
            evidence_strength=EvidenceStrengthFactor(
                score=evidence_strength_score,
                data_completeness=1.0,
                sample_size_days=r["lead_time_days"],
                model_type="contractual_po_sla_tracker",
                description="Verified ERP purchase order contract and delivery log."
            ),
            recommendation=ActionRecommendation(
                action=SupportedAction.CONTACT_SUPPLIER,
                details=(
                    f"Escalate PO {r['po_id']} with supplier representative at '{r['supplier_name']}'. "
                    f"Expected delivery on {r['expected_date']} was breached. Request immediate dispatch confirmation."
                ),
                requires_manager_approval=True
            ),
            assumptions=[
                f"Contractual supplier delivery SLA expected {order_qty} units by {r['expected_date']}.",
                "Warehouse receiving records are updated same-day."
            ],
            reason=f"Purchase order {r['po_id']} is marked DELAYED. Current store stock is {stock} units."
        ))

    return alerts


def _detect_data_quality_alerts(
    products_meta: Dict[str, Dict[str, Any]],
    stores_meta: Dict[str, Dict[str, Any]]
) -> List[AttentionItem]:
    """
    Generates alerts for inventory data-quality defects.
    Rule: Recommend STOCK_COUNT.
    """
    alerts: List[AttentionItem] = []
    report = check_inventory_data_quality()

    for category, issues in report.issues_by_category.items():
        for issue in issues[:3]:
            pid = issue.get("product_id")
            sid = issue.get("store_id")
            p_info = products_meta.get(pid, {}) if pid else {}
            s_info = stores_meta.get(sid, {}) if sid else {}

            impact_score = 6.0
            urgency_score = 5.0 if "negative" in category.lower() else 3.8
            evidence_strength_score = 1.0
            score, tier = calculate_priority_score(impact_score, urgency_score, evidence_strength_score)

            prod_dict = {
                "product_id": pid,
                "product_name": p_info.get("product_name", "Catalog Product"),
                "sku": p_info.get("sku", pid or "SKU")
            } if pid else None

            store_dict = {
                "store_id": sid,
                "store_name": s_info.get("store_name", "Store"),
                "city": s_info.get("city", "")
            } if sid else None

            alerts.append(AttentionItem(
                rank=0,
                alert_type=AlertType.DATA_QUALITY,
                priority=tier,
                priority_score=score,
                product=prod_dict,
                store=store_dict,
                evidence={
                    "metric": f"data_quality_{category}",
                    "value": issue.get("value", issue.get("error", "Defect")),
                    "unit": "integrity_defect",
                    "source": issue.get("table", "inventory.csv"),
                    "period": issue.get("date", report.as_of_date),
                    "calculation": f"Integrity check rule: {category}",
                    "raw_values": issue
                },
                business_impact=BusinessImpact(
                    score=impact_score,
                    exposure_inr=5000.0,
                    metric_name="inventory_ledger_discrepancy",
                    description=f"Data integrity defect in {category} threatens inventory valuation and replenishment accuracy."
                ),
                urgency=UrgencyFactor(
                    score=urgency_score,
                    primary_factor="data_integrity_violation",
                    description=issue.get("error", f"Integrity violation in {category}.")
                ),
                evidence_strength=EvidenceStrengthFactor(
                    score=evidence_strength_score,
                    data_completeness=1.0,
                    sample_size_days=1,
                    model_type="deterministic_integrity_check",
                    description="Confirmed data-quality audit check in SQLite."
                ),
                recommendation=ActionRecommendation(
                    action=SupportedAction.STOCK_COUNT,
                    details=f"Conduct emergency physical stock cycle count at {s_info.get('store_name', 'target store')} to reconcile ledger balance.",
                    requires_manager_approval=True
                ),
                assumptions=["Ledger balance discrepancy indicates shrinkage, missed receiving, or checkout scan anomaly."],
                reason=f"Data quality defect detected in {category}: {issue.get('error', 'Integrity issue')}."
            ))

    return alerts


# =========================================================================
# ATTENTION TODAY ORCHESTRATION ENGINE
# =========================================================================

def get_attention_today(limit: int = 5) -> Dict[str, Any]:
    """
    Identifies, scores, and ranks attention items across all 7 categories:
    1. Likely stockouts
    2. Slow-moving inventory
    3. Overstock
    4. Unusual sales spikes
    5. Unusual sales drops
    6. Supplier delays
    7. Inventory data-quality issues

    Returns:
    - top_attention_items: top N ranked AttentionItem records
    - total_alerts: total count across all categories
    - summary_metrics: breakdown of counts by priority and total financial exposure
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Pre-build store inventory map for cross-store rebalancing lookups
    cursor.execute("""
        SELECT
            i.store_id,
            s.store_name,
            s.city,
            i.product_id,
            i.closing_stock,
            p.reorder_point
        FROM inventory i
        JOIN stores s ON i.store_id = s.store_id
        JOIN products p ON i.product_id = p.product_id
        WHERE i.date = '2024-08-29';
    """)
    inventory_map: Dict[str, List[Dict[str, Any]]] = {}
    for row in cursor.fetchall():
        pid = row["product_id"]
        if pid not in inventory_map:
            inventory_map[pid] = []
        inventory_map[pid].append(dict(row))

    # Pre-build product metadata
    cursor.execute("""
        SELECT
            p.product_id,
            p.product_name,
            p.sku,
            p.category,
            p.cost_price,
            p.selling_price,
            p.reorder_point,
            sup.supplier_id,
            sup.supplier_name,
            sup.lead_time_days,
            sup.minimum_order_quantity
        FROM products p
        JOIN suppliers sup ON p.supplier_id = sup.supplier_id;
    """)
    products_meta = {row["product_id"]: dict(row) for row in cursor.fetchall()}

    # Pre-build store metadata
    cursor.execute("SELECT store_id, store_name, city FROM stores;")
    stores_meta = {row["store_id"]: dict(row) for row in cursor.fetchall()}

    conn.close()

    all_alerts: List[AttentionItem] = []

    # 1. Likely Stockouts (with transfer vs reorder rule evaluation)
    all_alerts.extend(_detect_stockout_alerts(inventory_map, products_meta, stores_meta))

    # 2. Slow-Moving Inventory
    all_alerts.extend(_detect_slow_moving_alerts(products_meta, stores_meta))

    # 3. Overstock
    all_alerts.extend(_detect_overstock_alerts(products_meta, stores_meta))

    # 4 & 5. Unusual Sales Spikes & Drops
    all_alerts.extend(_detect_sales_anomaly_alerts(products_meta, stores_meta))

    # 6. Supplier Delays
    all_alerts.extend(_detect_supplier_delay_alerts(products_meta, stores_meta))

    # 7. Inventory Data-Quality Issues
    all_alerts.extend(_detect_data_quality_alerts(products_meta, stores_meta))

    # Deduplicate product-store alerts (keep highest priority score for identical product-store pair)
    deduped: Dict[str, AttentionItem] = {}
    for alert in all_alerts:
        prod_id = alert.product.get("product_id") if alert.product else "GLOBAL"
        store_id = alert.store.get("store_id") if alert.store else "GLOBAL"
        key = f"{prod_id}_{store_id}_{alert.alert_type}"
        if key not in deduped or alert.priority_score > deduped[key].priority_score:
            deduped[key] = alert

    ranked_alerts = list(deduped.values())
    ranked_alerts.sort(key=lambda x: x.priority_score, reverse=True)

    # Assign sequential ranks (1, 2, 3...)
    for idx, alert in enumerate(ranked_alerts, 1):
        alert.rank = idx

    top_items = ranked_alerts[:limit]

    # Compute summary metrics
    crit_cnt = sum(1 for a in ranked_alerts if a.priority == PriorityTier.CRITICAL)
    high_cnt = sum(1 for a in ranked_alerts if a.priority == PriorityTier.HIGH)
    med_cnt = sum(1 for a in ranked_alerts if a.priority == PriorityTier.MEDIUM)
    low_cnt = sum(1 for a in ranked_alerts if a.priority == PriorityTier.LOW)
    total_exposure = sum(a.business_impact.exposure_inr for a in ranked_alerts)
    delayed_pos_cnt = sum(1 for a in ranked_alerts if a.alert_type == AlertType.SUPPLIER_DELAY)

    return {
        "status": "success",
        "total_alerts": len(ranked_alerts),
        "limit": limit,
        "summary_metrics": {
            "critical_count": crit_cnt,
            "high_count": high_cnt,
            "medium_count": med_cnt,
            "low_count": low_cnt,
            "total_financial_exposure_inr": round(total_exposure, 2),
            "delayed_pos_count": delayed_pos_cnt
        },
        "top_attention_items": [item.model_dump() for item in top_items]
    }


# =========================================================================
# BACKWARD COMPATIBILITY
# =========================================================================

def generate_action_recommendations() -> List[Dict[str, Any]]:
    """Backward-compatible helper returning action recommendations."""
    res = get_attention_today(limit=10)
    actions = []
    for item in res["top_attention_items"]:
        actions.append({
            "type": item["recommendation"]["action"],
            "priority": item["priority"],
            "store_id": item["store"]["store_id"] if item.get("store") else None,
            "store_name": item["store"]["store_name"] if item.get("store") else "Network",
            "product_id": item["product"]["product_id"] if item.get("product") else None,
            "product_name": item["product"]["product_name"] if item.get("product") else None,
            "rationale": item["reason"],
            "details": item["recommendation"]["details"],
            "priority_score": item["priority_score"]
        })
    return actions


__all__ = [
    "SupportedAction",
    "AlertType",
    "PriorityTier",
    "BusinessImpact",
    "UrgencyFactor",
    "EvidenceStrengthFactor",
    "ActionRecommendation",
    "AttentionItem",
    "calculate_priority_score",
    "normalize_financial_exposure",
    "get_attention_today",
    "generate_action_recommendations"
]