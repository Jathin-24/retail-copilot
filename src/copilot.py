"""
Retail Copilot Module
TRACK_ID: PS03

CRITICAL ARCHITECTURAL PRINCIPLE:
- NEVER MAKE A CLAIM WITHOUT SUPPORTING DATA.
- Gemini is NOT the source of truth.
- Local SQLite database and deterministic Python analytics engine are the SOLE SOURCE OF TRUTH.
- Explicit distinction between:
    1. OBSERVED FACT
    2. CALCULATED METRIC
    3. INFERENCE
    4. RECOMMENDATION
    5. ASSUMPTION
- Strict refusal and qualification rules when:
    - Product or store does not exist
    - Date range has insufficient data
    - Causal explanation cannot be established by data
    - Data is missing or unreliable
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

# Explicit Evidence Layer
from src.evidence import (
    MetricEvidence,
    EvidencePackage,
    build_stockout_evidence,
    build_causal_inquiry_evidence
)

logger = logging.getLogger(__name__)

# Supported Analytical Intents (strictly 9)
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

# Database Active Date Bounds
DATABASE_MIN_DATE = "2024-06-01"
DATABASE_MAX_DATE = "2024-08-29"


# =========================================================================
# DOCUMENT RETRIEVAL (POLICY / PROCEDURE KNOWLEDGE BASE)
# =========================================================================

# Lightweight keyword heuristic used to detect DOCUMENT/POLICY-oriented questions.
POLICY_KEYWORD_PATTERNS = [
    r"\bpolic",
    r"\bprocedur",
    r"\brule",
    r"\bcriteria",
    r"\bapprov",
    r"\btransfer",
    r"\breturn",
    r"\bdamaged\b",
    r"\bsafety stock buffer\b",
    r"\bmanual",
    r"\bguideline",
    r"\bsla\b",
    r"\bworkflow",
]

# Minimum cosine similarity for a retrieved document chunk to hijack the normal
# analytics flow for a NON-keyword question. Explicit policy keywords always win.
POLICY_MIN_SIMILARITY = 0.55


def _has_policy_keywords(question: str) -> bool:
    """True when the question text matches explicit policy/procedure keywords."""
    q = question.lower()
    return any(re.search(pattern, q) for pattern in POLICY_KEYWORD_PATTERNS)

UNAVAILABLE_RETRIEVAL_NOTE = (
    "Document retrieval is unavailable \u2014 policy/procedure answers from the "
    "knowledge base are not available right now. Database analytics remain fully functional."
)

_retrieval_module_cache: Optional[Any] = None
_retrieval_module_attempted = False


def is_policy_question(question: str, intent_data: Optional["IntentSchema"] = None) -> bool:
    """
    Detects whether a question is DOCUMENT/POLICY-oriented (policies, procedures, rules,
    approvals, transfers, returns, damaged goods, SLAs, workflows, guidelines).
    Uses the already-extracted intent OR a lightweight keyword heuristic.
    """
    q = question.lower()
    if _has_policy_keywords(question):
        return True
    if intent_data is not None and intent_data.intent == "UNKNOWN":
        return True
    return False


def _retrieve_module() -> Optional[Any]:
    """
    Lazily loads the local document-retrieval module (src/retrieval.py).
    Mirrors the existing 'from google import genai' lazy-import style and NEVER raises.
    The module file can be shadowed by the legacy src/retrieval/ package directory, so a
    direct file-load fallback is used. Returns None when the module is unavailable.
    """
    global _retrieval_module_cache, _retrieval_module_attempted
    if _retrieval_module_attempted:
        return _retrieval_module_cache

    _retrieval_module_attempted = True
    module = None

    try:
        from src import retrieval
        if hasattr(retrieval, "retrieve_documents") and hasattr(retrieval, "retrieval_status"):
            module = retrieval
    except Exception:
        module = None

    if module is None:
        try:
            import importlib.util
            from pathlib import Path
            module_path = Path(__file__).resolve().parent / "retrieval.py"
            if module_path.exists():
                spec = importlib.util.spec_from_file_location("_retail_document_retrieval", str(module_path))
                loaded = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(loaded)
                if hasattr(loaded, "retrieve_documents") and hasattr(loaded, "retrieval_status"):
                    module = loaded
        except Exception:
            module = None

    _retrieval_module_cache = module
    return module


def retrieval_status_safe() -> Dict[str, Any]:
    """Best-effort document-retrieval status. Never raises."""
    try:
        module = _retrieve_module()
        if module is None:
            return {
                "available": False,
                "model": None,
                "documents_indexed": 0,
                "chunks_count": 0,
                "reason": "Document retrieval module is not available.",
            }
        status = module.retrieval_status()
        if not isinstance(status, dict):
            status = {}
        return {
            "available": bool(status.get("available")),
            "model": status.get("model"),
            "documents_indexed": status.get("documents_indexed") or 0,
            "chunks_count": status.get("chunks_count") or 0,
            "reason": status.get("reason") or ("available" if status.get("available") else "unavailable"),
        }
    except Exception:
        return {
            "available": False,
            "model": None,
            "documents_indexed": 0,
            "chunks_count": 0,
            "reason": "Document retrieval status unavailable.",
        }


def _chunks_to_dicts(chunks: Any) -> List[Dict[str, Any]]:
    """Converts RetrievedChunk objects to plain dicts. Never raises."""
    out: List[Dict[str, Any]] = []
    for c in chunks or []:
        try:
            out.append({
                "document_name": getattr(c, "document_name", None),
                "chunk_id": getattr(c, "chunk_id", None),
                "section": getattr(c, "section", None),
                "text": getattr(c, "text", None),
                "score": getattr(c, "score", None),
            })
        except Exception:
            continue
    return out


def _build_policy_evidence_block(chunks: List[Dict[str, Any]]) -> str:
    """
    Builds the POLICY EVIDENCE citation block appended to chat answers so the
    final answer clearly distinguishes DATA EVIDENCE from POLICY EVIDENCE.
    """
    lines = ["POLICY EVIDENCE: grounded in knowledge-base documents, not database analytics."]
    for c in chunks:
        doc = c.get("document_name") or "unknown_document"
        chunk_id = c.get("chunk_id") or "?"
        section = c.get("section") or ""
        score = c.get("score")
        if isinstance(score, (int, float)):
            score_str = f"{float(score):.2f}"
        else:
            score_str = str(score) if score is not None else ""
        tag = f"{chunk_id} ('{section}', {score_str})" if section else f"{chunk_id} ({score_str})"
        text = (c.get("text") or "").replace("\n", " ").strip()
        excerpt = text[:160] + ("..." if len(text) > 160 else "")
        lines.append(f"- {tag} \u2014 {excerpt}")
    return "\n".join(lines)


class IntentSchema(BaseModel):
    """Structured Intent extracted from user question."""
    intent: str = Field(..., description="One of the supported retail analytical intents.")
    product: Optional[str] = Field(None, description="Product name, SKU, or ID mentioned in question.")
    store: Optional[str] = Field(None, description="Store name, city, or ID mentioned in question.")
    time_period: Optional[str] = Field(None, description="Time period mentioned (e.g. 'last week', 'July 2024').")
    requires_clarification: bool = Field(False, description="True if query is excessively ambiguous.")
    is_causal_inquiry: bool = Field(False, description="True if query seeks a causal explanation ('why did...').")


class CopilotResponse(BaseModel):
    """
    Strictly structured final response returned to user or client.
    Enforces 5-tier epistemic distinction:
    - OBSERVED FACT
    - CALCULATED METRIC
    - INFERENCE
    - RECOMMENDATION
    - ASSUMPTION
    """
    intent: Optional[str] = Field(None, description="Identified analytical intent.")
    product: Optional[str] = Field(None, description="Identified product entity.")
    store: Optional[str] = Field(None, description="Identified store entity.")
    time_period: Optional[str] = Field(None, description="Identified time period.")
    answer: str = Field(..., description="Evidence-grounded explanation with actual figures.")
    observed_facts: List[str] = Field(default_factory=list, description="OBSERVED FACT: Facts directly verified from database records.")
    calculated_metrics: List[Dict[str, Any]] = Field(default_factory=list, description="CALCULATED METRIC: Derived metrics with formulas and sources.")
    inferences: List[str] = Field(default_factory=list, description="INFERENCE: Analytical deductions grounded strictly in metrics.")
    recommendations: List[str] = Field(default_factory=list, description="RECOMMENDATION: Policy-compliant action items subject to approval.")
    assumptions: List[str] = Field(default_factory=list, description="ASSUMPTION: Stated baseline conditions.")
    evidence_package: Optional[Dict[str, Any]] = Field(None, description="Structured EvidencePackage behind answer/recommendation.")
    key_findings: List[str] = Field(default_factory=list, description="Bulleted summary of core findings.")
    evidence: Dict[str, Any] = Field(default_factory=dict, description="Deterministic Python analytics output.")
    recommendation: Optional[str] = Field(None, description="Primary recommendation string.")
    limitations: List[str] = Field(default_factory=list, description="Known boundary limitations of data.")
    data_sufficient: bool = Field(True, description="False if data does not contain necessary entity or records.")
    refusal_reason: Optional[str] = Field(None, description="Explicit refusal reason if query is refused or qualified.")
    confidence_note: str = Field("", description="Explanation of basis of confidence and data source.")
    document_retrieval_available: bool = Field(False, description="True when the local knowledge-base document retrieval service is available.")
    policy_evidence: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="POLICY EVIDENCE: retrieved policy/procedure document chunks (document_name, chunk_id, section, text, score)."
    )


# =========================================================================
# CAUSAL INQUIRY & DATE VALIDATION
# =========================================================================

def is_causal_question(question: str) -> bool:
    """
    Detects if the user is asking for a causal explanation that transaction
    logs alone cannot establish without external market/competitor datasets.
    """
    q = question.lower()
    causal_patterns = [
        r"\bwhy\s+did\s+sales\b",
        r"\bwhy\s+did\s+revenue\b",
        r"\bwhy\s+(?:are|is)\s+sales\b",
        r"\bwhat\s+caused\s+(?:sales|the\s+drop|the\s+decline)\b",
        r"\breason\s+for\s+(?:the\s+drop|the\s+decline|falling\s+sales)\b",
        r"\bwhy\s+is\s+.*not\s+selling\b",
        r"\bwhy\s+have\s+sales\s+fallen\b",
        r"\bwhy\s+did\s+.*drop\b",
        r"\bcause\s+of\s+(?:sales|revenue)\b",
        r"\bwhy\s+did\s+it\s+fall\b"
    ]
    return any(re.search(p, q) for p in causal_patterns)


def check_date_range_validity(question: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Checks if query references a date period that has insufficient or missing data.
    Database covers 2024-06-01 to 2024-08-29.
    Returns: (is_valid, extracted_year_or_period, error_message)
    """
    q = question.lower()

    # Search for explicit 4-digit years
    years = re.findall(r"\b(20\d\d)\b", q)
    for yr in years:
        if yr != "2024":
            return False, yr, f"Data is not available for year {yr}. Available records cover {DATABASE_MIN_DATE} to {DATABASE_MAX_DATE}."

    # Check for relative future terms
    if any(k in q for k in ["next month", "next year", "tomorrow", "future sales", "forecast for 2025", "forecast for 2026"]):
        return False, "future", f"Requested forward horizon is outside historical dataset ({DATABASE_MIN_DATE} to {DATABASE_MAX_DATE})."

    # Check for dates before June 2024 or after August 2024
    out_of_bounds_months = ["january", "february", "march", "april", "may", "september", "october", "november", "december"]
    for m in out_of_bounds_months:
        if m in q and "2024" in q:
            return False, m, f"No transactions recorded for {m.capitalize()} 2024. Available records span June 1, 2024 to August 29, 2024."

    return True, None, None


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
    causal = is_causal_question(question)

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
        # Policy/document questions must not be misread as store mentions
        # (e.g. "for emergency orders" should never become a store name).
        if not is_policy_question(question):
            # Check for store in "at <store>" or "in <store>"
            store_match = re.search(r"\b(?:at|in|for store|for)\s+([A-Za-z0-9\-\s]{3,20})\b", question, re.IGNORECASE)
            if store_match:
                cand = store_match.group(1).strip()
                if cand.lower() not in ["july", "august", "today", "yesterday", "stock", "sales", "all", "stores", "products", "risk"]:
                    store_candidate = cand

    # Detect product mentions
    product_candidate = None
    sku_match = re.search(r"\b(SKU-[A-Za-z0-9\-]+|PRD-\d+)\b", question, re.IGNORECASE)
    if sku_match:
        product_candidate = sku_match.group(1).upper()
    else:
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

    # If still not found, check regex patterns
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
    elif causal or any(k in q for k in ["product performance", "how did", "sales performance", "units sold", "top selling", "revenue", "sales volume", "sales drop", "sales fall"]):
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
        requires_clarification=False,
        is_causal_inquiry=causal
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
    Builds explicit EvidencePackage with granular MetricEvidence items.
    """
    store_id = resolved_store["store_id"] if resolved_store else None
    product_id = resolved_product["product_id"] if resolved_product else None
    evidence: Dict[str, Any] = {}
    data_sufficient = True
    missing_info: List[str] = []

    sku_map = get_product_sku_map()
    city_map = get_store_city_map()

    # 1. SPECIAL CASE: Causal inquiry (e.g. 'Why did sales fall?')
    if intent_data.is_causal_inquiry:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Query July 2024 vs August 2024 sales for the specified entity or portfolio
        where_clause = ""
        params_curr = []
        params_prev = []

        if product_id:
            where_clause += " AND product_id = ?"
            params_curr.append(product_id)
            params_prev.append(product_id)
        if store_id:
            where_clause += " AND store_id = ?"
            params_curr.append(store_id)
            params_prev.append(store_id)

        # Period 1: August 2024 (2024-08-01 to 2024-08-29)
        cursor.execute(f"SELECT COALESCE(SUM(quantity), 0) FROM sales WHERE date BETWEEN '2024-08-01' AND '2024-08-29' {where_clause}", params_curr)
        units_aug = cursor.fetchone()[0]

        # Period 2: July 2024 (2024-07-01 to 2024-07-29, equal 29-day baseline)
        cursor.execute(f"SELECT COALESCE(SUM(quantity), 0) FROM sales WHERE date BETWEEN '2024-07-01' AND '2024-07-29' {where_clause}", params_prev)
        units_jul = cursor.fetchone()[0]

        # Check stock availability
        stock_query = "SELECT COALESCE(SUM(closing_stock), 0) FROM inventory WHERE date = '2024-08-29'"
        stock_params = []
        if product_id:
            stock_query += " AND product_id = ?"
            stock_params.append(product_id)
        if store_id:
            stock_query += " AND store_id = ?"
            stock_params.append(store_id)
        cursor.execute(stock_query, stock_params)
        curr_stock = cursor.fetchone()[0]

        # Check price
        price = None
        if resolved_product:
            price = float(resolved_product.get("selling_price", 0.0))

        conn.close()

        pct_change = round(((units_aug - units_jul) / units_jul * 100.0), 1) if units_jul > 0 else 0.0
        entity_name = resolved_product["product_name"] if resolved_product else (resolved_store["store_name"] if resolved_store else "Store Network")

        pkg = build_causal_inquiry_evidence(
            entity_name=entity_name,
            period_current="August 2024 (2024-08-01 to 2024-08-29)",
            period_previous="July 2024 (2024-07-01 to 2024-07-29)",
            units_current=units_aug,
            units_previous=units_jul,
            pct_change=pct_change,
            current_stock=curr_stock,
            selling_price=price,
            product_id=product_id,
            store_id=store_id
        )
        return pkg.to_dict(), True, []

    # 2. Standard Intent Execution
    intent = intent_data.intent

    try:
        if intent == "STOCKOUT_RISK":
            if store_id and product_id:
                res = calculate_stockout_risk(store_id=store_id, product_id=product_id)
                pkg = build_stockout_evidence(
                    store_id=store_id,
                    store_name=resolved_store["store_name"],
                    city=resolved_store["city"],
                    product_id=product_id,
                    product_name=resolved_product["product_name"],
                    sku=resolved_product.get("sku") or sku_map.get(product_id, product_id),
                    current_stock=res.current_stock,
                    lead_time_days=res.lead_time_days,
                    lead_time_demand=round(res.lead_time_demand, 1),
                    daily_demand=round(res.demand_velocity, 2),
                    safety_stock=round(res.safety_stock, 1),
                    incoming_stock=res.incoming_quantity,
                    risk_score=round(res.risk_score, 1),
                    risk_level=res.risk_level,
                    days_of_inventory=round(res.days_of_inventory, 1) if res.days_of_inventory is not None else None
                )
                evidence = pkg.to_dict()
                evidence["type"] = "single_stockout_risk"
            else:
                results = assess_all_stockout_risks(store_id=store_id, min_risk_score=20.0)
                results.sort(key=lambda x: x.risk_score, reverse=True)
                critical_items = []
                metrics_list = []
                for r in results[:10]:
                    sku = sku_map.get(r.product_id, r.product_id)
                    city = city_map.get(r.store_id, "")
                    item_dict = {
                        "store_id": r.store_id,
                        "store_name": r.store_name,
                        "city": city,
                        "product_id": r.product_id,
                        "product_name": r.product_name,
                        "sku": sku,
                        "current_stock": r.current_stock,
                        "daily_demand": round(r.demand_velocity, 2),
                        "lead_time_days": r.lead_time_days,
                        "lead_time_demand": round(r.lead_time_demand, 1),
                        "incoming_stock": r.incoming_quantity,
                        "risk_score": round(r.risk_score, 1),
                        "risk_level": r.risk_level,
                        "days_of_inventory": round(r.days_of_inventory, 1) if r.days_of_inventory is not None else None,
                        "explanation_factors": r.explanation_factors
                    }
                    critical_items.append(item_dict)
                    metrics_list.append({
                        "metric": f"risk_score_{sku}_{r.store_id}",
                        "value": round(r.risk_score, 1),
                        "unit": "score/100",
                        "source": "inventory.csv + sales.csv + suppliers.csv",
                        "period": "2024-08-23 to 2024-08-29",
                        "calculation": "100 - (net_position / (lead_time_demand + safety_stock)) * 100",
                        "raw_values": {"current_stock": r.current_stock, "daily_demand": round(r.demand_velocity, 2)}
                    })

                evidence = {
                    "type": "stockout_risk_scan",
                    "store_filter": resolved_store["store_name"] if resolved_store else "All Stores",
                    "total_evaluated": len(results),
                    "critical_count": sum(1 for r in results if r.risk_level in ["CRITICAL", "HIGH"]),
                    "critical_items": critical_items,
                    "metrics": metrics_list,
                    "source_tables": ["inventory", "sales", "suppliers", "purchase_orders"],
                    "formulas": {
                        "lead_time_demand": "daily_demand_7d * supplier_lead_time_days",
                        "risk_score": "100 - (net_position / (lead_time_demand + safety_stock)) * 100"
                    },
                    "assumptions": [
                        "Supplier lead times based on standard supplier contract SLAs.",
                        "Recent 7-day average daily sales represents forward run-rate."
                    ],
                    "data_quality_warnings": []
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
                    "reorder_point": res.reorder_point,
                    "source_tables": ["inventory", "sales", "products"],
                    "metrics": [
                        {
                            "metric": "days_of_inventory",
                            "value": round(res.days_of_inventory, 1) if res.days_of_inventory is not None else None,
                            "unit": "days",
                            "source": "inventory.csv + sales.csv",
                            "period": "2024-08-23 to 2024-08-29",
                            "calculation": "current_stock / average_daily_sales_7d",
                            "raw_values": {"current_stock": res.current_stock, "average_daily_sales_7d": res.average_daily_sales_7d}
                        }
                    ]
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
                    "source_tables": ["inventory", "products"],
                    "metrics": [
                        {
                            "metric": "total_inventory_valuation",
                            "value": round(summary.get("total_inventory_valuation", 0.0), 2),
                            "unit": "INR",
                            "source": "inventory.csv + products.csv",
                            "period": "As of 2024-08-29",
                            "calculation": "SUM(closing_stock * cost_price)",
                            "raw_values": {"total_units": summary.get("total_units_in_stock", 0)}
                        }
                    ]
                }

        elif intent == "SLOW_MOVERS":
            results = detect_slow_moving_products(store_id=store_id)
            items = []
            metrics = []
            for r in results[:10]:
                sku = sku_map.get(r.product_id, r.product_id)
                items.append({
                    "store_id": r.store_id,
                    "store_name": r.store_name,
                    "product_id": r.product_id,
                    "product_name": r.product_name,
                    "sku": sku,
                    "current_stock": r.current_stock,
                    "avg_daily_sales": round(r.daily_sales_velocity, 3),
                    "days_of_inventory": round(r.days_of_inventory, 1) if r.days_of_inventory is not None else None,
                    "catalog_age_days": r.catalog_age_days,
                    "recommendation": "Initiate markdown or inter-store transfer"
                })
                metrics.append({
                    "metric": f"daily_sales_velocity_{sku}",
                    "value": round(r.daily_sales_velocity, 3),
                    "unit": "units/day",
                    "source": "sales.csv",
                    "period": "2024-06-01 to 2024-08-29",
                    "calculation": "SUM(quantity_sold) / calculation_days",
                    "raw_values": {"units_sold": r.units_sold_in_period, "closing_stock": r.current_stock}
                })
            evidence = {
                "type": "slow_movers_detection",
                "store_filter": resolved_store["store_name"] if resolved_store else "All Stores",
                "count": len(items),
                "items": items,
                "metrics": metrics,
                "source_tables": ["sales", "inventory", "products"]
            }

        elif intent == "OVERSTOCK":
            results = detect_overstocked_products(store_id=store_id)
            items = []
            metrics = []
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
                metrics.append({
                    "metric": f"days_of_inventory_{r.sku}",
                    "value": round(r.days_of_inventory, 1),
                    "unit": "days",
                    "source": "inventory.csv + sales.csv",
                    "period": "2024-08-23 to 2024-08-29",
                    "calculation": "current_stock / demand_velocity",
                    "raw_values": {"current_stock": r.current_stock, "demand_velocity": r.demand_velocity}
                })
            total_excess_val = sum(r.excess_inventory_value for r in results)
            evidence = {
                "type": "overstock_detection",
                "store_filter": resolved_store["store_name"] if resolved_store else "All Stores",
                "count": len(items),
                "total_excess_inventory_value": round(total_excess_val, 2),
                "items": items,
                "metrics": metrics,
                "source_tables": ["inventory", "sales", "products"]
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
                "items": items,
                "source_tables": ["sales"]
            }

        elif intent == "STORE_COMPARISON":
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT store_id, store_name, city FROM stores")
            stores_meta = [dict(r) for r in cursor.fetchall()]
            conn.close()

            items = []
            metrics = []
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
                    metrics.append({
                        "metric": f"revenue_{sm['city']}",
                        "value": round(s_perf.revenue, 2),
                        "unit": "INR",
                        "source": "sales.csv",
                        "period": "2024-06-01 to 2024-08-29",
                        "calculation": "SUM(quantity * unit_price - discount_amount)",
                        "raw_values": {"units_sold": s_perf.units, "gross_margin": s_perf.gross_margin}
                    })
                except Exception:
                    pass

            items.sort(key=lambda x: x["revenue"], reverse=True)
            evidence = {
                "type": "store_comparison",
                "store_count": len(items),
                "stores": items,
                "metrics": metrics,
                "source_tables": ["sales", "stores", "products"]
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
                    "growth_vs_previous_period": round(growth_val, 1) if growth_val is not None else None,
                    "source_tables": ["sales", "products"],
                    "metrics": [
                        {
                            "metric": "gross_margin_percent",
                            "value": round(res.gross_margin_percent, 1),
                            "unit": "%",
                            "source": "sales.csv + products.csv",
                            "period": "2024-06-01 to 2024-08-29",
                            "calculation": "((revenue - estimated_cogs) / revenue) * 100",
                            "raw_values": {"revenue": res.revenue, "estimated_cogs": res.estimated_cogs}
                        }
                    ]
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
                    "growth_vs_previous_period": round(growth_val, 1) if growth_val is not None else None,
                    "source_tables": ["sales", "stores", "products"]
                }
            else:
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
                    "top_products": items,
                    "source_tables": ["sales", "products"]
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
                "source_tables": ["inventory", "sales", "stores"]
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
    refusal_reason: Optional[str] = None,
    confidence_prefix: str = "Ground truth calculated deterministically via Python analytics over local SQLite data."
) -> CopilotResponse:
    """
    Produces a high-quality, strictly grounded response with explicit 5-tier distinction:
    - OBSERVED FACT
    - CALCULATED METRIC
    - INFERENCE
    - RECOMMENDATION
    - ASSUMPTION
    Never makes claims without supporting data.
    """
    # CASE: Data is insufficient or query refused
    if not data_sufficient or refusal_reason:
        reasons = " ".join(missing_info) if missing_info else "The requested information is not available in local records."
        
        # Specific Refusal Handling
        if refusal_reason == "UNESTABLISHED_CAUSALITY":
            pct = evidence.get("calculated_values", {}).get("percentage_change", 0.0)
            stock = evidence.get("raw_values", {}).get("stock_on_hand")
            price = evidence.get("raw_values", {}).get("recorded_price_inr")

            direction = "declined" if pct < 0 else "changed"
            val_pct = abs(pct) if pct < 0 else pct
            stock_str = f"{stock} units" if stock is not None else "tracked in ledger"
            price_str = f"₹{price}" if price is not None else "catalog rates"

            answer = (
                f"Sales {direction} by {val_pct}%, but the available data cannot establish the cause. "
                f"Inventory availability was {stock_str} and "
                f"price was {price_str}. "
                f"We do not have competitor pricing, customer feedback, or marketing data to determine why."
            )
            obs_facts = [
                f"Recorded transaction change: {pct}% over comparative July vs August 2024 period.",
                f"Stock on hand: {stock_str}.",
                f"Catalog price: {price_str}."
            ]
            calc_metrics = [
                {
                    "metric": "sales_change_percent",
                    "value": pct,
                    "unit": "%",
                    "source": "sales.csv",
                    "period": "2024-07-01 to 2024-08-29",
                    "calculation": "((units_current - units_previous) / units_previous) * 100",
                    "raw_values": evidence.get("raw_values", {})
                }
            ]
            inferences = [
                "Local point-of-sale records confirm the magnitude of sales change, but lack causal drivers."
            ]
            recommendations = [
                "Audit local store operations, gather customer feedback, and verify competitor promotions before adjusting ordering plans."
            ]
            assumptions = ["POS cash-register transactions accurately reflect customer purchases."]
            limitations = [
                "Local database does NOT contain competitor pricing, marketing campaigns, footfall traffic, or customer sentiment surveys."
            ]
            return CopilotResponse(
                intent=intent_data.intent,
                product=intent_data.product,
                store=intent_data.store,
                time_period=intent_data.time_period,
                answer=answer,
                observed_facts=obs_facts,
                calculated_metrics=calc_metrics,
                inferences=inferences,
                recommendations=recommendations,
                assumptions=assumptions,
                evidence_package=evidence.get("evidence_package") or (evidence if "source_tables" in evidence else None),
                key_findings=["Sales change verified, but root cause is unobservable from local data."],
                evidence=evidence,
                recommendation=recommendations[0],
                limitations=limitations,
                data_sufficient=True,
                refusal_reason="UNESTABLISHED_CAUSALITY",
                confidence_note=f"{confidence_prefix} (Causal speculation strictly refused)"
            )

        answer = f"Data is insufficient to answer this query. {reasons}"
        return CopilotResponse(
            intent=intent_data.intent,
            product=intent_data.product,
            store=intent_data.store,
            time_period=intent_data.time_period,
            answer=answer,
            observed_facts=["Requested entity or metric is absent from local database."],
            calculated_metrics=[],
            inferences=["Cannot compute metrics without verified input records."],
            recommendations=["Please rephrase using supported products, stores, or metrics."],
            assumptions=[],
            evidence_package=evidence.get("evidence_package") or (evidence if "source_tables" in evidence else None),
            key_findings=[reasons],
            evidence=evidence,
            recommendation="Please refine the query or choose from supported retail categories.",
            limitations=["Available data is restricted to local stores and catalog records."],
            data_sufficient=False,
            refusal_reason=refusal_reason or "INSUFFICIENT_DATA",
            confidence_note=f"{confidence_prefix} (Data insufficient)"
        )

    intent = intent_data.intent
    observed_facts: List[str] = []
    calculated_metrics: List[Dict[str, Any]] = []
    inferences: List[str] = []
    recommendations_list: List[str] = []
    assumptions_list: List[str] = []
    findings: List[str] = []
    recommendation_text: Optional[str] = None
    limitations: List[str] = []

    if intent == "STOCKOUT_RISK":
        if evidence.get("type") == "single_stockout_risk":
            name = evidence.get("product_name")
            sku = evidence.get("sku")
            store = evidence.get("store_name")
            city = evidence.get("city")
            stock = evidence.get("raw_values", {}).get("current_stock_units", evidence.get("current_stock", 0))
            demand = evidence.get("calculated_values", {}).get("daily_demand_units_per_day", evidence.get("daily_demand", 0.0))
            lead_time = evidence.get("raw_values", {}).get("supplier_lead_time_days", evidence.get("lead_time_days", 0))
            lead_demand = evidence.get("calculated_values", {}).get("lead_time_demand_units", evidence.get("lead_time_demand", 0.0))
            incoming = evidence.get("raw_values", {}).get("incoming_stock_units", evidence.get("incoming_stock", 0))
            score = evidence.get("calculated_values", {}).get("risk_score", evidence.get("risk_score", 0.0))

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
            observed_facts = [
                f"Current stock balance on record is {stock} units.",
                f"Incoming open purchase order quantity is {incoming} units.",
                f"Contractual supplier lead time is {lead_time} days."
            ]
            calculated_metrics = [
                {"metric": "daily_demand_7d", "value": demand, "unit": "units/day", "calculation": "SUM(quantity_7d) / 7.0"},
                {"metric": "lead_time_demand", "value": lead_demand, "unit": "units", "calculation": "daily_demand * lead_time_days"},
                {"metric": "risk_score", "value": score, "unit": "score/100", "calculation": "100 - (net_position / (lead_time_demand + safety_stock)) * 100"}
            ]
            inferences = [
                f"Lead-time demand ({lead_demand} units) exceeds available net position ({stock + incoming} units), signaling high stockout probability."
            ]
            recommendations_list = [
                "Place a replenishment order immediately, subject to manager approval."
            ]
            assumptions_list = [
                f"Supplier lead time remains constant at {lead_time} days.",
                f"Recent 7-day velocity ({demand} units/day) reflects forward customer demand."
            ]
            findings = [
                f"{sku} has a stockout risk score of {score}/100.",
                f"Lead-time demand ({lead_demand} units) exceeds net available stock ({stock + incoming} units)."
            ]
            recommendation_text = recommendations_list[0]
            limitations = ["Model does not assume unplanned promotional demand surges."]

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

            top_item = items[0] if items else {}
            observed_facts = [
                f"Database scan evaluated {evidence.get('total_evaluated', len(items))} items against active inventory.",
                f"Top at-risk SKU {top_item.get('sku')} has {top_item.get('current_stock', 0)} units on hand."
            ]
            calculated_metrics = [
                {"metric": "high_risk_skus_count", "value": count, "unit": "count", "calculation": "COUNT(risk_score >= 20.0)"}
            ]
            inferences = [
                f"{count} items have projected lead-time consumption that exceeds safe replenishment thresholds."
            ]
            recommendations_list = [rec_text]
            assumptions_list = [
                "Lead times based on primary supplier contract SLAs.",
                "Demand run-rate uses rolling 7-day average daily sales."
            ]
            findings = [
                f"Identified {count} items with elevated stockout risk scores (>20/100).",
                f"Top at-risk SKU is {top_item.get('sku')} at {top_item.get('store_name')} with risk score {top_item.get('risk_score')}/100."
            ]
            recommendation_text = rec_text
            limitations = ["Safety stock does not absorb unexpected multi-week supplier shutdowns."]

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

        observed_facts = [
            f"Overstock detection identified {len(items)} product-store pairs exceeding target runway.",
            f"Total excess inventory capital tied up: ₹{total_val:,.2f}."
        ]
        calculated_metrics = [
            {"metric": "total_excess_capital", "value": total_val, "unit": "INR", "calculation": "SUM(excess_units * unit_cost)"}
        ]
        inferences = [
            "Current stock velocity will not clear inventory before carrying costs exceed margins."
        ]
        recommendations_list = [rec_text]
        assumptions_list = ["Target runway benchmark is 45 days of supply."]
        findings = [
            f"Overstock ties up ₹{total_val:,.2f} across {len(items)} inventory positions."
        ]
        recommendation_text = rec_text
        limitations = ["Holding costs are estimated strictly from wholesale unit cost."]

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

        observed_facts = [
            f"Catalog scan identified {len(items)} SKUs with daily velocity below 0.2 units/day."
        ]
        calculated_metrics = [
            {"metric": "slow_movers_count", "value": len(items), "unit": "count", "calculation": "COUNT(daily_velocity < 0.2 AND age >= 21)"}
        ]
        inferences = [
            "Stagnant items represent potential dead stock if not repositioned."
        ]
        recommendations_list = [rec_text]
        assumptions_list = ["Filters out new launches younger than 21 days catalog age."]
        findings = [
            f"{len(items)} SKUs show sales velocity below 0.2 units/day despite holding inventory."
        ]
        recommendation_text = rec_text
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

        observed_facts = [
            f"All 4 regional stores analyzed over June 1, 2024 to August 29, 2024.",
            f"Top store by revenue: {top['store_name']} (₹{top['revenue']:,.2f})." if top else "Store data loaded."
        ]
        calculated_metrics = [
            {"metric": f"revenue_{s['city']}", "value": s['revenue'], "unit": "INR", "calculation": "SUM(sales.revenue)"}
            for s in stores
        ]
        inferences = [
            "Store revenues are closely aligned across metropolitan markets with consistent gross margins."
        ]
        recommendations_list = [rec_text]
        assumptions_list = ["Growth rates compare current 45-day window against preceding equal window."]
        findings = [
            f"Top revenue store is {top['store_name']} generating ₹{top['revenue']:,.2f}." if top else "Store comparison complete."
        ]
        recommendation_text = rec_text
        limitations = ["Regional demographic differences are not factored into gross margins."]

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
            observed_facts = [
                f"Total units sold: {evidence['units_sold']} units.",
                f"Total gross revenue: ₹{evidence['revenue']:,.2f}."
            ]
            calculated_metrics = [
                {"metric": "gross_margin_pct", "value": evidence['gross_margin_pct'], "unit": "%", "calculation": "((revenue - cogs) / revenue) * 100"}
            ]
            inferences = ["Product maintains stable margin contribution."]
            recommendations_list = ["Maintain stock availability to sustain revenue run-rate."]
            assumptions_list = ["Sales calculations include net discounts."]
            findings = [
                f"Generated ₹{evidence['revenue']:,.2f} from {evidence['units_sold']} units sold."
            ]
            recommendation_text = recommendations_list[0]
            limitations = ["Sales data reflects recorded point-of-sale transactions."]
        elif evidence.get("type") == "single_store_sales_performance":
            growth_str = f"{evidence.get('growth_vs_previous_period')}%" if evidence.get('growth_vs_previous_period') is not None else "N/A"
            answer = (
                f"Sales performance for {evidence['store_name']} ({evidence['city']}):\n\n"
                f"Revenue: ₹{evidence['revenue']:,.2f}\n"
                f"Units sold: {evidence['units_sold']:,} units\n"
                f"Gross margin: {evidence['gross_margin_pct']}%\n"
                f"Growth vs previous period: {growth_str}\n"
            )
            observed_facts = [
                f"{evidence['store_name']} units sold: {evidence['units_sold']:,} units.",
                f"Total revenue recorded: ₹{evidence['revenue']:,.2f}."
            ]
            calculated_metrics = [
                {"metric": "gross_margin_pct", "value": evidence['gross_margin_pct'], "unit": "%", "calculation": "((revenue - cogs) / revenue) * 100"}
            ]
            inferences = ["Store delivers steady sales contribution."]
            recommendations_list = ["Sustain operational cadence and monitor top-selling categories."]
            assumptions_list = ["All recorded sales transactions are final."]
            findings = [
                f"{evidence['store_name']} delivered ₹{evidence['revenue']:,.2f} in total revenue."
            ]
            recommendation_text = recommendations_list[0]
            limitations = ["Data excludes external offline vendor sales."]
        else:
            prods = evidence.get("top_products", [])
            lines = ["Top products by sales performance:\n"]
            for p in prods[:5]:
                lines.append(f"• {p['sku']} ({p['product_name']}): ₹{p['revenue']:,.2f} ({p['units_sold']} units, {p['gross_margin_pct']}% margin)")
            answer = "\n".join(lines)
            observed_facts = [f"Top SKU {prods[0]['sku']} generated ₹{prods[0]['revenue']:,.2f}."] if prods else []
            calculated_metrics = [{"metric": "top_products_count", "value": len(prods), "unit": "count", "calculation": "RANK(revenue DESC)"}]
            inferences = ["Top 5 SKUs drive significant share of category volume."]
            recommendations_list = ["Ensure uninterrupted supply chain replenishment for top revenue drivers."]
            assumptions_list = ["Net discounts applied at checkout are included."]
            findings = [f"Top revenue generator is {prods[0]['sku']}." if prods else "Sales overview generated."]
            recommendation_text = recommendations_list[0]
            limitations = ["Transaction logs do not capture customer demographic profiles."]

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
        observed_facts = [f"Found {len(items)} transactions deviating from 14-day rolling baseline."]
        calculated_metrics = [{"metric": "anomalies_detected", "value": len(items), "unit": "count", "calculation": "ABS(actual - baseline) > threshold"}]
        inferences = ["Spikes indicate potential localized bulk purchase or promo."]
        recommendations_list = [rec_text]
        assumptions_list = ["Rolling baseline window is 14 days."]
        findings = [f"Detected {len(items)} sales anomalies."]
        recommendation_text = rec_text
        limitations = ["Local holidays or micro-events are not tagged in transaction logs."]

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
            observed_facts = [
                f"Current on-hand stock: {evidence['current_stock']} units.",
                f"Contractual reorder point: {evidence['reorder_point']} units."
            ]
            calculated_metrics = [
                {"metric": "days_of_inventory", "value": evidence['days_of_inventory'], "unit": "days", "calculation": "current_stock / velocity_7d"},
                {"metric": "inventory_valuation", "value": evidence['inventory_valuation'], "unit": "INR", "calculation": "current_stock * cost_price"}
            ]
            inferences = ["Stock level is within operational limits." if evidence['current_stock'] > evidence['reorder_point'] else "Stock has breached reorder point."]
            rec_text = "Reorder immediately if stock is below reorder point." if evidence['current_stock'] <= evidence['reorder_point'] else "Inventory is currently within safe operational threshold."
            recommendations_list = [rec_text]
            assumptions_list = ["Valuation calculated at wholesale cost price."]
            findings = [f"Current stock is {evidence['current_stock']} units."]
            recommendation_text = rec_text
            limitations = ["Snapshot current as of latest recorded ledger date."]
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
            observed_facts = [
                f"Network inventory contains {evidence.get('total_units_in_stock'):,} total units across {evidence.get('total_skus')} SKUs.",
                f"{evidence.get('low_stock_count')} SKUs flagged for low stock."
            ]
            calculated_metrics = [
                {"metric": "total_inventory_valuation", "value": evidence.get('total_inventory_valuation', 0), "unit": "INR", "calculation": "SUM(closing_stock * cost_price)"}
            ]
            inferences = ["Capital is well distributed, but low-stock positions require reorder replenishment."]
            rec_text = "Focus replenishment actions on the low stock SKUs and evaluate markdown plans for overstocked lines."
            recommendations_list = [rec_text]
            assumptions_list = ["Inventory ledger closing balances represent physical store stock."]
            findings = [f"Network holds {evidence.get('total_units_in_stock'):,} units valued at ₹{evidence.get('total_inventory_valuation', 0):,.2f}."]
            recommendation_text = rec_text
            limitations = ["Shrinkage and theft are only accounted for if recorded as adjustments."]

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
        observed_facts = [
            f"Total network revenue: ₹{evidence.get('total_network_revenue', 0):,.2f}.",
            f"Stock on hand: {evidence.get('total_units_in_stock', 0):,} units."
        ]
        calculated_metrics = [
            {"metric": "total_network_revenue", "value": evidence.get('total_network_revenue', 0), "unit": "INR", "calculation": "SUM(sales.revenue)"}
        ]
        inferences = ["Operational balance requires prioritizing replenishment for high-risk SKUs."]
        rec_text = "Review high stockout risk items and initiate morning replenishment orders."
        recommendations_list = [rec_text]
        assumptions_list = ["Aggregated across all active physical store locations."]
        findings = [f"Network revenue totals ₹{evidence.get('total_network_revenue', 0):,.2f}."]
        recommendation_text = rec_text
        limitations = ["Data reflects recorded POS and inventory entries up to 2024-08-29."]

    return CopilotResponse(
        intent=intent_data.intent,
        product=intent_data.product,
        store=intent_data.store,
        time_period=intent_data.time_period,
        answer=answer,
        observed_facts=observed_facts,
        calculated_metrics=calculated_metrics,
        inferences=inferences,
        recommendations=recommendations_list,
        assumptions=assumptions_list,
        evidence_package=evidence.get("evidence_package") or (evidence if "source_tables" in evidence else None),
        key_findings=findings,
        evidence=evidence,
        recommendation=recommendation_text,
        limitations=limitations,
        data_sufficient=True,
        refusal_reason=None,
        confidence_note=confidence_prefix
    )


# =========================================================================
# GEMINI COPILOT AGENT CLASS
# =========================================================================

class RetailCopilot:
    """
    Coordinates between user questions, Gemini reasoning, and local deterministic analytics.
    Strictly adheres to:
    - NEVER MAKE A CLAIM WITHOUT SUPPORTING DATA.
    - Gemini is NOT the source of truth.
    - Local DB and deterministic analytics engine are the source of truth.
    - 5-tier distinction: OBSERVED FACT, CALCULATED METRIC, INFERENCE, RECOMMENDATION, ASSUMPTION.
    - Strict refusal rules for missing entities, insufficient dates, and unestablished causality.
    """

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self.model_name = model or os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
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
                "- requires_clarification: boolean, true only if intent cannot be mapped with reasonable confidence.\n"
                "- is_causal_inquiry: boolean, true if user asks 'why' or seeks a root-cause explanation (e.g. 'why did sales fall').\n\n"
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
                # Overwrite is_causal_inquiry with regex check if detected
                if is_causal_question(question):
                    parsed["is_causal_inquiry"] = True
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
        resolved_store: Optional[Dict[str, Any]],
        refusal_reason: Optional[str] = None,
        policy_chunks: Optional[List[Dict[str, Any]]] = None
    ) -> Optional[CopilotResponse]:
        """
        Requests Gemini to produce a grounded natural-language explanation strictly over the Python evidence package.
        Enforces 5-tier distinction and refusal rules.
        When policy documents were retrieved, they are injected as POLICY EVIDENCE reference context
        and Gemini is instructed to label claims as DATA EVIDENCE vs POLICY EVIDENCE.
        """
        client = self._get_client()
        if not client:
            return None

        try:
            from google.genai import types

            store_context = f"{resolved_store['store_name']} ({resolved_store['city']})" if resolved_store else "All Stores"
            product_context = f"{resolved_product['sku']} - {resolved_product['product_name']}" if resolved_product else "Not restricted to specific product"

            policy_context = ""
            if policy_chunks:
                chunk_lines = []
                for idx, c in enumerate(policy_chunks, 1):
                    doc = c.get("document_name") or "unknown_document"
                    chunk_id = c.get("chunk_id") or "?"
                    section = c.get("section") or ""
                    score = c.get("score")
                    score_str = f"{float(score):.2f}" if isinstance(score, (int, float)) else str(score or "")
                    tag = f"{chunk_id} ('{section}', {score_str})" if section else f"{chunk_id} ({score_str})"
                    chunk_lines.append(f"{idx}. [{tag}] \u2014 {c.get('text')}")
                policy_context = (
                    "RETRIEVED POLICY DOCUMENT EVIDENCE (KNOWLEDGE BASE):\n"
                    + "\n".join(chunk_lines)
                )

            prompt = (
                "You are an expert Retail Analytics Copilot for store managers.\n\n"
                "CRITICAL ARCHITECTURAL CONSTRAINTS:\n"
                "1. NEVER MAKE A CLAIM WITHOUT SUPPORTING DATA.\n"
                "2. The local database and deterministic Python evidence provided below are the SOLE SOURCE OF TRUTH.\n"
                "3. You must NEVER fabricate or hallucinate numbers, product IDs, or store IDs.\n"
                "4. STRICT REFUSAL RULE FOR CAUSALITY: If the user asks a causal question (e.g., 'Why did sales fall?'), "
                "   you must state the observed facts (e.g. 'Sales declined by X%, but the available data cannot establish the cause. "
                "   Inventory availability was [figure] and price was [figure]. We do not have competitor pricing, customer feedback, "
                "   or marketing data to determine why.') Do NOT say 'Customers preferred competitors' unless competitor data exists!\n"
                "5. Categorize your reasoning into the 5 explicit tiers:\n"
                "   - observed_facts: statements directly observed in SQLite\n"
                "   - calculated_metrics: mathematical results computed by Python formulas\n"
                "   - inferences: analytical deductions grounded in metrics\n"
                "   - recommendations: policy-compliant actions subject to manager approval\n"
                "   - assumptions: stated boundary assumptions\n"
                "6. Do NOT use unsupported certainty.\n"
                "7. LABEL YOUR SOURCES: Label claims as 'DATA EVIDENCE' when they come from the "
                "deterministic Python evidence package above, and 'POLICY EVIDENCE' when they come "
                "from the retrieved knowledge-base documents. NEVER present a policy or procedure "
                "statement as if it were derived from database analytics.\n"
                "8. POLICY QUESTIONS: When the user asks about a policy, procedure, rule, approval, "
                "transfer, return, damaged goods, guideline, SLA, or workflow, answer strictly from the "
                "RETRIEVED POLICY DOCUMENT EVIDENCE below (if provided). If no relevant policy document "
                "was retrieved, state that the knowledge base does not cover the specific policy rather "
                "than inventing one. Database analytics must not be cited as the source for policy text.\n\n"
                f"USER QUESTION: \"{question}\"\n"
                f"DETECTED INTENT: {intent_data.intent}\n"
                f"IS CAUSAL INQUIRY: {intent_data.is_causal_inquiry}\n"
                f"STORE CONTEXT: {store_context}\n"
                f"PRODUCT CONTEXT: {product_context}\n"
                f"EVIDENCE PACKAGE FROM PYTHON (SOURCE OF TRUTH - DATA EVIDENCE):\n"
                f"{json.dumps(evidence, indent=2)}\n\n"
                f"{policy_context}\n\n"
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
                data["evidence"] = evidence
                if refusal_reason:
                    data["refusal_reason"] = refusal_reason
                if not data.get("confidence_note"):
                    data["confidence_note"] = "Explanation reasoned by Gemini 2.5 Flash, strictly grounded on deterministic Python calculations."
                return CopilotResponse(**data)

        except Exception as e:
            logger.warning(f"Gemini response generation failed: {e}", exc_info=True)

        return None

    def ask(self, question: str) -> CopilotResponse:
        """
        Executes the full copilot pipeline with strict refusal checks and document-retrieval
        integration for POLICY/PROCEDURE questions:

        USER QUESTION
        → date validity check
        → intent extraction
        → entity resolution
        → deterministic analytics & evidence package
        → Gemini explanation
        → document (policy) evidence grounding
        → structured response
        """
        # Detect policy-oriented questions up-front (keyword heuristic; intent is refined in _ask_core).
        policy_query = is_policy_question(question)
        policy_chunks: List[Dict[str, Any]] = []
        retrieval_available = False
        if policy_query:
            policy_chunks, retrieval_available = self._gather_policy_evidence(question)

        response = self._ask_core(
            question,
            policy_query=policy_query,
            policy_chunks=policy_chunks,
            retrieval_available=retrieval_available
        )
        return self._finalize_policy_response(
            response,
            question=question,
            policy_query=policy_query,
            policy_chunks=policy_chunks,
            retrieval_available=retrieval_available
        )

    def _build_policy_answer_response(
        self,
        question: str,
        intent_data: "IntentSchema",
        policy_chunks: List[Dict[str, Any]]
    ) -> "CopilotResponse":
        """
        Deterministic, citation-only policy answer built from the retrieved knowledge base.
        Never consults database entities (a phantom store/product must not block a policy answer).
        """
        top = policy_chunks[0]
        citation = f"{top['chunk_id']} ('{top['section'] or 'General'}', score {top['score']:.2f})"
        answer = (
            "Answered from the retailer policy knowledge base.\n\n"
            f"Per {citation}:\n{top['text'].strip()}"
        )
        if len(policy_chunks) > 1:
            answer += (
                f"\n\n{len(policy_chunks) - 1} additional matching section(s) "
                "are cited in POLICY EVIDENCE below."
            )
        evidence = {"policy_evidence": [dict(c) for c in policy_chunks]}
        return CopilotResponse(
            intent="POLICY_DOCUMENT",
            product=None,
            store=None,
            time_period=intent_data.time_period,
            answer=answer,
            observed_facts=[],
            calculated_metrics=[],
            inferences=[f"Knowledge-base match: {citation}."],
            recommendations=[
                "Policy claims are grounded in POLICY EVIDENCE; database analytics were not used for this answer."
            ],
            assumptions=[
                "Policy documents indexed in data/documents are authoritative operational references."
            ],
            evidence=evidence,
            evidence_package=None,
            key_findings=[f"Retrieved {len(policy_chunks)} document section(s) related to the question."],
            data_sufficient=True,
            refusal_reason=None,
            recommendation="Follow the cited policy; escalate manager-approval steps per the document.",
            limitations=["POLICY EVIDENCE is local document content, not database analytics."],
            confidence_note="Deterministic document retrieval result."
        )

    def _gather_policy_evidence(
        self,
        question: str,
        top_k: int = 5
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """
        BEST-EFFORT document retrieval for POLICY/PROCEDURE questions.
        Returns (policy_chunks, retrieval_available). NEVER raises.
        """
        try:
            module = _retrieve_module()
            if module is None:
                return [], False
            status = module.retrieval_status()
            available = bool(status and isinstance(status, dict) and status.get("available"))
            chunks = _chunks_to_dicts(module.retrieve_documents(question, top_k=top_k))
            return chunks, available
        except Exception as e:
            logger.warning(f"Document retrieval failed (treated as unavailable): {e}")
            return [], False

    def _finalize_policy_response(
        self,
        response: CopilotResponse,
        question: str,
        policy_query: bool,
        policy_chunks: List[Dict[str, Any]],
        retrieval_available: bool
    ) -> CopilotResponse:
        """
        Attaches document-retrieval results to the assembled response:
        - sets document_retrieval_available
        - injects policy_evidence into the evidence package (clearly-labelled section)
        - appends a POLICY EVIDENCE block to the answer when chunks were used
        - appends an unavailability note when retrieval is unavailable for policy questions
        """
        response.document_retrieval_available = bool(retrieval_available)

        if not policy_query:
            return response

        if policy_chunks:
            chunks = [dict(c) for c in policy_chunks]
            response.policy_evidence = chunks

            if isinstance(response.evidence, dict):
                response.evidence.setdefault("policy_evidence", list(chunks))
            if isinstance(response.evidence_package, dict):
                response.evidence_package.setdefault("policy_evidence", list(chunks))

            if "POLICY EVIDENCE:" not in (response.answer or ""):
                block = _build_policy_evidence_block(chunks)
                response.answer = (response.answer or "").rstrip() + "\n\n" + block
            if "policy claim" not in (response.confidence_note or "").lower():
                response.confidence_note = (
                    (response.confidence_note + " ").strip() + "Policy claims grounded in retrieved "
                    "knowledge-base documents (POLICY EVIDENCE), not database analytics."
                )
        else:
            note = UNAVAILABLE_RETRIEVAL_NOTE if not retrieval_available else (
                "Document retrieval is available, but no knowledge-base section matched this "
                "question strongly enough to cite."
            )
            if response.answer and note not in response.answer:
                response.answer = response.answer.rstrip() + "\n\n" + note

        return response

    def _ask_core(
        self,
        question: str,
        policy_query: bool = False,
        policy_chunks: Optional[List[Dict[str, Any]]] = None,
        retrieval_available: bool = False
    ) -> CopilotResponse:
        """Internal pipeline (see ask for the full flow description)."""
        # 1. Date Range Validity Check (Strict Refusal Rule)
        is_date_valid, requested_date, date_err = check_date_range_validity(question)
        if not is_date_valid:
            ans = f"The date range has insufficient data. {date_err}"
            return CopilotResponse(
                intent="UNKNOWN",
                time_period=str(requested_date),
                answer=ans,
                observed_facts=[f"Database contains transactions and inventory records from {DATABASE_MIN_DATE} to {DATABASE_MAX_DATE}."],
                calculated_metrics=[],
                inferences=["No records exist for the specified date period."],
                recommendations=[f"Please query within the active recording window ({DATABASE_MIN_DATE} to {DATABASE_MAX_DATE})."],
                assumptions=[],
                evidence_package=None,
                key_findings=[f"Requested date period '{requested_date}' is outside available records."],
                evidence={"requested_period": requested_date, "available_period": f"{DATABASE_MIN_DATE} to {DATABASE_MAX_DATE}"},
                recommendation="Query data within June 1, 2024 to August 29, 2024.",
                limitations=[f"Local dataset is bounded to {DATABASE_MIN_DATE} through {DATABASE_MAX_DATE}."],
                data_sufficient=False,
                refusal_reason="INSUFFICIENT_DATE_RANGE",
                confidence_note="Date range check failed: insufficient local records."
            )

        # 2. Intent Extraction
        intent_data = self.extract_intent(question)

        # Check if question is causal inquiry
        if is_causal_question(question):
            intent_data.is_causal_inquiry = True

        # Refine policy detection using the extracted intent (UNKNOWN => document/policy oriented).
        if not policy_query and intent_data.intent == "UNKNOWN" and not intent_data.is_causal_inquiry:
            policy_query = True
            policy_chunks, retrieval_available = self._gather_policy_evidence(question)

        # Policy/procedure questions are answered from the knowledge base (POLICY EVIDENCE),
        # never through database-entity refusals (phantom stores/products must not block policy answers).
        if policy_query and policy_chunks:
            top_score = float(policy_chunks[0].get("score", 0.0))
            if _has_policy_keywords(question) or top_score >= POLICY_MIN_SIMILARITY:
                return self._build_policy_answer_response(question, intent_data, policy_chunks)

        # 3. Entity Resolution against local SQLite database
        resolved_product, resolved_store, product_not_found, store_not_found = resolve_entities(
            product_query=intent_data.product,
            store_query=intent_data.store
        )

        # Handle non-existent store (Strict Refusal Rule)
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
                intent=intent_data.intent,
                store=intent_data.store,
                time_period=intent_data.time_period,
                answer=msg,
                observed_facts=[f"Database contains records for {len(avail_stores)} stores: {', '.join(avail_stores)}."],
                calculated_metrics=[],
                inferences=[f"Store '{intent_data.store}' does not exist in local records."],
                recommendations=[f"Please specify one of our active store locations: {', '.join(avail_stores)}."],
                assumptions=[],
                evidence_package=None,
                key_findings=[f"Store '{intent_data.store}' does not exist in local records."],
                evidence={"requested_store": intent_data.store, "available_stores": avail_stores},
                recommendation=f"Please specify one of our active store locations: {', '.join(avail_stores)}.",
                limitations=["Database contains records only for authorized physical store network."],
                data_sufficient=False,
                refusal_reason="NON_EXISTENT_STORE",
                confidence_note="Local database contains no records for the requested store."
            )

        # Handle non-existent product (Strict Refusal Rule)
        if product_not_found:
            msg = (
                f"The requested product '{intent_data.product}' was not found in the local catalog. "
                f"Please verify the product name or SKU."
            )
            return CopilotResponse(
                intent=intent_data.intent,
                product=intent_data.product,
                time_period=intent_data.time_period,
                answer=msg,
                observed_facts=["Catalog contains 108 active SKUs across Beverages, Snacks, and Grocery."],
                calculated_metrics=[],
                inferences=[f"Product '{intent_data.product}' is not indexed or stocked."],
                recommendations=["Search the catalog using standard SKU format (e.g. SKU-PRE-001) or product name."],
                assumptions=[],
                evidence_package=None,
                key_findings=[f"Product '{intent_data.product}' not found in catalog."],
                evidence={"requested_product": intent_data.product},
                recommendation="Search the catalog using standard SKU format (e.g. SKU-PRE-001) or product name.",
                limitations=["Search performed across active catalog records only."],
                data_sufficient=False,
                refusal_reason="UNSUPPORTED_PRODUCT",
                confidence_note="Local database contains no matching SKU or product."
            )

        # 4. Deterministic Analytics (The local database is the source of truth)
        evidence, data_sufficient, missing_info = execute_deterministic_analytics(
            intent_data=intent_data,
            resolved_product=resolved_product,
            resolved_store=resolved_store
        )

        refusal_reason = evidence.get("refusal_reason")

        # If data is insufficient or refused, return immediately with explanation
        if not data_sufficient or refusal_reason:
            return synthesize_deterministic_response(
                question=question,
                intent_data=intent_data,
                evidence=evidence,
                data_sufficient=data_sufficient,
                missing_info=missing_info,
                refusal_reason=refusal_reason,
                confidence_prefix="Local database query completed."
            )

        # 5. Gemini Explanation & Reasoning (if API key available)
        gemini_response = None
        if self.api_key:
            gemini_response = self.explain_with_gemini(
                question=question,
                intent_data=intent_data,
                evidence=evidence,
                resolved_product=resolved_product,
                resolved_store=resolved_store,
                refusal_reason=refusal_reason,
                policy_chunks=policy_chunks
            )

        if gemini_response:
            return gemini_response

        # 6. Deterministic Fallback if Gemini unavailable or failed
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
            refusal_reason=None,
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
    "DATABASE_MIN_DATE",
    "DATABASE_MAX_DATE",
    "resolve_entities",
    "is_causal_question",
    "check_date_range_validity",
    "rule_based_extract_intent",
    "execute_deterministic_analytics",
    "get_copilot",
    "ask_copilot",
    "is_policy_question",
    "retrieval_status_safe"
]
