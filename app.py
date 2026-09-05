"""
Retail - Sales and Inventory Copilot
TRACK_ID: PS03

Primary application entrypoint.
Starts FastAPI application and serves static frontend and REST endpoints.
Start command: python app.py
Default URL: http://localhost:8000
"""
import os
import sys
import socket
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

# Load environment variables (e.g. GEMINI_API_KEY)
load_dotenv()

# Initialize core modules
from src.database.schema import init_db
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
    check_inventory_data_quality
)
from fastapi import HTTPException, Query
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
from src.copilot import ask_copilot, get_copilot
from src.recommendations import get_attention_today

class CopilotChatRequest(BaseModel):
    question: str = Field(..., description="Natural language question from store manager.")

app = FastAPI(
    title="Retail - Sales and Inventory Copilot",
    description="Deterministic retail business logic combined with Gemini-powered reasoning.",
    version="0.1.0"
)

# Base directories
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# Ensure static directory exists and mount it
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.on_event("startup")
def on_startup():
    """Ensure database schema is initialized on boot."""
    init_db()

@app.get("/api/health")
def health_check():
    """
    Health check endpoint required by hackathon specification.
    """
    return {
        "status": "healthy",
        "track_id": "PS03",
        "project": "Retail - Sales and Inventory Copilot",
        "version": "0.1.0"
    }

# =========================================================================
# DETERMINISTIC RETAIL ANALYTICS API ENDPOINTS
# Calculations are executed in Python over local SQLite data. Not by Gemini.
# =========================================================================

@app.get("/api/analytics/product-performance")
def get_product_performance(
    product_id: Optional[str] = Query(None, description="Optional product ID. If omitted, returns ranked product overview."),
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    store_id: Optional[str] = Query(None, description="Optional store ID filter")
):
    try:
        res = calculate_product_performance(
            product_id=product_id,
            start_date=start_date,
            end_date=end_date,
            store_id=store_id
        )
        if hasattr(res, "to_dict"):
            return res.to_dict()
        return res
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/analytics/store-performance")
def get_store_performance(
    store_id: Optional[str] = Query(None, description="Optional store ID. If omitted, returns comparison of all stores."),
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)")
):
    try:
        res = calculate_store_performance(
            store_id=store_id,
            start_date=start_date,
            end_date=end_date
        )
        if hasattr(res, "to_dict"):
            return res.to_dict()
        return res
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/analytics/inventory-health")
def get_inventory_health(
    store_id: Optional[str] = Query(None, description="Store ID"),
    product_id: Optional[str] = Query(None, description="Product ID"),
    as_of_date: Optional[str] = Query(None, description="Snapshot date (YYYY-MM-DD)")
):
    try:
        if store_id and product_id:
            res = calculate_inventory_health(store_id=store_id, product_id=product_id, as_of_date=as_of_date)
            return res.to_dict()
        return get_inventory_health_summary(as_of_date=as_of_date)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/analytics/inventory-turnover")
def get_inventory_turnover(
    product_id: Optional[str] = Query(None, description="Optional product ID filter"),
    store_id: Optional[str] = Query(None, description="Optional store ID filter"),
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)")
):
    try:
        res = calculate_inventory_turnover(
            product_id=product_id,
            store_id=store_id,
            start_date=start_date,
            end_date=end_date
        )
        return res.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/analytics/slow-movers")
def get_slow_movers(
    sales_threshold_daily: float = Query(0.20, description="Max daily sales velocity"),
    inventory_threshold_units: int = Query(15, description="Min units in stock"),
    min_catalog_age_days: int = Query(21, description="Min days since product launched"),
    as_of_date: Optional[str] = Query(None, description="Evaluation date (YYYY-MM-DD)"),
    store_id: Optional[str] = Query(None, description="Optional store ID")
):
    try:
        results = detect_slow_moving_products(
            sales_threshold_daily=sales_threshold_daily,
            inventory_threshold_units=inventory_threshold_units,
            min_catalog_age_days=min_catalog_age_days,
            as_of_date=as_of_date,
            store_id=store_id
        )
        return [r.to_dict() for r in results]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/analytics/overstock")
def get_overstock(
    target_days: float = Query(45.0, description="Target runway days of inventory"),
    min_stock: int = Query(25, description="Min stock on hand to consider"),
    as_of_date: Optional[str] = Query(None, description="Evaluation date (YYYY-MM-DD)"),
    store_id: Optional[str] = Query(None, description="Optional store ID")
):
    try:
        results = detect_overstocked_products(
            target_days=target_days,
            min_stock=min_stock,
            as_of_date=as_of_date,
            store_id=store_id
        )
        return [r.to_dict() for r in results]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/analytics/stockout-risk")
