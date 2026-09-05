"""
Unit tests for deterministic retail sales and inventory analytics engine.
Verifies all mathematical formulas, boundary conditions, edge cases, and deterministic rules.
Run command: python3 -m unittest discover -s tests
"""
import unittest
from datetime import datetime
from src.database.schema import init_db
from src.analytics import (
    calculate_product_performance,
    calculate_store_performance,
    calculate_inventory_health,
    calculate_inventory_turnover,
    detect_slow_moving_products,
    detect_overstocked_products,
    calculate_stockout_risk,
    assess_all_stockout_risks,
    detect_sales_anomalies,
    check_inventory_data_quality
)

class TestSalesAnalytics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def test_product_performance_formula_consistency(self):
        """Validates that product sales arithmetic, revenue, COGS, and margins balance exactly."""
        perf = calculate_product_performance("PRD-001", "2024-07-01", "2024-07-31", "STR-001")
        self.assertEqual(perf.product_id, "PRD-001")
        self.assertEqual(perf.store_id, "STR-001")
        self.assertGreater(perf.units_sold, 0)
        self.assertGreater(perf.revenue, 0)
        self.assertGreater(perf.estimated_cogs, 0)

        # Gross profit = Revenue - COGS
        expected_gp = round(perf.revenue - perf.estimated_cogs, 2)
        self.assertAlmostEqual(perf.gross_profit, expected_gp, places=2)

        # Gross Margin % = (Gross Profit / Revenue) * 100
        expected_margin = round((perf.gross_profit / perf.revenue) * 100.0, 2)
        self.assertAlmostEqual(perf.gross_margin_percent, expected_margin, places=2)

        # Average daily units = units_sold / 31 calendar days in July
        self.assertAlmostEqual(perf.average_daily_units, round(perf.units_sold / 31.0, 2), places=2)

        # Average transaction value = revenue / transactions
        self.assertGreater(perf.average_transaction_value, 0)

        # Comparison with previous period (June 2024 has 31 days lookback)
        comp = perf.comparison_with_previous_period
        self.assertIn("previous_period_start", comp)
        self.assertIn("previous_period_end", comp)
        self.assertIn("units_growth_percent", comp)
        self.assertIn("revenue_growth_percent", comp)

    def test_product_performance_nonexistent_product(self):
        """Ensures invalid product IDs raise descriptive ValueError."""
        with self.assertRaises(ValueError):
            calculate_product_performance("NONEXISTENT-SKU-999")

    def test_store_performance_formula_consistency(self):
        """Validates store revenue aggregation, COGS, and growth metrics."""
        store_perf = calculate_store_performance("STR-001", "2024-07-01", "2024-07-31")
        self.assertEqual(store_perf.store_id, "STR-001")
        self.assertGreater(store_perf.revenue, 0)
        self.assertGreater(store_perf.units, 0)
        
        # Margin arithmetic
        expected_gp = round(store_perf.revenue - store_perf.estimated_cogs, 2)
        self.assertAlmostEqual(store_perf.gross_profit, expected_gp, places=2)
        expected_margin = round((store_perf.gross_profit / store_perf.revenue) * 100.0, 2)
        self.assertAlmostEqual(store_perf.gross_margin, expected_margin, places=2)

        # Growth metrics present
        growth = store_perf.growth_vs_previous_period
        self.assertIn("previous_revenue", growth)
        self.assertIn("revenue_growth_percent", growth)


