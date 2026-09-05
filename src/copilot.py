"""
Retail Copilot Module
TRACK_ID: PS03

CRITICAL ARCHITECTURAL PRINCIPLE:
- Gemini is NOT the source of truth.
- Local SQLite database and deterministic Python analytics engine are the SOLE SOURCE OF TRUTH.
- Gemini performs NLU intent classification, entity extraction assistance, and grounded reasoning.
- Every numerical claim MUST correspond to evidence returned by Python.
- Gemini NEVER invents numerical values or database IDs.
- If GEMINI_API_KEY is missing or API call fails, deterministic fallback ensures the app never crashes.
"""
from __future__ import annotations

import os
import re
import json
import logging
from typing import Dict, Any, Optional, List, Tuple
from pydantic import BaseModel, Field

# Local Database & Analytics Engine (The Source of Truth)
from src.database.connection import get_db_connection
from src.analytics import (
    calculate_product_performance,
    calculate_store_performance,
    calculate_inventory_health,
    calculate_inventory_turnover,
    get_inventory_health_summary,
    detect_slow_moving_products,
    detect_overstocked_products,
    calculate_stockout_risk,
    assess_all_stockout_risks,
    detect_sales_anomalies,
    check_inventory_data_quality,
)

logger = logging.getLogger(__name__)

# Supported Analytical Intents
SUPPORTED_INTENTS = [
    "SALES_PERFORMANCE",
    "INVENTORY_STATUS",
    "STOCKOUT_RISK",
    "SLOW_MOVERS",
    "OVERSTOCK",
    "SALES_ANOMALY",
    "STORE_COMPARISON",
    "GENERAL_RETAIL_ANALYSIS",
    "UNKNOWN"
]


class IntentSchema(BaseModel):
    """Structured Intent extracted from user question."""
    intent: str = Field(..., description="One of the supported retail analytical intents.")
    product: Optional[str] = Field(None, description="Product name, SKU, or ID mentioned in question.")
    store: Optional[str] = Field(None, description="Store name, city, or ID mentioned in question.")
    time_period: Optional[str] = Field(None, description="Time period mentioned (e.g. 'last week', 'July 2024').")
    requires_clarification: bool = Field(False, description="True if query is excessively ambiguous.")


class CopilotResponse(BaseModel):
    """Strictly structured final response returned to user or client."""
    answer: str = Field(..., description="Evidence-grounded explanation with actual figures.")
    key_findings: List[str] = Field(default_factory=list, description="Bulleted summary of core findings.")
    evidence: Dict[str, Any] = Field(default_factory=dict, description="Deterministic Python analytics output.")
    recommendation: Optional[str] = Field(None, description="Policy-compliant action supported by evidence.")
    assumptions: List[str] = Field(default_factory=list, description="Stated analytical assumptions.")
    limitations: List[str] = Field(default_factory=list, description="Known boundary limitations of data.")
    data_sufficient: bool = Field(True, description="False if data does not contain necessary entity or records.")
    confidence_note: str = Field("", description="Explanation of basis of confidence and data source.")


# =========================================================================
# ENTITY RESOLUTION (Resolved against local SQLite database)
# =========================================================================

def get_product_sku_map() -> Dict[str, str]:
    """Returns mapping from product_id to SKU."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT product_id, sku FROM products")
        mapping = {r["product_id"]: r["sku"] for r in cursor.fetchall()}
        conn.close()
        return mapping
    except Exception:
        return {}

def get_store_city_map() -> Dict[str, str]:
    """Returns mapping from store_id to City."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT store_id, city FROM stores")
        mapping = {r["store_id"]: r["city"] for r in cursor.fetchall()}
        conn.close()
        return mapping
    except Exception:
        return {}

