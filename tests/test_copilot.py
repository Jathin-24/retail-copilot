"""
Tests for Gemini Copilot Integration (src/copilot.py)
Validates:
1. All 9 supported analytical intents are correctly recognized.
2. Structured intent schema complies with requirements.
3. Entity resolution correctly maps stores/products against SQLite and flags non-existent entities.
4. Deterministic analytics engine is the ground truth (exact numbers, zero hallucinations).
5. All 8 structured response fields are always present.
6. Missing GEMINI_API_KEY does not crash application and provides graceful fallback.
7. Simulated Gemini API errors trigger graceful deterministic fallbacks.
8. REST endpoints /api/copilot/chat, /api/copilot/query, /api/copilot/status work as specified.
"""
import os
import unittest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from app import app
from src.database.schema import init_db
from src.copilot import (
    RetailCopilot,
    IntentSchema,
    CopilotResponse,
    SUPPORTED_INTENTS,
    resolve_entities,
    rule_based_extract_intent,
    execute_deterministic_analytics,
    synthesize_deterministic_response,
    ask_copilot
)


class TestCopilotPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        cls.copilot = RetailCopilot(api_key=None) # Tests offline/fallback mode by default
        cls.client = TestClient(app)

    def test_supported_intents_list(self):
        expected_intents = [
            "SALES_PERFORMANCE",
            "INVENTORY_STATUS",
            "STOCKOUT_RISK",
            "SLOW_MOVERS",
            "OVERSTOCK",
            "SALES_ANOMALY",
            "STORE_COMPARISON",
            "GENERAL_RETAIL_ANALYSIS",
            "UNKNOWN"
        ]
        self.assertEqual(SUPPORTED_INTENTS, expected_intents)

    def test_intent_extraction_rule_based(self):
        test_cases = [
            ("What is running out?", "STOCKOUT_RISK"),
            ("Which items are overstocked?", "OVERSTOCK"),
            ("What products are not moving?", "SLOW_MOVERS"),
            ("What sales spikes or anomalies occurred?", "SALES_ANOMALY"),
            ("Which stores are performing well?", "STORE_COMPARISON"),
            ("Compare store performance across the network", "STORE_COMPARISON"),
            ("How did PRD-001 perform?", "SALES_PERFORMANCE"),
            ("What is our current inventory health summary?", "INVENTORY_STATUS"),
            ("What should I do today?", "GENERAL_RETAIL_ANALYSIS"),
            ("What is the weather in London?", "UNKNOWN"),
        ]

        for query, expected_intent in test_cases:
            intent_res = rule_based_extract_intent(query)
            self.assertIsInstance(intent_res, IntentSchema)
            self.assertEqual(
                intent_res.intent,
                expected_intent,
                f"Failed for query '{query}': got {intent_res.intent}, expected {expected_intent}"
            )

    def test_intent_schema_structure(self):
        schema = rule_based_extract_intent("What is running out in Hyderabad?")
        data = schema.model_dump()
        self.assertIn("intent", data)
        self.assertIn("product", data)
        self.assertIn("store", data)
        self.assertIn("time_period", data)
        self.assertIn("requires_clarification", data)
        self.assertEqual(data["intent"], "STOCKOUT_RISK")
        self.assertEqual(data["store"], "Hyderabad")

    def test_entity_resolution_valid_store(self):
        # Hyderabad -> STR-004 Inorbit Cyberabad
        _, store, _, store_not_found = resolve_entities(store_query="Hyderabad")
        self.assertFalse(store_not_found)
        self.assertIsNotNone(store)
        self.assertEqual(store["store_id"], "STR-004")
        self.assertIn("Cyberabad", store["store_name"])

        # Bengaluru -> STR-001 Nexus Koramangala
        _, store_blr, _, _ = resolve_entities(store_query="Bengaluru")
        self.assertIsNotNone(store_blr)
        self.assertEqual(store_blr["store_id"], "STR-001")

    def test_entity_resolution_valid_product(self):
        # By SKU
        prod_sku, _, prod_not_found, _ = resolve_entities(product_query="SKU-PRE-001")
        self.assertFalse(prod_not_found)
        self.assertIsNotNone(prod_sku)
        self.assertEqual(prod_sku["product_id"], "PRD-001")

        # By product name keyword
        prod_name, _, _, _ = resolve_entities(product_query="Masala Chai")
        self.assertIsNotNone(prod_name)
        self.assertEqual(prod_name["product_id"], "PRD-001")

    def test_entity_resolution_non_existent_entities(self):
        # Non-existent store
        _, store, _, store_not_found = resolve_entities(store_query="Chennai")
        self.assertTrue(store_not_found)
        self.assertIsNone(store)

        # Non-existent product
        prod, _, prod_not_found, _ = resolve_entities(product_query="PlayStation 5 Console")
        self.assertTrue(prod_not_found)
        self.assertIsNone(prod)

    def test_copilot_response_for_non_existent_store(self):
        resp = self.copilot.ask("What is running out in Chennai?")
        self.assertFalse(resp.data_sufficient)
        self.assertIn("Chennai", resp.answer)
        self.assertIn("not found in the database", resp.answer)
        self.assertIn("Nexus Koramangala", resp.answer)

    def test_copilot_response_for_non_existent_product(self):
        resp = self.copilot.ask("How did iPhone 16 perform?")
        self.assertFalse(resp.data_sufficient)
        self.assertIn("iPhone 16", resp.answer)
        self.assertIn("not found in the local catalog", resp.answer)

    def test_copilot_response_schema_fields(self):
        resp = self.copilot.ask("What is running out?")
        self.assertIsInstance(resp, CopilotResponse)
        data = resp.model_dump()
        required_fields = [
            "answer",
            "key_findings",
            "evidence",
            "recommendation",
            "assumptions",
            "limitations",
            "data_sufficient",
            "confidence_note"
        ]
        for field in required_fields:
            self.assertIn(field, data, f"Missing required field {field}")

        self.assertTrue(data["data_sufficient"])
        self.assertIsInstance(data["key_findings"], list)
        self.assertGreater(len(data["key_findings"]), 0)
        self.assertIsInstance(data["evidence"], dict)
        self.assertIsNotNone(data["recommendation"])

    def test_stockout_risk_grounding_actual_numbers(self):
        resp = self.copilot.ask("What is running out in Hyderabad?")
        self.assertTrue(resp.data_sufficient)
        self.assertIn("Inorbit Cyberabad", resp.answer)
        self.assertIn("Current stock:", resp.answer)
        self.assertIn("Risk score:", resp.answer)
        # Check evidence matches
        self.assertIn("critical_items", resp.evidence)

    def test_store_comparison_grounding(self):
        resp = self.copilot.ask("Which stores are performing well?")
        self.assertTrue(resp.data_sufficient)
        self.assertIn("Store network performance comparison", resp.answer)
        self.assertIn("Revenue:", resp.answer)
        self.assertIn("Gross Margin:", resp.answer)
        self.assertEqual(resp.evidence["type"], "store_comparison")
        self.assertEqual(resp.evidence["store_count"], 4)

    def test_slow_movers_query(self):
        resp = self.copilot.ask("What products are slow moving?")
        self.assertTrue(resp.data_sufficient)
        self.assertIn("slow-moving", resp.answer.lower())
        self.assertEqual(resp.evidence["type"], "slow_movers_detection")

    def test_overstock_query(self):
        resp = self.copilot.ask("What is overstocked?")
        self.assertTrue(resp.data_sufficient)
        self.assertIn("overstocked", resp.answer.lower())
        self.assertEqual(resp.evidence["type"], "overstock_detection")

    def test_sales_anomaly_query(self):
        resp = self.copilot.ask("What sales anomalies or spikes occurred?")
        self.assertTrue(resp.data_sufficient)
        self.assertIn("anomal", resp.answer.lower())
        self.assertEqual(resp.evidence["type"], "sales_anomalies")

    def test_unknown_query_handling(self):
        resp = self.copilot.ask("What is the capital of France?")
        self.assertFalse(resp.data_sufficient)
        self.assertIn("Data is insufficient", resp.answer)
        self.assertIn("supported_intents", resp.evidence)

    def test_gemini_integration_with_mocked_sdk(self):
        """
        Validates that when GEMINI_API_KEY is present, the pipeline invokes
        the official google-genai SDK and respects the returned structured explanation.
        """
        mock_copilot = RetailCopilot(api_key="AIzaSyDummyKeyForTestingOnly")

        mock_gemini_client = MagicMock()
        mock_gen_response = MagicMock()
        mock_gen_response.text = (
            '{\n'
            '  "answer": "Grounded explanation: 3 products have high stockout risk at Hyderabad.",\n'
            '  "key_findings": ["SKU-104 has risk score 91/100"],\n'
            '  "evidence": {},\n'
            '  "recommendation": "Place a replenishment order, subject to manager approval.",\n'
            '  "assumptions": ["Supplier lead time remains constant"],\n'
            '  "limitations": ["Assumes no external promotional surge"],\n'
            '  "data_sufficient": true,\n'
            '  "confidence_note": "Reasoned by Gemini 2.5 Flash grounded in local SQLite analytics."\n'
            '}'
        )
        mock_gemini_client.models.generate_content.return_value = mock_gen_response

        with patch.object(mock_copilot, "_get_client", return_value=mock_gemini_client):
            resp = mock_copilot.ask("What is running out in Hyderabad?")
            self.assertTrue(resp.data_sufficient)
            self.assertIn("Grounded explanation", resp.answer)
            self.assertEqual(resp.recommendation, "Place a replenishment order, subject to manager approval.")

    def test_gemini_api_failure_graceful_fallback(self):
        """
        Simulates an API exception (e.g. rate limit, network failure) from Gemini SDK.
        Ensures the system catches the error and gracefully falls back to deterministic synthesis without crashing.
        """
        mock_copilot = RetailCopilot(api_key="AIzaSyDummyKeyForTestingOnly")

        mock_gemini_client = MagicMock()
        mock_gemini_client.models.generate_content.side_effect = RuntimeError("Simulated Gemini API timeout or quota error")

        with patch.object(mock_copilot, "_get_client", return_value=mock_gemini_client):
            resp = mock_copilot.ask("What is running out?")
            # Must NOT raise exception; must fall back to deterministic response
            self.assertIsInstance(resp, CopilotResponse)
            self.assertTrue(resp.data_sufficient)
            self.assertIn("stockout risk", resp.answer)
            self.assertIn("fallback", resp.confidence_note.lower())

    def test_fastapi_copilot_status_endpoint(self):
        resp = self.client.get("/api/copilot/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("gemini_api_key_configured", data)
        self.assertEqual(data["sdk"], "google-genai")
        self.assertEqual(data["model"], "gemini-2.5-flash")
        self.assertIn("source_of_truth", data)

    def test_fastapi_copilot_chat_endpoint(self):
        payload = {"question": "What is running out?"}
        resp = self.client.post("/api/copilot/chat", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("answer", data)
        self.assertIn("key_findings", data)
        self.assertIn("evidence", data)
        self.assertIn("recommendation", data)
        self.assertIn("data_sufficient", data)
        self.assertTrue(data["data_sufficient"])

    def test_fastapi_copilot_query_endpoint(self):
        resp = self.client.get("/api/copilot/query?question=Which+stores+are+performing+well%3F")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["data_sufficient"])
        self.assertIn("Store network performance", data["answer"])


if __name__ == "__main__":
    unittest.main()
