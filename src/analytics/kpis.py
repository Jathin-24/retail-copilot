"""
Deterministic KPI calculations for retail sales and inventory.
Python performs all arithmetic, aggregation, filtering, and metrics.
Gemini does not perform math.

Maintains backward compatibility while providing access to enhanced performance calculators.
"""
from src.analytics.sales import (
    calculate_store_performance,
    calculate_product_performance
)
from src.analytics.inventory import get_inventory_health_summary

__all__ = [
    "calculate_store_performance",
    "calculate_product_performance",
    "get_inventory_health_summary"
]
