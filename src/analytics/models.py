"""
Structured typed data models for deterministic retail analytics.
Provides dataclasses with .to_dict() methods for serialization and clean interfaces.
"""
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List, Optional

@dataclass
class ProductPerformance:
    product_id: str
    product_name: str
    sku: str
    category: str
    store_id: Optional[str]
    start_date: str
    end_date: str
    units_sold: int
    revenue: float
    estimated_cogs: float
    gross_profit: float
    gross_margin_percent: float
    average_daily_units: float
    average_transaction_value: float
    number_of_sales_days: int
    comparison_with_previous_period: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class StorePerformance:
    store_id: str
    store_name: str
    start_date: str
    end_date: str
    revenue: float
    units: int
    estimated_cogs: float
    gross_profit: float
    gross_margin: float
    growth_vs_previous_period: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class InventoryHealth:
    store_id: str
    product_id: str
    product_name: str
    as_of_date: str
    current_stock: int
    average_daily_sales_7d: float
    average_daily_sales_30d: float
    days_of_inventory: float
    inventory_value_at_cost: float
    inventory_value_at_retail: float
    sell_through_rate: Optional[float]
    reorder_point: int
    status: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class InventoryTurnover:
    product_id: Optional[str]
    store_id: Optional[str]
    start_date: str
    end_date: str
    calculation_period_days: int
    cogs: float
    average_inventory_cost: float
    inventory_turnover: float
    annualized_turnover: float
    calculation_period_description: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class SlowMoverResult:
    product_id: str
    product_name: str
    category: str
    store_id: str
    store_name: str
    current_stock: int
    daily_sales_velocity: float
    units_sold_in_period: int
    days_of_inventory: float
    first_recorded_date: str
    catalog_age_days: int
    is_newly_launched: bool
    is_slow_moving: bool
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class OverstockResult:
    product_id: str
    product_name: str
    sku: str
    category: str
    store_id: str
    store_name: str
    current_stock: int
    demand_velocity: float
    days_of_inventory: float
    target_days: float
    target_stock: int
    excess_units_estimate: int
    excess_inventory_value: float
    unit_cost: float
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class StockoutRiskResult:
    store_id: str
    store_name: str
    product_id: str
    product_name: str
    current_stock: int
    demand_velocity: float
    lead_time_days: int
    lead_time_demand: float
    safety_stock: float
    incoming_quantity: int
    inventory_position: float
    days_of_inventory: float
    risk_level: str
    risk_score: float
    explanation_factors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class SalesAnomalyResult:
    expected_sales: float
    actual_sales: float
    deviation: float
    percentage_change: float
    date: str
    store_id: str
    store_name: str
    product_id: str
    product_name: str
    anomaly_type: str
    severity: str
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class DataQualityReport:
    as_of_date: str
    total_issues_found: int
    passed: bool
    checks_performed: List[str] = field(default_factory=list)
    issues_by_category: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