def get_stockout_risk(
    store_id: Optional[str] = Query(None, description="Store ID"),
    product_id: Optional[str] = Query(None, description="Product ID"),
    as_of_date: Optional[str] = Query(None, description="Snapshot date (YYYY-MM-DD)"),
    min_risk_score: float = Query(20.0, description="Min risk score filter for catalog scan")
):
    try:
        if store_id and product_id:
            res = calculate_stockout_risk(store_id=store_id, product_id=product_id, as_of_date=as_of_date)
            return res.to_dict()
        results = assess_all_stockout_risks(store_id=store_id, as_of_date=as_of_date, min_risk_score=min_risk_score)
        return [r.to_dict() for r in results]
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/analytics/anomalies")
def get_sales_anomalies(
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    store_id: Optional[str] = Query(None, description="Optional store ID"),
    product_id: Optional[str] = Query(None, description="Optional product ID"),
    window_days: int = Query(14, description="Lookback rolling baseline window in days"),
    min_pct_change: float = Query(50.0, description="Min percentage deviation from baseline")
):
    try:
        results = detect_sales_anomalies(
            start_date=start_date,
            end_date=end_date,
            store_id=store_id,
            product_id=product_id,
            window_days=window_days,
            min_pct_change=min_pct_change
        )
        return [r.to_dict() for r in results]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/analytics/data-quality")
def get_data_quality_report():
    try:
        report = check_inventory_data_quality()
        return report.to_dict()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/attention")
def get_attention_today_endpoint(limit: int = Query(5, description="Number of top attention items to return")):
    """
    Attention Today endpoint returning prioritized operational alerts.
    Returns top N attention items ranked by deterministic priority score.
    """
    try:
        result = get_attention_today(limit=limit)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# =========================================================================
# GEMINI COPILOT REST API ENDPOINTS
# Grounded on deterministic Python calculations.
# =========================================================================

@app.post("/api/copilot/chat")
def post_copilot_chat(payload: CopilotChatRequest):
    """
    Accepts user question and returns structured grounded response:
    answer, key_findings, evidence, recommendation, assumptions, limitations,
    data_sufficient, confidence_note.
    """
    try:
        return ask_copilot(payload.question)
    except Exception as e:
        return {
            "intent": "UNKNOWN",
            "answer": f"A processing error occurred: {str(e)}",
            "observed_facts": [],
            "calculated_metrics": [],
            "inferences": [],
            "recommendations": ["Try asking a supported retail question such as 'What is running out?'"],
            "assumptions": [],
            "key_findings": ["Encountered unexpected internal error."],
            "evidence": {"error": str(e)},
            "recommendation": "Try asking a supported retail question such as 'What is running out?'",
            "limitations": [],
            "data_sufficient": False,
            "refusal_reason": "PROCESSING_ERROR",
            "confidence_note": "Failed during copilot execution."
        }

@app.get("/api/copilot/query")
def get_copilot_query(question: str = Query(..., description="Retail analytics question")):
    """
    GET alternative for copilot query inspection via browser or curl.
    """
    try:
        return ask_copilot(question)
    except Exception as e:
        return {
            "intent": "UNKNOWN",
            "answer": f"A processing error occurred: {str(e)}",
            "observed_facts": [],
            "calculated_metrics": [],
            "inferences": [],
            "recommendations": ["Try asking a supported retail question such as 'What is running out?'"],
            "assumptions": [],
            "key_findings": ["Encountered unexpected internal error."],
            "evidence": {"error": str(e)},
            "recommendation": None,
            "limitations": [],
            "data_sufficient": False,
            "refusal_reason": "PROCESSING_ERROR",
            "confidence_note": "Failed during copilot execution."
        }

@app.get("/api/copilot/status")
def get_copilot_status():
    """
    Returns runtime configuration and health of Gemini LLM integration.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    is_configured = bool(api_key and len(api_key.strip()) > 0)
    return {
        "gemini_api_key_configured": is_configured,
        "sdk": "google-genai",
        "model": "gemini-2.5-flash",
        "active_mode": "live_reasoning" if is_configured else "deterministic_fallback",
        "source_of_truth": "Local SQLite Database and Deterministic Python Analytics Engine",
        "status_message": (
            "Gemini live reasoning active via google-genai SDK."
            if is_configured else
            "GEMINI_API_KEY is not configured. Copilot is functioning in deterministic fallback mode using verified local calculations."
        )
    }

@app.get("/")
def read_root():
    """
    Serves the simple landing page for the Retail Sales and Inventory Copilot.
    No frontend build step required.
    """
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {
        "project": "Retail - Sales and Inventory Copilot",
        "track_id": "PS03",
        "message": "Welcome to Retail Sales and Inventory Copilot"
    }

def get_target_port() -> int:
    """
    Determines the port to bind to.
    Defaults strictly to 8000 as mandated by hackathon requirements.
    In environments where 8000 is occupied (or DEFAULT_APP_PORT is defined for container ingress),
    it adapts seamlessly.
    """
    # 1. Direct command-line override: python app.py --port 8000
    for idx, arg in enumerate(sys.argv):
        if arg in ("--port", "-p") and idx + 1 < len(sys.argv):
            try:
                return int(sys.argv[idx + 1])
            except ValueError:
                pass

    # 2. Check if DEFAULT_APP_PORT is explicitly requested by container routing
    if os.environ.get("DEFAULT_APP_PORT"):
        return int(os.environ["DEFAULT_APP_PORT"])

    # 3. Standard clean evaluation machine: Default to 8000
    preferred_port = 8000
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("0.0.0.0", preferred_port))
            return preferred_port
    except OSError:
        # Fallback if 8000 is held by an environment daemon
        return 3000

if __name__ == "__main__":
    port = get_target_port()
    print(f"Starting Retail - Sales and Inventory Copilot on 0.0.0.0:{port}")
    uvicorn.run("app:app", host="0.0.0.0", port=port, log_level="info")
