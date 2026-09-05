"""
Evidence Layer for Retail Analytics Copilot
TRACK_ID: PS03

MANDATORY ARCHITECTURAL PRINCIPLE:
NEVER MAKE A CLAIM WITHOUT SUPPORTING DATA.

Every analytical claim, inference, or recommendation must be grounded in an
explicit evidence structure containing:
- source dataset/table
- relevant product ID
- relevant store ID
- date range
- raw values used
- calculated values
- formula or calculation description
- assumptions
- data-quality warnings if any
"""
from __future__ import annotations

from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field


class MetricEvidence(BaseModel):
    """
    Structured atomic evidence record for a single KPI or metric.
    Example:
    {
      "metric": "days_of_inventory",
      "value": 3.4,
      "unit": "days",
      "source": "inventory.csv + sales.csv",
      "period": "2024-08-23 to 2024-08-29",
      "calculation": "current_stock / average_daily_sales_7d",
      "raw_values": {"current_stock": 38, "average_daily_sales_7d": 11.2}
    }
    """
    metric: str = Field(..., description="Name of metric (e.g. days_of_inventory, risk_score, sales_revenue).")
    value: Any = Field(..., description="Numerical or categorical evaluated value.")
    unit: str = Field(..., description="Unit of measurement (e.g. 'days', 'units', 'INR', 'score/100', '%').")
    source: str = Field(..., description="Primary CSV or database table origin.")
    period: str = Field(..., description="Date range over which metric was evaluated.")
    calculation: str = Field(..., description="Deterministic formula or computational rule description.")
    raw_values: Dict[str, Any] = Field(default_factory=dict, description="Raw input values fed into calculation.")


class EvidencePackage(BaseModel):
    """
    Comprehensive structured evidence package passed to Gemini and exposed to UI.
    Contains zero fabricated data.
    """
    source_tables: List[str] = Field(default_factory=list, description="List of tables/CSVs queried.")
    product_id: Optional[str] = Field(None, description="Target product ID if entity-specific.")
    product_name: Optional[str] = Field(None, description="Target product name.")
    sku: Optional[str] = Field(None, description="Target SKU.")
    store_id: Optional[str] = Field(None, description="Target store ID if entity-specific.")
    store_name: Optional[str] = Field(None, description="Target store name.")
    city: Optional[str] = Field(None, description="Target city.")
    date_range: Dict[str, Optional[str]] = Field(default_factory=dict, description="Start and end dates.")
    raw_values: Dict[str, Any] = Field(default_factory=dict, description="Raw database quantities.")
    calculated_values: Dict[str, Any] = Field(default_factory=dict, description="Computed metrics.")
    metrics: List[MetricEvidence] = Field(default_factory=list, description="List of granular MetricEvidence items.")
    formulas: Dict[str, str] = Field(default_factory=dict, description="Readable calculation formulas.")
    assumptions: List[str] = Field(default_factory=list, description="Analytical assumptions made.")
    data_quality_warnings: List[str] = Field(default_factory=list, description="Integrity warnings, gaps, or flags.")
    causal_limitations: List[str] = Field(default_factory=list, description="Statements on unmeasured external variables.")
    data_sufficient: bool = Field(True, description="False if data does not contain necessary entity or records.")
    refusal_reason: Optional[str] = Field(None, description="Explicit reason if query is refused or qualified.")

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


# =========================================================================
# EVIDENCE BUILDERS FOR DOMAIN ANALYTICS
# =========================================================================

