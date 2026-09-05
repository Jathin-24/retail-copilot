"""
Forecasting package: deterministic demand run-rate and days-of-supply calculations.
"""
from src.forecasting.demand import calculate_days_of_supply, project_stockout_dates

__all__ = [
    "calculate_days_of_supply",
    "project_stockout_dates"
]
