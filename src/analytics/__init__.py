"""
Analytics package: deterministic sales and inventory calculations.
"""
from src.analytics.kpis import (
    calculate_store_performance,
    calculate_product_performance,
    get_inventory_health_summary
)

__all__ = [
    "calculate_store_performance",
    "calculate_product_performance",
    "get_inventory_health_summary"
]