def build_stockout_evidence(
    store_id: Optional[str],
    store_name: Optional[str],
    city: Optional[str],
    product_id: Optional[str],
    product_name: Optional[str],
    sku: Optional[str],
    current_stock: int,
    lead_time_days: int,
    lead_time_demand: float,
    daily_demand: float,
    safety_stock: float,
    incoming_stock: int,
    risk_score: float,
    risk_level: str,
    days_of_inventory: Optional[float],
    period: str = "2024-08-23 to 2024-08-29"
) -> EvidencePackage:
    """Builds evidence package for a single product stockout risk."""
    metrics = [
        MetricEvidence(
            metric="current_stock",
            value=current_stock,
            unit="units",
            source="inventory.csv (latest closing_stock)",
            period=period.split(" to ")[-1],
            calculation="Direct ledger balance closing_stock as of latest date",
            raw_values={"closing_stock": current_stock}
        ),
        MetricEvidence(
            metric="daily_demand",
            value=daily_demand,
            unit="units/day",
            source="sales.csv",
            period=period,
            calculation="SUM(quantity sold over last 7 days) / 7.0",
            raw_values={"units_sold_7d": round(daily_demand * 7, 1)}
        ),
        MetricEvidence(
            metric="lead_time_days",
            value=lead_time_days,
            unit="days",
            source="suppliers.csv",
            period="Active contract",
            calculation="Contractual supplier replenishment lead time in days",
            raw_values={"supplier_lead_time": lead_time_days}
        ),
        MetricEvidence(
            metric="lead_time_demand",
            value=lead_time_demand,
            unit="units",
            source="sales.csv + suppliers.csv",
            period=period,
            calculation="daily_demand * lead_time_days",
            raw_values={"daily_demand": daily_demand, "lead_time_days": lead_time_days}
        ),
        MetricEvidence(
            metric="risk_score",
            value=risk_score,
            unit="score/100",
            source="inventory.csv + sales.csv + purchase_orders.csv",
            period=period,
            calculation="MIN(100, MAX(0, 100 - (net_position / (lead_time_demand + safety_stock)) * 100))",
            raw_values={
                "current_stock": current_stock,
                "incoming_stock": incoming_stock,
                "lead_time_demand": lead_time_demand,
                "safety_stock": safety_stock
            }
        )
    ]

    raw = {
        "current_stock_units": current_stock,
        "incoming_stock_units": incoming_stock,
        "supplier_lead_time_days": lead_time_days
    }
    calc = {
        "daily_demand_units_per_day": daily_demand,
        "lead_time_demand_units": lead_time_demand,
        "safety_stock_units": safety_stock,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "days_of_inventory": days_of_inventory
    }
    formulas = {
        "lead_time_demand": "daily_demand * lead_time_days",
        "net_inventory_position": "current_stock + incoming_quantity",
        "risk_score": "100 - (net_position / (lead_time_demand + safety_stock)) * 100"
    }
    assumptions = [
        f"Supplier lead time remains constant at {lead_time_days} days.",
        f"Recent 7-day average daily sales ({daily_demand} units/day) is representative of near-term demand.",
        "Incoming quantity reflects confirmed open purchase orders."
    ]
    warnings = []
    if current_stock == 0:
        warnings.append("CRITICAL: On-hand stock is completely depleted (0 units).")
    if incoming_stock == 0 and risk_score >= 80:
        warnings.append("WARNING: No incoming purchase orders recorded for this high-risk item.")

    return EvidencePackage(
        source_tables=["inventory", "sales", "suppliers", "purchase_orders"],
        product_id=product_id,
        product_name=product_name,
        sku=sku,
        store_id=store_id,
        store_name=store_name,
        city=city,
        date_range={"start_date": period.split(" to ")[0], "end_date": period.split(" to ")[-1]},
        raw_values=raw,
        calculated_values=calc,
        metrics=metrics,
        formulas=formulas,
        assumptions=assumptions,
        data_quality_warnings=warnings,
        causal_limitations=["Lead-time demand excludes external promotion or competitor stockouts."],
        data_sufficient=True
    )


def build_causal_inquiry_evidence(
    entity_name: str,
    period_current: str,
    period_previous: str,
    units_current: int,
    units_previous: int,
    pct_change: float,
    current_stock: Optional[int] = None,
    selling_price: Optional[float] = None,
    product_id: Optional[str] = None,
    store_id: Optional[str] = None
) -> EvidencePackage:
    """
    Builds explicit refusal evidence for causal questions (e.g. 'Why did sales fall?').
    Proves what the local data establishes (sales, stock, price),
    and strictly documents what external data is missing (competitor, feedback, marketing).
    """
    metrics = [
        MetricEvidence(
            metric="sales_units_change",
            value=pct_change,
            unit="%",
            source="sales.csv",
            period=f"{period_previous} vs {period_current}",
            calculation="((units_current - units_previous) / units_previous) * 100",
            raw_values={"units_current": units_current, "units_previous": units_previous}
        )
    ]
    if current_stock is not None:
        metrics.append(
            MetricEvidence(
                metric="inventory_availability",
                value=current_stock,
                unit="units",
                source="inventory.csv",
                period=period_current,
                calculation="closing_stock on record",
                raw_values={"closing_stock": current_stock}
            )
        )
    if selling_price is not None:
        metrics.append(
            MetricEvidence(
                metric="selling_price",
                value=selling_price,
                unit="INR",
                source="products.csv",
                period="Active catalog",
                calculation="catalog selling_price",
                raw_values={"selling_price": selling_price}
            )
        )

    return EvidencePackage(
        source_tables=["sales", "inventory", "products"],
        product_id=product_id,
        product_name=entity_name if product_id else None,
        store_id=store_id,
        date_range={"period_current": period_current, "period_previous": period_previous},
        raw_values={
            "units_sold_current_period": units_current,
            "units_sold_previous_period": units_previous,
            "stock_on_hand": current_stock,
            "recorded_price_inr": selling_price
        },
        calculated_values={
            "percentage_change": pct_change
        },
        metrics=metrics,
        formulas={
            "percentage_change": "((current_units - previous_units) / previous_units) * 100"
        },
        assumptions=[
            "Sales numbers reflect point-of-sale cash register transactions."
        ],
        data_quality_warnings=[
            "Local database does NOT contain external causal datasets (competitor pricing, marketing campaigns, footfall, customer surveys)."
        ],
        causal_limitations=[
            "Local data establishes correlation and magnitude of decline, but CANNOT establish root causality.",
            "Missing competitor pricing data.",
            "Missing customer feedback, reviews, or satisfaction scores.",
            "Missing marketing, advertising spend, or promotional campaign data.",
            "Missing footfall traffic or weather/local demographic disruption data."
        ],
        data_sufficient=True,
        refusal_reason="UNESTABLISHED_CAUSALITY"
    )