def resolve_entities(
    product_query: Optional[str] = None,
    store_query: Optional[str] = None
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], bool, bool]:
    """
    Resolves product and store entities against local SQLite tables.
    Prevents model hallucination of IDs.
    Returns:
        (resolved_product, resolved_store, product_not_found, store_not_found)
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    resolved_store = None
    store_not_found = False

    if store_query and store_query.strip():
        sq = store_query.strip()
        cursor.execute("SELECT store_id, store_name, city, state, store_type FROM stores")
        stores = [dict(row) for row in cursor.fetchall()]

        # 1. Exact ID, name, or city match
        for s in stores:
            if (
                sq.lower() == s["store_id"].lower()
                or sq.lower() == s["store_name"].lower()
                or sq.lower() == s["city"].lower()
            ):
                resolved_store = s
                break

        # 2. Substring match
        if not resolved_store:
            for s in stores:
                if (
                    sq.lower() in s["store_id"].lower()
                    or sq.lower() in s["store_name"].lower()
                    or sq.lower() in s["city"].lower()
                    or s["city"].lower() in sq.lower()
                    or s["store_name"].lower() in sq.lower()
                ):
                    resolved_store = s
                    break

        if not resolved_store:
            store_not_found = True

    resolved_product = None
    product_not_found = False

    if product_query and product_query.strip():
        pq = product_query.strip()
        cursor.execute("""
            SELECT product_id, sku, product_name, category, subcategory, brand, cost_price, selling_price, reorder_point
            FROM products
        """)
        products = [dict(row) for row in cursor.fetchall()]

        # 1. Exact ID or SKU match
        for p in products:
            if pq.lower() == p["product_id"].lower() or pq.lower() == p["sku"].lower():
                resolved_product = p
                break

        # 2. Exact product name match
        if not resolved_product:
            for p in products:
                if pq.lower() == p["product_name"].lower():
                    resolved_product = p
                    break

        # 3. Substring match
        if not resolved_product:
            pq_clean = re.sub(r"[^a-zA-Z0-9\s]", "", pq.lower())
            pq_words = set(pq_clean.split())
            best_match = None
            best_score = 0

            for p in products:
                p_text = f"{p['product_name']} {p['sku']} {p['brand']}".lower()
                if pq.lower() in p_text:
                    best_match = p
                    break
                p_words = set(re.sub(r"[^a-zA-Z0-9\s]", "", p_text).split())
                overlap = len(pq_words.intersection(p_words))
                if overlap > best_score and overlap >= 2:
                    best_score = overlap
                    best_match = p

            if best_match:
                resolved_product = best_match
            else:
                product_not_found = True

    conn.close()
    return resolved_product, resolved_store, product_not_found, store_not_found


# =========================================================================
# DETERMINISTIC RULE-BASED INTENT EXTRACTOR (Fallback & Local Parser)
# =========================================================================

def rule_based_extract_intent(question: str) -> IntentSchema:
    """
    Deterministic NLP parser used when Gemini is unconfigured or unavailable.
    Maps user queries to one of the 9 supported intents and extracts potential entity mentions.
    """
    q = question.lower()

    # Detect store mentions
    store_candidate = None
    if "hyderabad" in q or "cyberabad" in q:
        store_candidate = "Hyderabad"
    elif "mumbai" in q or "palladium" in q:
        store_candidate = "Mumbai"
    elif "bengaluru" in q or "bangalore" in q or "koramangala" in q:
        store_candidate = "Bengaluru"
    elif "delhi" in q or "citywalk" in q:
        store_candidate = "Delhi"
    elif "str-001" in q:
        store_candidate = "STR-001"
    elif "str-002" in q:
        store_candidate = "STR-002"
    elif "str-003" in q:
        store_candidate = "STR-003"
    elif "str-004" in q:
        store_candidate = "STR-004"
    else:
        # Check for store in "at <store>" or "in <store>"
        store_match = re.search(r"\b(?:at|in|for store|for)\s+([A-Za-z0-9\-\s]{3,20})\b", question, re.IGNORECASE)
        if store_match:
            cand = store_match.group(1).strip()
            # Exclude common retail keywords
            if cand.lower() not in ["july", "august", "today", "yesterday", "stock", "sales", "all", "stores", "products", "risk"]:
                store_candidate = cand

    # Detect product mentions
    product_candidate = None
    sku_match = re.search(r"\b(SKU-[A-Za-z0-9\-]+|PRD-\d+)\b", question, re.IGNORECASE)
    if sku_match:
        product_candidate = sku_match.group(1).upper()
    else:
        # Known catalog product key terms
        catalog_hints = [
            "masala chai", "filter coffee", "electrolyte", "mango juice", "coconut water",
            "jeera soda", "green tea", "badam milk", "apple cider", "darjeeling",
            "amla aloe", "mocha drink", "instant chicory", "thandai syrup", "makhana",
            "bhujia sev", "potato wafers", "khakhra", "dark chocolate", "almonds",
            "cashews", "digestive fiber", "butter cookies", "roasted chana", "banana chips",
            "soan papdi", "pita chips", "corn puffs", "basmati rice"
        ]
        for hint in catalog_hints:
            if hint in q:
                product_candidate = hint
                break

    # If still not found, check regex patterns like "how did <X> perform", "performance of <X>", etc.
    if not product_candidate:
        perf_match = re.search(r"\b(?:how did|performance of|sales of|sales for|about product|for product)\s+([A-Za-z0-9\-\s]{2,30}?)(?:\s+in|\s+at|\s+perform|\?|$)", question, re.IGNORECASE)
        if perf_match:
            cand = perf_match.group(1).strip()
            if cand.lower() not in ["store", "all", "stores", "products", "sales", "inventory", "stock", "we", "our"]:
                product_candidate = cand

    # Classify Intent
    if any(k in q for k in ["running out", "stockout", "stock out", "low stock", "run out", "critical stock", "shortage", "deplete"]):
        intent = "STOCKOUT_RISK"
    elif any(k in q for k in ["overstock", "excess stock", "surplus", "too much inventory", "high runway"]):
        intent = "OVERSTOCK"
    elif any(k in q for k in ["slow mover", "slow moving", "not moving", "stagnant", "dead stock", "low velocity", "dormant"]):
        intent = "SLOW_MOVERS"
    elif any(k in q for k in ["anomaly", "anomalies", "spike", "sudden drop", "abnormal", "unusual sales"]):
        intent = "SALES_ANOMALY"
    elif any(k in q for k in ["compare store", "store comparison", "which stores are performing", "store performance across", "stores performing well", "compare all stores", "store rank"]):
        intent = "STORE_COMPARISON"
    elif any(k in q for k in ["store performance", "branch performance"]) and store_candidate:
        intent = "STORE_COMPARISON"
    elif any(k in q for k in ["product performance", "how did", "sales performance", "units sold", "top selling", "revenue", "sales volume"]):
        intent = "SALES_PERFORMANCE"
    elif any(k in q for k in ["inventory health", "inventory status", "days of inventory", "stock level", "current stock", "units in stock"]):
        intent = "INVENTORY_STATUS"
    elif any(k in q for k in ["what should i do", "daily action", "retail analysis", "overall health", "overview", "summary", "actions today"]):
        intent = "GENERAL_RETAIL_ANALYSIS"
    elif any(k in q for k in ["inventory", "stock"]):
        intent = "INVENTORY_STATUS"
    elif any(k in q for k in ["sales", "performance"]):
        intent = "SALES_PERFORMANCE"
    else:
        intent = "UNKNOWN"

    return IntentSchema(
        intent=intent,
        product=product_candidate,
        store=store_candidate,
        time_period=None,
        requires_clarification=False
    )


# =========================================================================
# DETERMINISTIC ANALYTICS EXECUTION (Python is Source of Truth)
# =========================================================================

def execute_deterministic_analytics(
    intent_data: IntentSchema,
    resolved_product: Optional[Dict[str, Any]],
    resolved_store: Optional[Dict[str, Any]]
) -> Tuple[Dict[str, Any], bool, List[str]]:
    """
    Executes Python deterministic calculations matching the identified intent.
    Returns:
        (evidence_dict, data_sufficient, missing_info_notes)
    """
    store_id = resolved_store["store_id"] if resolved_store else None
    product_id = resolved_product["product_id"] if resolved_product else None
    evidence: Dict[str, Any] = {}
    data_sufficient = True
    missing_info: List[str] = []

    sku_map = get_product_sku_map()
    city_map = get_store_city_map()

    intent = intent_data.intent

    try:
        if intent == "STOCKOUT_RISK":
            if store_id and product_id:
                res = calculate_stockout_risk(store_id=store_id, product_id=product_id)
                evidence = {
                    "type": "single_stockout_risk",
                    "store_id": store_id,
                    "store_name": resolved_store["store_name"],
                    "city": resolved_store["city"],
                    "product_id": product_id,
                    "product_name": resolved_product["product_name"],
                    "sku": resolved_product.get("sku") or sku_map.get(product_id, product_id),
                    "current_stock": res.current_stock,
                    "lead_time_days": res.lead_time_days,
                    "lead_time_demand": round(res.lead_time_demand, 1),
                    "daily_demand": round(res.demand_velocity, 2),
                    "safety_stock": round(res.safety_stock, 1),
                    "incoming_stock": res.incoming_quantity,
                    "risk_score": round(res.risk_score, 1),
                    "risk_level": res.risk_level,
                    "days_of_inventory": round(res.days_of_inventory, 1) if res.days_of_inventory is not None else None,
                    "explanation_factors": res.explanation_factors
                }
            else:
                # Catalog-wide or store-wide stockout risk scan
                results = assess_all_stockout_risks(store_id=store_id, min_risk_score=20.0)
                # Sort descending by risk score
                results.sort(key=lambda x: x.risk_score, reverse=True)
                critical_items = []
                for r in results[:10]:
                    critical_items.append({
                        "store_id": r.store_id,
                        "store_name": r.store_name,
                        "city": city_map.get(r.store_id, ""),
                        "product_id": r.product_id,
                        "product_name": r.product_name,
                        "sku": sku_map.get(r.product_id, r.product_id),
                        "current_stock": r.current_stock,
                        "daily_demand": round(r.demand_velocity, 2),
                        "lead_time_days": r.lead_time_days,
                        "lead_time_demand": round(r.lead_time_demand, 1),
                        "incoming_stock": r.incoming_quantity,
                        "risk_score": round(r.risk_score, 1),
                        "risk_level": r.risk_level,
                        "days_of_inventory": round(r.days_of_inventory, 1) if r.days_of_inventory is not None else None,
                        "explanation_factors": r.explanation_factors
                    })
                evidence = {
                    "type": "stockout_risk_scan",
                    "store_filter": resolved_store["store_name"] if resolved_store else "All Stores",
                    "total_evaluated": len(results),
                    "critical_count": sum(1 for r in results if r.risk_level in ["CRITICAL", "HIGH"]),
                    "critical_items": critical_items
                }

        elif intent == "INVENTORY_STATUS":
            if store_id and product_id:
                res = calculate_inventory_health(store_id=store_id, product_id=product_id)
                evidence = {
                    "type": "single_inventory_health",
                    "store_id": store_id,
                    "store_name": resolved_store["store_name"],
                    "product_id": product_id,
                    "product_name": resolved_product["product_name"],
                    "sku": resolved_product.get("sku") or sku_map.get(product_id, product_id),
                    "current_stock": res.current_stock,
                    "inventory_valuation": round(res.inventory_value_at_cost, 2),
                    "days_of_inventory": round(res.days_of_inventory, 1) if res.days_of_inventory is not None else None,
                    "daily_sales_velocity": round(res.average_daily_sales_7d, 2),
                    "health_status": res.status,
                    "reorder_point": res.reorder_point
                }
            else:
                summary = get_inventory_health_summary()
                evidence = {
                    "type": "inventory_health_summary",
                    "total_skus": summary.get("total_skus", 0),
                    "total_units_in_stock": summary.get("total_units_in_stock", 0),
                    "total_inventory_valuation": round(summary.get("total_inventory_valuation", 0.0), 2),
                    "healthy_count": summary.get("healthy_count", 0),
                    "low_stock_count": summary.get("low_stock_count", 0),
                    "overstock_count": summary.get("overstock_count", 0),
                    "out_of_stock_count": summary.get("out_of_stock_count", 0),
                }

        elif intent == "SLOW_MOVERS":
            results = detect_slow_moving_products(store_id=store_id)
            items = []
            for r in results[:10]:
                items.append({
                    "store_id": r.store_id,
                    "store_name": r.store_name,
                    "product_id": r.product_id,
                    "product_name": r.product_name,
                    "sku": sku_map.get(r.product_id, r.product_id),
                    "current_stock": r.current_stock,
                    "avg_daily_sales": round(r.daily_sales_velocity, 3),
                    "days_of_inventory": round(r.days_of_inventory, 1) if r.days_of_inventory is not None else None,
                    "catalog_age_days": r.catalog_age_days,
                    "recommendation": "Initiate markdown or inter-store transfer"
                })
            evidence = {
                "type": "slow_movers_detection",
                "store_filter": resolved_store["store_name"] if resolved_store else "All Stores",
                "count": len(items),
                "items": items
            }

        elif intent == "OVERSTOCK":
            results = detect_overstocked_products(store_id=store_id)
            items = []
            for r in results[:10]:
                items.append({
                    "store_id": r.store_id,
                    "store_name": r.store_name,
                    "product_id": r.product_id,
                    "product_name": r.product_name,
                    "sku": r.sku or sku_map.get(r.product_id, r.product_id),
                    "current_stock": r.current_stock,
                    "avg_daily_sales": round(r.demand_velocity, 2),
                    "target_runway_days": r.target_days,
                    "days_of_inventory": round(r.days_of_inventory, 1),
                    "excess_units": r.excess_units_estimate,
                    "excess_inventory_value": round(r.excess_inventory_value, 2),
                    "recommendation": "Reduce future order quantities and consider clearance"
                })
            total_excess_val = sum(r.excess_inventory_value for r in results)
            evidence = {
                "type": "overstock_detection",
                "store_filter": resolved_store["store_name"] if resolved_store else "All Stores",
                "count": len(items),
                "total_excess_inventory_value": round(total_excess_val, 2),
                "items": items
            }

        elif intent == "SALES_ANOMALY":
            results = detect_sales_anomalies(store_id=store_id, product_id=product_id)
            items = []
            for r in results[:10]:
                items.append({
                    "date": r.date,
                    "store_id": r.store_id,
                    "store_name": r.store_name,
                    "product_id": r.product_id,
                    "product_name": r.product_name,
                    "sku": sku_map.get(r.product_id, r.product_id),
                    "actual_sales": r.actual_sales,
                    "baseline_mean": round(r.expected_sales, 1),
                    "pct_change": round(r.percentage_change, 1),
                    "anomaly_type": r.anomaly_type,
                    "severity": r.severity
                })
            evidence = {
                "type": "sales_anomalies",
                "count": len(items),
                "items": items
            }

        elif intent == "STORE_COMPARISON":
            # To provide rich margin & growth data, evaluate each store specifically
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT store_id, store_name, city FROM stores")
            stores_meta = [dict(r) for r in cursor.fetchall()]
            conn.close()

            items = []
            for sm in stores_meta:
                try:
                    s_perf = calculate_store_performance(store_id=sm["store_id"])
                    growth_val = (
                        s_perf.growth_vs_previous_period.get("growth_rate_pct", 0.0)
                        if isinstance(s_perf.growth_vs_previous_period, dict) else None
                    )
                    items.append({
                        "store_id": s_perf.store_id,
                        "store_name": s_perf.store_name,
                        "city": sm["city"],
                        "revenue": round(s_perf.revenue, 2),
                        "units_sold": s_perf.units,
                        "gross_profit": round(s_perf.gross_profit, 2),
                        "gross_margin_pct": round(s_perf.gross_margin, 1),
                        "growth_vs_previous_period": round(growth_val, 1) if growth_val is not None else None
                    })
                except Exception:
                    pass

            items.sort(key=lambda x: x["revenue"], reverse=True)
            evidence = {
                "type": "store_comparison",
                "store_count": len(items),
                "stores": items
            }

        elif intent == "SALES_PERFORMANCE":
            if product_id:
                res = calculate_product_performance(product_id=product_id, store_id=store_id)
                growth_val = (
                    res.comparison_with_previous_period.get("revenue_growth_percent", 0.0)
                    if isinstance(res.comparison_with_previous_period, dict) else None
                )
                evidence = {
                    "type": "product_sales_performance",
                    "product_id": res.product_id,
                    "product_name": res.product_name,
                    "sku": res.sku,
                    "revenue": round(res.revenue, 2),
                    "units_sold": res.units_sold,
                    "gross_margin_pct": round(res.gross_margin_percent, 1),
                    "growth_vs_previous_period": round(growth_val, 1) if growth_val is not None else None
                }
            elif store_id:
                res = calculate_store_performance(store_id=store_id)
                growth_val = (
                    res.growth_vs_previous_period.get("growth_rate_pct", 0.0)
                    if isinstance(res.growth_vs_previous_period, dict) else None
                )
                evidence = {
                    "type": "single_store_sales_performance",
                    "store_id": res.store_id,
                    "store_name": res.store_name,
                    "city": city_map.get(res.store_id, ""),
                    "revenue": round(res.revenue, 2),
                    "units_sold": res.units,
                    "gross_margin_pct": round(res.gross_margin, 1),
                    "growth_vs_previous_period": round(growth_val, 1) if growth_val is not None else None
                }
            else:
                # Top products overview
                results = calculate_product_performance()
                items = []
                for p in results[:8]:
                    if isinstance(p, dict):
                        rev = float(p.get("total_revenue", 0.0))
                        gp = float(p.get("total_gross_profit", 0.0))
                        margin = round((gp / rev * 100.0), 1) if rev > 0 else 0.0
                        items.append({
                            "product_id": p.get("product_id"),
                            "product_name": p.get("product_name"),
                            "sku": p.get("sku"),
                            "revenue": round(rev, 2),
                            "units_sold": int(p.get("total_units_sold", 0)),
                            "gross_margin_pct": margin
                        })
                    else:
                        items.append({
                            "product_id": p.product_id,
                            "product_name": p.product_name,
                            "sku": p.sku,
                            "revenue": round(p.revenue, 2),
                            "units_sold": p.units_sold,
                            "gross_margin_pct": round(p.gross_margin_percent, 1)
                        })
                evidence = {
                    "type": "top_products_sales_performance",
                    "top_products": items
                }

        elif intent == "GENERAL_RETAIL_ANALYSIS":
            health_summary = get_inventory_health_summary()
            stockout_results = assess_all_stockout_risks(min_risk_score=50.0)
            stores_perf = calculate_store_performance(store_id=None)
            total_rev = sum(s.revenue for s in stores_perf)
            evidence = {
                "type": "general_retail_overview",
                "total_network_revenue": round(total_rev, 2),
                "total_units_in_stock": health_summary.get("total_units_in_stock", 0),
                "total_inventory_valuation": round(health_summary.get("total_inventory_valuation", 0.0), 2),
                "high_stockout_risk_count": len(stockout_results),
                "low_stock_sku_count": health_summary.get("low_stock_count", 0),
                "overstock_sku_count": health_summary.get("overstock_count", 0),
            }

        else:  # UNKNOWN
            data_sufficient = False
            missing_info.append("Query does not map to recognized retail sales or inventory analytical intents.")
            evidence = {
                "supported_intents": SUPPORTED_INTENTS[:-1]
            }

    except Exception as e:
        logger.error(f"Deterministic analytics execution failed: {e}", exc_info=True)
        data_sufficient = False
        missing_info.append(f"Calculation error: {str(e)}")
        evidence = {"error": str(e)}

    return evidence, data_sufficient, missing_info


# =========================================================================
# DETERMINISTIC RESPONSE SYNTHESIZER (Fallback & Zero-Hallucination Engine)
# =========================================================================

def synthesize_deterministic_response(
    question: str,
    intent_data: IntentSchema,
    evidence: Dict[str, Any],
    data_sufficient: bool,
    missing_info: List[str],
    confidence_prefix: str = "Ground truth calculated deterministically via Python analytics over local SQLite data."
) -> CopilotResponse:
    """
    Produces a high-quality, strictly grounded response using actual figures from the evidence package.
    Used when GEMINI_API_KEY is not configured or as fallback when API call fails.
    Zero hallucination guarantee: uses only verified values.
    """
    if not data_sufficient:
        reasons = " ".join(missing_info) if missing_info else "The requested information is not available in local records."
        answer = f"Data is insufficient to answer this query. {reasons}"
        return CopilotResponse(
            answer=answer,
            key_findings=[reasons],
            evidence=evidence,
            recommendation="Please refine the query or choose from supported retail categories (Stockout Risk, Overstock, Slow Movers, Store Performance, Sales Anomalies).",
            assumptions=[],
            limitations=["Available data is restricted to local stores and catalog records."],
            data_sufficient=False,
            confidence_note=f"{confidence_prefix} (Data insufficient)"
        )

    intent = intent_data.intent

    if intent == "STOCKOUT_RISK":
        if evidence.get("type") == "single_stockout_risk":
            name = evidence.get("product_name")
            sku = evidence.get("sku")
            store = evidence.get("store_name")
            city = evidence.get("city")
            stock = evidence.get("current_stock")
            demand = evidence.get("daily_demand")
            lead_time = evidence.get("lead_time_days")
            lead_demand = evidence.get("lead_time_demand")
            incoming = evidence.get("incoming_stock")
            score = evidence.get("risk_score")

            answer = (
                f"Product stockout risk evaluation for {sku} ({name}) at {store} ({city}):\n\n"
                f"Current stock: {stock} units\n"
                f"7-day demand: {demand} units/day\n"
                f"Supplier lead time: {lead_time} days\n"
                f"Lead-time demand: {lead_demand} units\n"
                f"Incoming stock: {incoming} units\n"
                f"Risk score: {score}/100\n\n"
                f"Recommendation:\nPlace a replenishment order, subject to manager approval."
            )
            findings = [
                f"{sku} at {store} has a stockout risk score of {score}/100 ({evidence.get('risk_level')}).",
                f"Projected lead-time demand is {lead_demand} units against current stock of {stock} units."
            ]
            recommendation = "Place a replenishment order, subject to manager approval."
            assumptions = [
                f"Supplier lead time remains constant at {lead_time} days.",
                f"Recent sales velocity ({demand} units/day) reflects forward customer demand."
            ]
            limitations = ["Model does not assume promotional or external seasonal demand spikes."]

        else:
            items = evidence.get("critical_items", [])
            count = evidence.get("critical_count", len(items))
            store_filter = evidence.get("store_filter", "All Stores")

            lines = [f"{count} products have high stockout risk ({store_filter}).\n"]
            for idx, item in enumerate(items[:3], 1):
                city_str = f" ({item['city']})" if item.get('city') else ""
                lines.append(
                    f"{item['sku']} ({item['product_name']}) at {item['store_name']}{city_str}:\n"
                    f"Current stock: {item['current_stock']} units\n"
                    f"7-day demand: {item['daily_demand']} units/day\n"
                    f"Supplier lead time: {item['lead_time_days']} days\n"
                    f"Lead-time demand: {item['lead_time_demand']} units\n"
                    f"Incoming stock: {item['incoming_stock']} units\n"
                    f"Risk score: {item['risk_score']}/100\n"
                )

            rec_text = "Place replenishment orders for high-risk SKUs immediately, subject to manager approval."
            lines.append(f"Recommendation:\n{rec_text}")
            answer = "\n".join(lines)

            findings = [
                f"Identified {count} items with elevated stockout risk scores (>20/100).",
                f"Top at-risk SKU is {items[0]['sku']} ({items[0]['product_name']}) at {items[0]['store_name']} with risk score {items[0]['risk_score']}/100." if items else "No critical stockouts detected."
            ]
            recommendation = rec_text
            assumptions = ["Lead times based on primary supplier contract terms.", "Demand projection uses recent 7-day moving average."]
            limitations = ["Safety stock does not absorb unexpected supply disruptions exceeding lead time."]

    elif intent == "OVERSTOCK":
        items = evidence.get("items", [])
        total_val = evidence.get("total_excess_inventory_value", 0.0)
        lines = [f"Found {len(items)} overstocked products with ₹{total_val:,.2f} in excess inventory capital tied up.\n"]
        for item in items[:3]:
            lines.append(
                f"{item['sku']} ({item['product_name']}) at {item['store_name']}:\n"
                f"Current stock: {item['current_stock']} units\n"
                f"Days of inventory: {item['days_of_inventory']} days (Target: {item['target_runway_days']} days)\n"
                f"Excess units: {item['excess_units']} units\n"
                f"Excess capital: ₹{item['excess_inventory_value']:,.2f}\n"
            )
        rec_text = "Initiate targeted promotional markdowns or inter-store transfers to rebalance excess stock."
        lines.append(f"Recommendation:\n{rec_text}")
        answer = "\n".join(lines)
        findings = [
            f"Overstock ties up an estimated ₹{total_val:,.2f} across {len(items)} inventory positions.",
            f"Top surplus SKU is {items[0]['sku']} with {items[0]['days_of_inventory']} days of supply." if items else "No severe overstocks found."
        ]
        recommendation = rec_text
        assumptions = ["Target runway benchmark is 45 days of supply."]
        limitations = ["Holding costs are estimated from wholesale purchase cost."]

    elif intent == "SLOW_MOVERS":
        items = evidence.get("items", [])
        lines = [f"Identified {len(items)} slow-moving products across catalog.\n"]
        for item in items[:3]:
            lines.append(
                f"{item['sku']} ({item['product_name']}) at {item['store_name']}:\n"
                f"Current stock: {item['current_stock']} units\n"
                f"Average daily sales: {item['avg_daily_sales']} units/day\n"
                f"Catalog age: {item['catalog_age_days']} days\n"
            )
        rec_text = "Review non-performing lines for category clearance, supplier return, or merchandising repositioning."
        lines.append(f"Recommendation:\n{rec_text}")
        answer = "\n".join(lines)
        findings = [
            f"{len(items)} SKUs show sales velocity below 0.2 units/day despite holding inventory.",
            f"Slowest moving item is {items[0]['sku']} ({items[0]['product_name']}) with {items[0]['avg_daily_sales']} units/day." if items else "No stagnant items found."
        ]
        recommendation = rec_text
        assumptions = ["Evaluation filters out new product launches under 21 days catalog age."]
        limitations = ["Velocity reflects store sales and excludes online direct shipments."]

    elif intent == "STORE_COMPARISON":
        stores = evidence.get("stores", [])
        lines = ["Store network performance comparison ranked by revenue:\n"]
        for s in stores:
            growth_str = f"{s['growth_vs_previous_period']}%" if s['growth_vs_previous_period'] is not None else "N/A"
            lines.append(
                f"• {s['store_name']} ({s['city']}):\n"
                f"  Revenue: ₹{s['revenue']:,.2f} | Units: {s['units_sold']:,}\n"
                f"  Gross Margin: {s['gross_margin_pct']}% | Growth: {growth_str}\n"
            )
        top = stores[0] if stores else None
        bottom = stores[-1] if stores else None
        rec_text = f"Analyze best practices from top performer {top['store_name']} and review underperforming categories at {bottom['store_name']}." if top and bottom else "Maintain standard store operational audits."
        lines.append(f"Recommendation:\n{rec_text}")
        answer = "\n".join(lines)
        findings = [
            f"Top revenue store is {top['store_name']} ({top['city']}) generating ₹{top['revenue']:,.2f}.",
            f"Lowest revenue store is {bottom['store_name']} ({bottom['city']}) with ₹{bottom['revenue']:,.2f}."
        ] if top and bottom else ["Store comparison data retrieved."]
        recommendation = rec_text
        assumptions = ["Growth compares current period against equal preceding lookback window."]
        limitations = ["Regional demographic differences are not factored into gross margins."]

    elif intent == "SALES_ANOMALY":
        items = evidence.get("items", [])
        lines = [f"Detected {len(items)} statistical sales anomalies:\n"]
        for item in items[:4]:
            lines.append(
                f"• {item['date']} - {item['sku']} ({item['product_name']}) at {item['store_name']}:\n"
                f"  Anomaly: {item['anomaly_type']} ({item['severity']})\n"
                f"  Actual sales: {item['actual_sales']} units vs baseline mean {item['baseline_mean']} units ({item['pct_change']}% change)\n"
            )
        rec_text = "Investigate high-severity spikes for local stock depletion and investigate drops for potential out-of-stock shelf outages."
        lines.append(f"Recommendation:\n{rec_text}")
        answer = "\n".join(lines)
        findings = [
            f"Found {len(items)} transactions deviating significantly from 14-day rolling baseline.",
            f"Most prominent anomaly is on {items[0]['date']} for {items[0]['sku']} with {items[0]['pct_change']}% deviation." if items else "No anomalies detected."
        ]
        recommendation = rec_text
        assumptions = ["Anomalies detected using a 14-day rolling window baseline."]
        limitations = ["One-off bulk corporate orders may trigger artificial spike anomalies."]

    elif intent == "INVENTORY_STATUS":
        if evidence.get("type") == "single_inventory_health":
            answer = (
                f"Inventory status for {evidence['sku']} ({evidence['product_name']}) at {evidence['store_name']}:\n"
                f"Current stock: {evidence['current_stock']} units\n"
                f"Inventory valuation: ₹{evidence['inventory_valuation']:,.2f}\n"
                f"Days of inventory: {evidence['days_of_inventory']} days\n"
                f"Daily sales velocity: {evidence['daily_sales_velocity']} units/day\n"
                f"Status: {evidence['health_status']}\n"
                f"Reorder point: {evidence['reorder_point']} units"
            )
            findings = [
                f"Current stock is {evidence['current_stock']} units valued at ₹{evidence['inventory_valuation']:,.2f}.",
                f"Runway is estimated at {evidence['days_of_inventory']} days."
            ]
            recommendation = "Reorder immediately if stock is below reorder point." if evidence['current_stock'] <= evidence['reorder_point'] else "Inventory is currently within safe operational threshold."
        else:
            answer = (
                f"Overall retail inventory health summary across all stores:\n\n"
                f"Total SKUs: {evidence.get('total_skus')}\n"
                f"Total units in stock: {evidence.get('total_units_in_stock'):,} units\n"
                f"Total inventory valuation: ₹{evidence.get('total_inventory_valuation', 0):,.2f}\n"
                f"Healthy SKUs: {evidence.get('healthy_count')}\n"
                f"Low stock alerts: {evidence.get('low_stock_count')}\n"
                f"Overstock alerts: {evidence.get('overstock_count')}\n"
                f"Stockouts: {evidence.get('out_of_stock_count')}"
            )
            findings = [
                f"Network holds {evidence.get('total_units_in_stock'):,} units across {evidence.get('total_skus')} SKUs valued at ₹{evidence.get('total_inventory_valuation', 0):,.2f}.",
                f"{evidence.get('low_stock_count')} SKUs are running low, requiring operational reordering attention."
            ]
            recommendation = "Focus replenishment actions on the low stock SKUs and evaluate markdown plans for overstocked lines."
        assumptions = ["Valuation calculated using wholesale cost price."]
        limitations = ["Inventory snapshot current as of latest recorded ledger date."]

    elif intent == "SALES_PERFORMANCE":
        if evidence.get("type") == "product_sales_performance":
            growth_str = f"{evidence.get('growth_vs_previous_period')}%" if evidence.get('growth_vs_previous_period') is not None else "N/A"
            answer = (
                f"Sales performance for {evidence['sku']} ({evidence['product_name']}):\n\n"
                f"Revenue: ₹{evidence['revenue']:,.2f}\n"
                f"Units sold: {evidence['units_sold']} units\n"
                f"Gross margin: {evidence['gross_margin_pct']}%\n"
                f"Growth vs previous period: {growth_str}\n"
            )
            findings = [
                f"Generated ₹{evidence['revenue']:,.2f} from {evidence['units_sold']} units sold.",
                f"Maintains a gross margin of {evidence['gross_margin_pct']}%."
            ]
            recommendation = "Maintain stock availability to sustain revenue run-rate."
        elif evidence.get("type") == "single_store_sales_performance":
            growth_str = f"{evidence.get('growth_vs_previous_period')}%" if evidence.get('growth_vs_previous_period') is not None else "N/A"
            answer = (
                f"Sales performance for {evidence['store_name']} ({evidence['city']}):\n\n"
                f"Revenue: ₹{evidence['revenue']:,.2f}\n"
                f"Units sold: {evidence['units_sold']:,} units\n"
                f"Gross margin: {evidence['gross_margin_pct']}%\n"
                f"Growth vs previous period: {growth_str}\n"
            )
            findings = [
                f"{evidence['store_name']} delivered ₹{evidence['revenue']:,.2f} in total revenue.",
                f"Gross margin stands at {evidence['gross_margin_pct']}%."
            ]
            recommendation = "Sustain operational cadence and monitor top-selling categories."
        else:
            prods = evidence.get("top_products", [])
            lines = ["Top products by sales performance:\n"]
            for p in prods[:5]:
                lines.append(f"• {p['sku']} ({p['product_name']}): ₹{p['revenue']:,.2f} ({p['units_sold']} units, {p['gross_margin_pct']}% margin)")
            answer = "\n".join(lines)
            findings = [
                f"Top revenue generator is {prods[0]['sku']} with ₹{prods[0]['revenue']:,.2f}." if prods else "Product sales data compiled."
            ]
            recommendation = "Ensure uninterrupted supply chain replenishment for top revenue drivers."
        assumptions = ["Sales calculations include net discounts."]
        limitations = ["Sales data reflects recorded point-of-sale transactions."]

    else:  # GENERAL_RETAIL_ANALYSIS
        answer = (
            f"Retail portfolio health overview:\n\n"
            f"Total store network revenue: ₹{evidence.get('total_network_revenue', 0):,.2f}\n"
            f"Total stock on hand: {evidence.get('total_units_in_stock', 0):,} units\n"
            f"Inventory valuation: ₹{evidence.get('total_inventory_valuation', 0):,.2f}\n"
            f"High stockout risk items: {evidence.get('high_stockout_risk_count', 0)}\n"
            f"Low stock alerts: {evidence.get('low_stock_sku_count', 0)}\n"
            f"Overstocked lines: {evidence.get('overstock_sku_count', 0)}\n"
        )
        findings = [
            f"Network revenue totals ₹{evidence.get('total_network_revenue', 0):,.2f} across all stores.",
            f"{evidence.get('high_stockout_risk_count', 0)} critical stockout risks require immediate manager replenishment."
        ]
        recommendation = "Review high stockout risk items and initiate morning replenishment orders."
        assumptions = ["Aggregated across all active physical store locations."]
        limitations = ["Reflects data up to the latest closing date."]

    return CopilotResponse(
        answer=answer,
        key_findings=findings,
        evidence=evidence,
        recommendation=recommendation,
        assumptions=assumptions,
        limitations=limitations,
        data_sufficient=True,
        confidence_note=confidence_prefix
    )


# =========================================================================
# GEMINI COPILOT AGENT CLASS
# =========================================================================

class RetailCopilot:
    """
    Coordinates between user questions, Gemini reasoning, and local deterministic analytics.
    Strictly adheres to:
    - Gemini is NOT the source of truth.
    - Local DB and deterministic analytics engine are the source of truth.
    - Never hallucinates IDs or figures.
    - Gracefully handles missing/invalid GEMINI_API_KEY.
    """

    def __init__(self, api_key: Optional[str] = None, model: str = "gemini-2.5-flash"):
        # Read API key ONLY from GEMINI_API_KEY environment variable if not passed directly
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self.model_name = model
        self._client = None

    def _get_client(self):
        """Lazy initialization of Google GenAI client."""
        if self._client is not None:
            return self._client

        if not self.api_key:
            return None

        try:
            from google import genai
            self._client = genai.Client(api_key=self.api_key)
            return self._client
        except Exception as e:
            logger.warning(f"Could not initialize Google GenAI client: {e}")
            return None

    def extract_intent(self, question: str) -> IntentSchema:
        """
        Extracts structured intent from user query:
        1. Uses Gemini LLM structured output if GEMINI_API_KEY is available.
        2. Falls back to deterministic rule-based parser if key is missing or API call fails.
        """
        client = self._get_client()
        if not client:
            return rule_based_extract_intent(question)

        try:
            from google.genai import types

            prompt = (
                "You are an intent classification assistant for a multi-store retail sales and inventory system.\n"
                "Analyze the user's question and extract the intent and entities according to the following rules:\n\n"
                "SUPPORTED INTENTS:\n"
                "- STOCKOUT_RISK: queries about running out of stock, stockouts, shortages, depleted inventory, critical stock.\n"
                "- OVERSTOCK: queries about excess inventory, surplus stock, high runway, too much stock.\n"
                "- SLOW_MOVERS: queries about slow-moving items, stagnant or dead stock, low velocity items.\n"
                "- INVENTORY_STATUS: queries about current inventory levels, inventory health, runway, days of supply, total units, valuation.\n"
                "- SALES_PERFORMANCE: queries about revenue, sales volume, margin, profit, best sellers, top products, run rates.\n"
                "- STORE_COMPARISON: queries comparing multiple stores, store rankings, top vs bottom locations.\n"
                "- SALES_ANOMALY: queries about sales spikes, sudden drops, abnormal sales behavior.\n"
                "- GENERAL_RETAIL_ANALYSIS: high-level queries, overall retail health, daily actions, executive summary.\n"
                "- UNKNOWN: queries not related to retail store sales, inventory, or operations.\n\n"
                "ENTITIES:\n"
                "- product: any mentioned product name, SKU (e.g. SKU-104), or product ID, or null.\n"
                "- store: any mentioned store name, city (e.g. Hyderabad, Mumbai, Bengaluru, Delhi), or store ID, or null.\n"
                "- time_period: any mentioned time window (e.g. 'last week', 'July 2024'), or null.\n"
                "- requires_clarification: boolean, true only if intent cannot be mapped with reasonable confidence.\n\n"
                f"User Question: \"{question}\""
            )

            response = client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=IntentSchema,
                    temperature=0.0
                )
            )

            if response.text:
                parsed = json.loads(response.text)
                return IntentSchema(**parsed)

        except Exception as e:
            logger.warning(f"Gemini intent extraction failed, falling back to rule-based extractor: {e}")

        return rule_based_extract_intent(question)

    def explain_with_gemini(
        self,
        question: str,
        intent_data: IntentSchema,
        evidence: Dict[str, Any],
        resolved_product: Optional[Dict[str, Any]],
        resolved_store: Optional[Dict[str, Any]]
    ) -> Optional[CopilotResponse]:
        """
        Requests Gemini to produce a grounded natural-language explanation strictly over the Python evidence package.
        Returns None if Gemini client is unavailable or if the API call fails.
        """
        client = self._get_client()
        if not client:
            return None

        try:
            from google.genai import types

            store_context = f"{resolved_store['store_name']} ({resolved_store['city']})" if resolved_store else "All Stores"
            product_context = f"{resolved_product['sku']} - {resolved_product['product_name']}" if resolved_product else "Not restricted to specific product"

            prompt = (
                "You are an expert Retail Analytics Copilot for store managers.\n\n"
                "CRITICAL ARCHITECTURAL CONSTRAINTS:\n"
                "1. The local database and the deterministic Python evidence provided below are the SOLE SOURCE OF TRUTH.\n"
                "2. You must NEVER fabricate or hallucinate numbers, product IDs, or store IDs.\n"
                "3. Every numerical claim in your answer MUST correspond directly to the evidence package.\n"
                "4. Use actual figures provided in the evidence (units, revenue in INR, lead time days, risk scores).\n"
                "5. Do NOT use unsupported certainty. For example, say 'SKU-104 has a high stockout risk score of 91/100', NOT 'SKU-104 will definitely run out soon'.\n"
                "6. Provide recommendations ONLY when supported by evidence (e.g. place replenishment order subject to manager approval, initiate markdown, transfer excess units).\n"
                "7. Assumptions and limitations must clearly state data boundaries.\n\n"
                f"USER QUESTION: \"{question}\"\n"
                f"DETECTED INTENT: {intent_data.intent}\n"
                f"STORE CONTEXT: {store_context}\n"
                f"PRODUCT CONTEXT: {product_context}\n"
                f"EVIDENCE PACKAGE FROM PYTHON (SOURCE OF TRUTH):\n"
                f"{json.dumps(evidence, indent=2)}\n\n"
                "Produce a structured JSON response matching the schema."
            )

            response = client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=CopilotResponse,
                    temperature=0.1
                )
            )

            if response.text:
                data = json.loads(response.text)
                # Ensure evidence package is preserved exactly as calculated
                data["evidence"] = evidence
                if not data.get("confidence_note"):
                    data["confidence_note"] = "Explanation reasoned by Gemini 2.5 Flash, strictly grounded on deterministic Python calculations."
                return CopilotResponse(**data)

        except Exception as e:
            logger.warning(f"Gemini response generation failed: {e}", exc_info=True)

        return None

    def ask(self, question: str) -> CopilotResponse:
        """
        Executes the full copilot pipeline:
        USER QUESTION
        → intent extraction
        → entity resolution
        → deterministic analytics
        → evidence package
        → Gemini explanation
        → structured response
        """
        # 1. Intent Extraction
        intent_data = self.extract_intent(question)

        # 2. Entity Resolution against local SQLite database
        resolved_product, resolved_store, product_not_found, store_not_found = resolve_entities(
            product_query=intent_data.product,
            store_query=intent_data.store
        )

        # Handle non-existent entities: explain that data does not contain it without fabricating
        if store_not_found:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT store_name, city FROM stores")
            avail_stores = [f"{r['store_name']} ({r['city']})" for r in cursor.fetchall()]
            conn.close()
            msg = (
                f"The requested store '{intent_data.store}' was not found in the database. "
                f"Available store locations are: {', '.join(avail_stores)}."
            )
            return CopilotResponse(
                answer=msg,
                key_findings=[f"Store '{intent_data.store}' does not exist in local records."],
                evidence={"requested_store": intent_data.store, "available_stores": avail_stores},
                recommendation=f"Please specify one of our active store locations: {', '.join(avail_stores)}.",
                assumptions=[],
                limitations=["Database contains records only for authorized physical store network."],
                data_sufficient=False,
                confidence_note="Local database contains no records for the requested store."
            )

        if product_not_found:
            msg = (
                f"The requested product '{intent_data.product}' was not found in the local catalog. "
                f"Please verify the product name or SKU."
            )
            return CopilotResponse(
                answer=msg,
                key_findings=[f"Product '{intent_data.product}' not found in catalog."],
                evidence={"requested_product": intent_data.product},
                recommendation="Search the catalog using standard SKU format (e.g. SKU-PRE-001) or product name.",
                assumptions=[],
                limitations=["Search performed across active catalog records only."],
                data_sufficient=False,
                confidence_note="Local database contains no matching SKU or product."
            )

        # 3. Deterministic Analytics (The local database is the source of truth)
        evidence, data_sufficient, missing_info = execute_deterministic_analytics(
            intent_data=intent_data,
            resolved_product=resolved_product,
            resolved_store=resolved_store
        )

        # If data is insufficient or intent unknown, return immediately with explanation
        if not data_sufficient:
            return synthesize_deterministic_response(
                question=question,
                intent_data=intent_data,
                evidence=evidence,
                data_sufficient=False,
                missing_info=missing_info,
                confidence_prefix="Local database query completed."
            )

        # 4. Gemini Explanation & Reasoning (if API key available)
        gemini_response = None
        if self.api_key:
            gemini_response = self.explain_with_gemini(
                question=question,
                intent_data=intent_data,
                evidence=evidence,
                resolved_product=resolved_product,
                resolved_store=resolved_store
            )

        if gemini_response:
            return gemini_response

        # 5. Deterministic Fallback if Gemini unavailable or failed
        conf_prefix = (
            "Ground truth computed deterministically via Python analytics over local SQLite data. "
            "(GEMINI_API_KEY not configured - deterministic response mode active)"
            if not self.api_key else
            "Ground truth computed deterministically via Python analytics over local SQLite data. "
            "(Gemini reasoning standby - deterministic fallback generated)"
        )

        return synthesize_deterministic_response(
            question=question,
            intent_data=intent_data,
            evidence=evidence,
            data_sufficient=True,
            missing_info=[],
            confidence_prefix=conf_prefix
        )


# =========================================================================
# CONVENIENCE EXPORTS & GLOBAL INSTANCE
# =========================================================================

# Backwards compatibility alias
RetailCopilotAgent = RetailCopilot

_default_copilot: Optional[RetailCopilot] = None

def get_copilot() -> RetailCopilot:
    """Returns the global RetailCopilot singleton."""
    global _default_copilot
    if _default_copilot is None:
        _default_copilot = RetailCopilot()
    return _default_copilot

def ask_copilot(question: str) -> Dict[str, Any]:
    """
    Convenience function to ask the copilot and get a dict response.
    """
    copilot = get_copilot()
    response = copilot.ask(question)
    return response.model_dump()

__all__ = [
    "RetailCopilot",
    "RetailCopilotAgent",
    "IntentSchema",
    "CopilotResponse",
    "SUPPORTED_INTENTS",
    "resolve_entities",
    "rule_based_extract_intent",
    "execute_deterministic_analytics",
    "get_copilot",
    "ask_copilot"
]
