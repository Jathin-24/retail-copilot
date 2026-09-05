"""
Integration tests for FastAPI REST analytical endpoints in app.py.
Validates all endpoints return expected schema, correct HTTP status codes, and deterministic metrics.
"""
import unittest
from fastapi.testclient import TestClient
from app import app
from src.database.schema import init_db

class TestAnalyticsAPIEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        cls.client = TestClient(app)

    def test_health_check(self):
        resp = self.client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "healthy")
        self.assertEqual(data["track_id"], "PS03")

    def test_product_performance_endpoint(self):
        # Single product
        resp = self.client.get("/api/analytics/product-performance?product_id=PRD-001&store_id=STR-001&start_date=2024-07-01&end_date=2024-07-31")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["product_id"], "PRD-001")
        self.assertGreater(data["revenue"], 0)
        self.assertIn("comparison_with_previous_period", data)

        # Overview of all products
        resp_all = self.client.get("/api/analytics/product-performance?start_date=2024-07-01&end_date=2024-07-31")
        self.assertEqual(resp_all.status_code, 200)
        self.assertIsInstance(resp_all.json(), list)

    def test_store_performance_endpoint(self):
        # Single store
        resp = self.client.get("/api/analytics/store-performance?store_id=STR-001&start_date=2024-07-01&end_date=2024-07-31")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["store_id"], "STR-001")
        self.assertIn("growth_vs_previous_period", data)

        # All stores
        resp_all = self.client.get("/api/analytics/store-performance?start_date=2024-07-01&end_date=2024-07-31")
        self.assertEqual(resp_all.status_code, 200)
        self.assertIsInstance(resp_all.json(), list)

    def test_inventory_health_endpoint(self):
        # Specific item
        resp = self.client.get("/api/analytics/inventory-health?store_id=STR-001&product_id=PRD-001")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["product_id"], "PRD-001")
        self.assertIn("days_of_inventory", data)

        # Overall summary
        resp_summary = self.client.get("/api/analytics/inventory-health")
        self.assertEqual(resp_summary.status_code, 200)
        data_summary = resp_summary.json()
        self.assertIn("total_units_in_stock", data_summary)

    def test_inventory_turnover_endpoint(self):
        resp = self.client.get("/api/analytics/inventory-turnover?product_id=PRD-001&store_id=STR-001")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("inventory_turnover", data)
        self.assertIn("cogs", data)
        self.assertIn("calculation_period_days", data)

    def test_slow_movers_endpoint(self):
        resp = self.client.get("/api/analytics/slow-movers?sales_threshold_daily=0.20&inventory_threshold_units=15")
        self.assertEqual(resp.status_code, 200)
        items = resp.json()
        self.assertIsInstance(items, list)
        if len(items) > 0:
            self.assertTrue(items[0]["is_slow_moving"])
            self.assertIn("evidence", items[0])

    def test_overstock_endpoint(self):
        resp = self.client.get("/api/analytics/overstock?target_days=45.0&min_stock=20")
        self.assertEqual(resp.status_code, 200)
        items = resp.json()
        self.assertIsInstance(items, list)
        if len(items) > 0:
            self.assertGreater(items[0]["days_of_inventory"], 45.0)
            self.assertIn("excess_inventory_value", items[0])

    def test_stockout_risk_endpoint(self):
        # Single product
        resp = self.client.get("/api/analytics/stockout-risk?store_id=STR-001&product_id=PRD-004")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["product_id"], "PRD-004")
        self.assertIn("risk_level", data)
        self.assertIn("risk_score", data)
        self.assertIn("lead_time_demand", data)

        # Batch scan
        resp_all = self.client.get("/api/analytics/stockout-risk?store_id=STR-001&min_risk_score=50.0")
        self.assertEqual(resp_all.status_code, 200)
        self.assertIsInstance(resp_all.json(), list)

    def test_anomalies_endpoint(self):
        resp = self.client.get("/api/analytics/anomalies?store_id=STR-001&product_id=PRD-001")
        self.assertEqual(resp.status_code, 200)
        items = resp.json()
        self.assertIsInstance(items, list)

    def test_data_quality_endpoint(self):
        resp = self.client.get("/api/analytics/data-quality")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("total_issues_found", data)
        self.assertIn("passed", data)
        self.assertIn("checks_performed", data)
        self.assertTrue(data["passed"])

if __name__ == "__main__":
    unittest.main()
