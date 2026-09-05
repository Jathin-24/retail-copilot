"""
Tests for Evidence Grounding, Strict Refusal Rules, and Epistemic Tiers
TRACK_ID: PS03

Requirements:
- NEVER MAKE A CLAIM WITHOUT SUPPORTING DATA.
- Explicit evidence layer in src/evidence.py
- Refusal/qualification rules for:
  1. Unsupported product
  2. Missing data / non-existent store
  3. Insufficient date range
  4. Causal question without causal evidence (e.g. "Why did sales fall?")
  5. Gemini failure graceful handling
- Epistemic 5-tier distinction:
  OBSERVED FACT, CALCULATED METRIC, INFERENCE, RECOMMENDATION, ASSUMPTION.
"""
import unittest
from unittest.mock import MagicMock, patch

from src.database.schema import init_db
from src.copilot import (
    RetailCopilot,
    CopilotResponse,
    DATABASE_MIN_DATE,
    DATABASE_MAX_DATE,
    is_causal_question,
    check_date_range_validity
)
from src.evidence import (
    MetricEvidence,
    EvidencePackage,
    build_stockout_evidence,
    build_causal_inquiry_evidence
)


class TestEvidenceGrounding(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        cls.copilot = RetailCopilot(api_key=None)

    # -------------------------------------------------------------------------
    # 1. Unsupported Product Test
    # -------------------------------------------------------------------------
    def test_unsupported_product(self):
        """When product does not exist in catalog, copilot must refuse with UNSUPPORTED_PRODUCT."""
        res = self.copilot.ask("How did PlayStation 5 perform in sales?")
        self.assertIsInstance(res, CopilotResponse)
        self.assertFalse(res.data_sufficient)
        self.assertEqual(res.refusal_reason, "UNSUPPORTED_PRODUCT")
        self.assertIn("PlayStation 5", res.answer)
        self.assertTrue(len(res.observed_facts) > 0)
        self.assertTrue(any("catalog" in f.lower() for f in res.observed_facts))
        self.assertTrue(any("not indexed" in inf.lower() for inf in res.inferences))
        self.assertIsNone(res.evidence_package)

    # -------------------------------------------------------------------------
    # 2. Missing Data / Non-existent Store Test
    # -------------------------------------------------------------------------
    def test_missing_data_non_existent_store(self):
        """When requested store is not in local SQLite, copilot must refuse with NON_EXISTENT_STORE."""
        res = self.copilot.ask("What is running out in Tokyo?")
        self.assertIsInstance(res, CopilotResponse)
        self.assertFalse(res.data_sufficient)
        self.assertEqual(res.refusal_reason, "NON_EXISTENT_STORE")
        self.assertIn("Tokyo", res.answer)
        self.assertTrue(len(res.observed_facts) > 0)
        # Should inform the user of available store locations
        self.assertTrue(any("Hyderabad" in f or "Bengaluru" in f or "Mumbai" in f for f in res.observed_facts))

    # -------------------------------------------------------------------------
    # 3. Insufficient Date Range Test
    # -------------------------------------------------------------------------
    def test_insufficient_date_range(self):
        """When question targets dates outside 2024-06-01 to 2024-08-29, copilot must refuse."""
        # Case A: Year 2026
        res_2026 = self.copilot.ask("What were total sales in 2026?")
        self.assertIsInstance(res_2026, CopilotResponse)
        self.assertFalse(res_2026.data_sufficient)
        self.assertEqual(res_2026.refusal_reason, "INSUFFICIENT_DATE_RANGE")
        self.assertIn("insufficient data", res_2026.answer.lower())
        self.assertIn(DATABASE_MIN_DATE, res_2026.answer)
        self.assertIn(DATABASE_MAX_DATE, res_2026.answer)

        # Case B: Future horizon
        res_future = self.copilot.ask("Forecast sales for next year")
        self.assertFalse(res_future.data_sufficient)
        self.assertEqual(res_future.refusal_reason, "INSUFFICIENT_DATE_RANGE")

    # -------------------------------------------------------------------------
    # 4. Causal Question Without Causal Evidence Test
    # -------------------------------------------------------------------------
    def test_causal_question_without_causal_evidence(self):
        """
        User asks: "Why did sales fall?"
        Copilot must refuse causal speculation, state observed facts, inventory availability,
        and price figure, and document missing external datasets (competitor, feedback, marketing).
        Must NEVER say 'Customers preferred competitors'.
        """
        res = self.copilot.ask("Why did sales fall?")
        self.assertIsInstance(res, CopilotResponse)
        self.assertEqual(res.refusal_reason, "UNESTABLISHED_CAUSALITY")
        
        # Grounded answer checks
        ans = res.answer
        self.assertIn("cannot establish the cause", ans)
        self.assertIn("Inventory availability was", ans)
        self.assertIn("price was", ans)
        self.assertIn("competitor pricing", ans)
        self.assertIn("customer feedback", ans)
        self.assertIn("marketing data", ans)

        # Strictly forbidden hallucination check
        self.assertNotIn("Customers preferred competitors", ans)
        self.assertNotIn("preferred competitor", ans.lower())

        # Limitations must explicitly list missing external variables
        self.assertTrue(len(res.limitations) > 0)
        limit_text = " ".join(res.limitations).lower()
        self.assertIn("competitor pricing", limit_text)
        self.assertIn("marketing", limit_text)

        # Evidence package verification
        pkg = res.evidence_package or res.evidence
        self.assertIn("metrics", pkg)
        self.assertIn("source_tables", pkg)
        self.assertIn("causal_limitations", pkg)

    # -------------------------------------------------------------------------
    # 5. Gemini Failure Graceful Handling Test
    # -------------------------------------------------------------------------
    def test_gemini_failure_graceful_fallback(self):
        """Simulated Gemini API error must result in deterministic fallback with full 5-tier fields."""
        copilot = RetailCopilot(api_key="fake-gemini-test-key")
        with patch.object(copilot, "_get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.models.generate_content.side_effect = RuntimeError("Gemini API connection timed out (504)")
            mock_get_client.return_value = mock_client

            res = copilot.ask("What is running out in Hyderabad?")
            self.assertIsInstance(res, CopilotResponse)
            self.assertTrue(res.data_sufficient)
            self.assertIn("deterministic", res.confidence_note.lower())
            self.assertIn("standby", res.confidence_note.lower())
            # 5-tier fields must all be populated
            self.assertTrue(len(res.observed_facts) > 0)
            self.assertTrue(len(res.calculated_metrics) > 0)
            self.assertTrue(len(res.inferences) > 0)
            self.assertTrue(len(res.recommendations) > 0)
            self.assertTrue(len(res.assumptions) > 0)

    # -------------------------------------------------------------------------
    # 6. Epistemic 5-Tier Distinction Test
    # -------------------------------------------------------------------------
    def test_epistemic_five_tier_distinction(self):
        """Every valid copilot response must populate all 5 epistemic categories distinctly."""
        queries = [
            "What is running out?",
            "What is overstocked?",
            "Show slow moving products",
            "Which stores are performing well?"
        ]
        for q in queries:
            res = self.copilot.ask(q)
            self.assertIsInstance(res, CopilotResponse)
            self.assertIsInstance(res.observed_facts, list, f"observed_facts missing in '{q}'")
            self.assertIsInstance(res.calculated_metrics, list, f"calculated_metrics missing in '{q}'")
            self.assertIsInstance(res.inferences, list, f"inferences missing in '{q}'")
            self.assertIsInstance(res.recommendations, list, f"recommendations missing in '{q}'")
            self.assertIsInstance(res.assumptions, list, f"assumptions missing in '{q}'")
            self.assertTrue(len(res.observed_facts) > 0, f"empty observed_facts in '{q}'")
            self.assertTrue(len(res.calculated_metrics) > 0, f"empty calculated_metrics in '{q}'")
            self.assertTrue(len(res.inferences) > 0, f"empty inferences in '{q}'")
            self.assertTrue(len(res.recommendations) > 0, f"empty recommendations in '{q}'")

    # -------------------------------------------------------------------------
    # 7. Evidence Layer Structure Test (src/evidence.py)
    # -------------------------------------------------------------------------
    def test_evidence_structure_compliance(self):
        """
        Validates the structure defined in user request:
        {
          "metric": "days_of_inventory",
          "value": 3.4,
          "unit": "days",
          "source": "inventory.csv + sales.csv",
          "period": "...",
          "calculation": "current_stock / average_daily_sales_7d"
        }
        """
        metric_ev = MetricEvidence(
            metric="days_of_inventory",
            value=3.4,
            unit="days",
            source="inventory.csv + sales.csv",
            period="2024-08-23 to 2024-08-29",
            calculation="current_stock / average_daily_sales_7d",
            raw_values={"current_stock": 34, "average_daily_sales_7d": 10.0}
        )
        d = metric_ev.model_dump()
        self.assertEqual(d["metric"], "days_of_inventory")
        self.assertEqual(d["value"], 3.4)
        self.assertEqual(d["unit"], "days")
        self.assertEqual(d["source"], "inventory.csv + sales.csv")
        self.assertEqual(d["period"], "2024-08-23 to 2024-08-29")
        self.assertEqual(d["calculation"], "current_stock / average_daily_sales_7d")

        # Test EvidencePackage
        pkg = build_stockout_evidence(
            store_id="STR-001",
            store_name="Nexus Koramangala",
            city="Bengaluru",
            product_id="PRD-004",
            product_name="Heritage Special Ghee 500ml",
            sku="SKU-SNK-004",
            current_stock=12,
            lead_time_days=4,
            lead_time_demand=18.4,
            daily_demand=4.6,
            safety_stock=8.0,
            incoming_stock=0,
            risk_score=72.5,
            risk_level="HIGH",
            days_of_inventory=2.6
        )
        pkg_dict = pkg.to_dict()
        self.assertIn("source_tables", pkg_dict)
        self.assertIn("metrics", pkg_dict)
        self.assertIn("raw_values", pkg_dict)
        self.assertIn("calculated_values", pkg_dict)
        self.assertIn("formulas", pkg_dict)
        self.assertIn("assumptions", pkg_dict)
        self.assertIn("data_quality_warnings", pkg_dict)
        self.assertTrue(len(pkg_dict["metrics"]) >= 4)


if __name__ == "__main__":
    unittest.main()
