"""
Deterministic sales anomaly detection for spikes and drops.
Uses a robust rolling baseline (rolling mean and median) with standard deviation
thresholds to detect statistically significant sales fluctuations.
"""
from datetime import datetime, timedelta
import math
from typing import List, Dict, Any, Optional
from src.database.connection import get_db_connection
from src.analytics.models import SalesAnomalyResult

def detect_sales_anomalies(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    store_id: Optional[str] = None,
    product_id: Optional[str] = None,
    window_days: int = 14,
    std_dev_multiplier: float = 2.0,
    min_pct_change: float = 50.0
) -> List[SalesAnomalyResult]:
    """
    Deterministically scans historical sales transactions for unusual spikes and drops
    compared against a rolling baseline of the preceding `window_days`.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    if not start_date or not end_date:
        cursor.execute("SELECT MIN(date), MAX(date) FROM sales")
        min_date, max_date = cursor.fetchone()
        start = start_date or min_date or "2024-06-01"
        end = end_date or max_date or "2024-08-29"
    else:
        start = start_date
        end = end_date

    # Need baseline lookback window prior to start date
    dt_start = datetime.strptime(start, "%Y-%m-%d")
    dt_query_start = dt_start - timedelta(days=window_days)
    query_start_str = dt_query_start.strftime("%Y-%m-%d")

    # Fetch daily aggregated sales
    query = """
        SELECT 
            s.date,
            s.store_id,
            st.store_name,
            s.product_id,
            p.product_name,
            SUM(s.quantity) as daily_units
        FROM sales s
        JOIN stores st ON s.store_id = st.store_id
        JOIN products p ON s.product_id = p.product_id
        WHERE s.date BETWEEN ? AND ?
    """
    params = [query_start_str, end]
    if store_id:
        query += " AND s.store_id = ?"
        params.append(store_id)
    if product_id:
        query += " AND s.product_id = ?"
        params.append(product_id)

    query += " GROUP BY s.date, s.store_id, st.store_name, s.product_id, p.product_name ORDER BY s.store_id, s.product_id, s.date ASC"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    # Group records by (store_id, product_id)
    series_map: Dict[tuple, List[Dict[str, Any]]] = {}
    for r in rows:
        key = (r["store_id"], r["product_id"])
        if key not in series_map:
            series_map[key] = []
        series_map[key].append(dict(r))

    anomalies: List[SalesAnomalyResult] = []

    for (s_id, p_id), records in series_map.items():
        # Map date -> daily_units
        date_sales = {r["date"]: r["daily_units"] for r in records}
        store_name = records[0]["store_name"]
        product_name = records[0]["product_name"]

        # Evaluate each date in the target window [start, end]
        for rec in records:
            rec_date = rec["date"]
            if rec_date < start or rec_date > end:
                continue

            actual_sales = float(rec["daily_units"])
            dt_curr = datetime.strptime(rec_date, "%Y-%m-%d")

            # Collect past window_days values
            past_values = []
            for i in range(1, window_days + 1):
                past_date_str = (dt_curr - timedelta(days=i)).strftime("%Y-%m-%d")
                past_values.append(date_sales.get(past_date_str, 0))

            if len(past_values) < window_days // 2:
                continue

            # Robust baseline: mean and median
            mean_baseline = sum(past_values) / float(len(past_values))
            sorted_vals = sorted(past_values)
            mid = len(sorted_vals) // 2
            median_baseline = float(sorted_vals[mid])

            expected_sales = round(mean_baseline, 2)
            variance = sum((v - mean_baseline) ** 2 for v in past_values) / float(len(past_values))
            std_dev = math.sqrt(variance)
            threshold_dev = max(1.5, std_dev * std_dev_multiplier)

            deviation = round(actual_sales - expected_sales, 2)
            if expected_sales > 0:
                pct_change = round((deviation / expected_sales) * 100.0, 1)
            else:
                pct_change = 100.0 if actual_sales >= 5 else 0.0

            # 1. Unusual Sales Spike: actual significantly exceeds expected
            if deviation >= threshold_dev and pct_change >= min_pct_change and actual_sales >= 5:
                severity = "CRITICAL" if pct_change >= 200 else ("HIGH" if pct_change >= 100 else "MEDIUM")
                evidence = {
                    "baseline_window_days": window_days,
                    "rolling_mean": expected_sales,
                    "rolling_median": median_baseline,
                    "sample_standard_deviation": round(std_dev, 2),
                    "threshold_applied": round(threshold_dev, 2),
                    "z_score": round(deviation / max(0.5, std_dev), 2),
                    "explanation": f"Daily sales of {actual_sales} surged by +{pct_change}% over 14-day rolling baseline of {expected_sales} units."
                }
                anomalies.append(SalesAnomalyResult(
                    expected_sales=expected_sales,
                    actual_sales=actual_sales,
                    deviation=deviation,
                    percentage_change=pct_change,
                    date=rec_date,
                    store_id=s_id,
                    store_name=store_name,
                    product_id=p_id,
                    product_name=product_name,
                    anomaly_type="SPIKE",
                    severity=severity,
                    evidence=evidence
                ))

            # 2. Unusual Sales Drop: actual significantly lower than expected
            elif deviation <= -threshold_dev and pct_change <= -min_pct_change and expected_sales >= 5:
                severity = "CRITICAL" if pct_change <= -80 else ("HIGH" if pct_change <= -60 else "MEDIUM")
                evidence = {
                    "baseline_window_days": window_days,
                    "rolling_mean": expected_sales,
                    "rolling_median": median_baseline,
                    "sample_standard_deviation": round(std_dev, 2),
                    "threshold_applied": round(threshold_dev, 2),
                    "z_score": round(deviation / max(0.5, std_dev), 2),
                    "explanation": f"Daily sales of {actual_sales} collapsed by {pct_change}% below 14-day rolling baseline of {expected_sales} units."
                }
                anomalies.append(SalesAnomalyResult(
                    expected_sales=expected_sales,
                    actual_sales=actual_sales,
                    deviation=deviation,
                    percentage_change=pct_change,
                    date=rec_date,
                    store_id=s_id,
                    store_name=store_name,
                    product_id=p_id,
                    product_name=product_name,
                    anomaly_type="DROP",
                    severity=severity,
                    evidence=evidence
                ))

    # Sort anomalies by magnitude of deviation
    return sorted(anomalies, key=lambda x: abs(x.deviation), reverse=True)