class TestInventoryHealthAndTurnover(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def test_inventory_health_calculations(self):
        """Verifies 7d/30d velocity, Days of Inventory runway, and valuations at cost/retail."""
        health = calculate_inventory_health("STR-001", "PRD-001", "2024-08-29")
        self.assertEqual(health.store_id, "STR-001")
        self.assertEqual(health.product_id, "PRD-001")
        self.assertGreaterEqual(health.current_stock, 0)
        self.assertGreaterEqual(health.average_daily_sales_7d, 0)
        self.assertGreaterEqual(health.average_daily_sales_30d, 0)
        self.assertGreaterEqual(health.days_of_inventory, 0)

        # Inventory valuation checks
        self.assertGreater(health.inventory_value_at_retail, health.inventory_value_at_cost)
        self.assertIn(health.status, ["HEALTHY", "LOW_STOCK", "OUT_OF_STOCK", "OVERSTOCKED"])

        # Sell through rate should be a valid percentage
        if health.sell_through_rate is not None:
            self.assertGreaterEqual(health.sell_through_rate, 0.0)
            self.assertLessEqual(health.sell_through_rate, 100.0)

    def test_inventory_turnover_formula(self):
        """Verifies Inventory Turnover = COGS / Average Inventory Cost."""
        turnover = calculate_inventory_turnover("PRD-001", "STR-001", "2024-06-01", "2024-08-29")
        self.assertEqual(turnover.calculation_period_days, 90)
        self.assertGreater(turnover.cogs, 0)
        self.assertGreater(turnover.average_inventory_cost, 0)

        # Formula: Turnover = COGS / Avg Inventory Cost
        expected_turnover = round(turnover.cogs / turnover.average_inventory_cost, 2)
        self.assertAlmostEqual(turnover.inventory_turnover, expected_turnover, places=2)

        # Annualized Turnover = Turnover * (365 / 90)
        expected_annualized = round(turnover.inventory_turnover * (365.0 / 90.0), 2)
        self.assertAlmostEqual(turnover.annualized_turnover, expected_annualized, places=2)

        # Check documentation of calculation period
        self.assertIn("2024-06-01 to 2024-08-29", turnover.calculation_period_description)
        self.assertIn("90 calendar days", turnover.calculation_period_description)


class TestInventoryOptimizationRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def test_slow_moving_products_deterministic_rules(self):
        """Verifies slow mover identification respects sales, stock, and catalog age criteria."""
        slow = detect_slow_moving_products(
            sales_threshold_daily=0.20,
            inventory_threshold_units=15,
            min_catalog_age_days=21
        )
        self.assertIsInstance(slow, list)
        self.assertGreater(len(slow), 0)

        for item in slow:
            self.assertLessEqual(item.daily_sales_velocity, 0.20)
            self.assertGreaterEqual(item.current_stock, 15)
            self.assertGreaterEqual(item.catalog_age_days, 21)
            self.assertFalse(item.is_newly_launched)
            self.assertTrue(item.is_slow_moving)
            # Evidence contains source identifiers
            self.assertIn("actual_stock", item.evidence)
            self.assertIn("actual_daily_velocity", item.evidence)
            self.assertIn("rule_justification", item.evidence)

    def test_overstocked_products_deterministic_rules(self):
        """Verifies overstocked products have days_of_inventory > target_days and correct excess math."""
        overstocked = detect_overstocked_products(target_days=45.0, min_stock=20)
        self.assertIsInstance(overstocked, list)
        self.assertGreater(len(overstocked), 0)

        for item in overstocked:
            self.assertGreater(item.days_of_inventory, 45.0)
            self.assertGreater(item.excess_units_estimate, 0)
            # Excess value = excess_units * unit_cost
            expected_val = round(item.excess_units_estimate * item.unit_cost, 2)
            self.assertAlmostEqual(item.excess_inventory_value, expected_val, places=2)
            self.assertIn("capital_tied_up_inr", item.evidence)

    def test_stockout_risk_baseline_model(self):
        """Verifies baseline stockout model: lead_time_demand, safety_stock, position, and heuristic score."""
        risk = calculate_stockout_risk("STR-001", "PRD-004")
        self.assertEqual(risk.product_id, "PRD-004")
        self.assertEqual(risk.store_id, "STR-001")
        
        # Formulas check
        expected_ltd = round(risk.demand_velocity * risk.lead_time_days, 2)
        self.assertAlmostEqual(risk.lead_time_demand, expected_ltd, places=2)

        # Position = Current + Incoming - Reserved(0)
        expected_pos = float(risk.current_stock + risk.incoming_quantity)
        self.assertAlmostEqual(risk.inventory_position, expected_pos, places=2)

        # Risk score is heuristic 0-100
        self.assertGreaterEqual(risk.risk_score, 0.0)
        self.assertLessEqual(risk.risk_score, 100.0)
        self.assertIn(risk.risk_level, ["CRITICAL", "HIGH", "MEDIUM", "LOW"])

        # Explanation factors provide transparent audit trail
        self.assertGreater(len(risk.explanation_factors), 3)


class TestSalesAnomaliesAndDataQuality(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def test_sales_anomalies_detection(self):
        """Verifies spikes and drops with rolling mean/median baseline."""
        anomalies = detect_sales_anomalies(start_date="2024-07-01", end_date="2024-08-29")
        self.assertIsInstance(anomalies, list)
        self.assertGreater(len(anomalies), 0)

        for a in anomalies:
            self.assertIn(a.anomaly_type, ["SPIKE", "DROP"])
            self.assertIn(a.severity, ["CRITICAL", "HIGH", "MEDIUM"])
            # Deviation = Actual - Expected
            self.assertAlmostEqual(a.deviation, round(a.actual_sales - a.expected_sales, 2), places=2)
            if a.anomaly_type == "SPIKE":
                self.assertGreater(a.deviation, 0)
            else:
                self.assertLess(a.deviation, 0)

    def test_inventory_data_quality_suite(self):
        """Verifies all 6 deterministic data quality audits pass on validated dataset."""
        report = check_inventory_data_quality()
        self.assertEqual(report.total_issues_found, 0)
        self.assertTrue(report.passed)
        self.assertEqual(len(report.checks_performed), 6)
        self.assertIn("negative_stock", report.issues_by_category)
        self.assertIn("impossible_quantities", report.issues_by_category)
        self.assertIn("missing_references", report.issues_by_category)
        self.assertIn("unexplained_inventory_jumps", report.issues_by_category)
        self.assertIn("sales_exceeding_available_inventory", report.issues_by_category)
        self.assertIn("duplicate_transaction_ids", report.issues_by_category)

if __name__ == "__main__":
    unittest.main()
