"""
Anomaly detection package: deterministic statistical spike, drop, and discrepancy detection.
"""
from src.anomaly.detector import detect_sales_spikes_and_drops, detect_overstocked_items

__all__ = [
    "detect_sales_spikes_and_drops",
    "detect_overstocked_items"
]
