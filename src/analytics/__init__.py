"""
Retail Analytics Engine (PS03): Deterministic calculations for sales and inventory.
All business calculations are executed deterministically in Python using local SQLite data.
Gemini does not perform math.
"""
from src.analytics.models import (
    ProductPerformance,
    StorePerformance,
    InventoryHealth,
    InventoryTurnover,
    SlowMoverResult,
    OverstockResult,
    StockoutRiskResult,
    SalesAnomalyResult,
    DataQualityReport
)

from src.analytics.sales import (
    calculate_product_performance,
    calculate_store_performance
)

from src.analytics.inventory import (
    calculate_inventory_health,
    calculate_inventory_turnover,
    get_inventory_health_summary
)

from src.analytics.slow_movers import (
    detect_slow_moving_products
)

from src.analytics.overstock import (
    detect_overstocked_products
)

from src.analytics.stockout_risk import (
    calculate_stockout_risk,
    assess_all_stockout_risks
)

from src.analytics.anomalies import (
    detect_sales_anomalies
)

from src.analytics.quality import (
    check_inventory_data_quality,
    check_negative_stock,
    check_impossible_quantities,
    check_missing_references,
    check_unexplained_inventory_jumps,
    check_sales_exceeding_inventory,
    check_duplicate_transaction_ids
)

# Friendly functional aliases matching prompt specifications
inventory_turnover = calculate_inventory_turnover
detect_slow_movers = detect_slow_moving_products
detect_overstock = detect_overstocked_products

__all__ = [
    # Models
    "ProductPerformance",
    "StorePerformance",
    "InventoryHealth",
    "InventoryTurnover",
    "SlowMoverResult",
    "OverstockResult",
    "StockoutRiskResult",
    "SalesAnomalyResult",
    "DataQualityReport",
    # Functions
    "calculate_product_performance",
    "calculate_store_performance",
    "calculate_inventory_health",
    "calculate_inventory_turnover",
    "inventory_turnover",
    "get_inventory_health_summary",
    "detect_slow_moving_products",
    "detect_slow_movers",
    "detect_overstocked_products",
    "detect_overstock",
    "calculate_stockout_risk",
    "assess_all_stockout_risks",
    "detect_sales_anomalies",
    "check_inventory_data_quality",
    "check_negative_stock",
    "check_impossible_quantities",
    "check_missing_references",
    "check_unexplained_inventory_jumps",
    "check_sales_exceeding_inventory",
    "check_duplicate_transaction_ids"
]
