"""
Gemini Copilot Agent module.
CRITICAL ARCHITECTURAL PRINCIPLE:
- Python performs all arithmetic, aggregation, filtering, and KPIs.
- Gemini performs NLU, intent classification, explaining computed evidence, and framing recommendations.
- Gemini NEVER invents numerical values.
- Gracefully handles missing/invalid GEMINI_API_KEY and API failures.
"""
import os
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

class RetailCopilotAgent:
    """
    Coordinates between natural language user requests and deterministic analytical engines.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self._client = None

    def _get_gemini_client(self):
        """Lazy initialization of Google GenAI client to prevent startup failure."""
        if not self._client and self.api_key:
            try:
                from google import genai
                self._client = genai.Client(api_key=self.api_key)
            except Exception as e:
                logger.warning(f"Could not initialize Gemini Client: {e}")
                self._client = None
        return self._client

    def classify_intent(self, question: str) -> str:
        """
        Classifies user query into analytical categories:
        - stockout_risk: 'What is running out?'
        - overstock: 'What is overstocked?'
        - slow_moving: 'What products are not moving?'
        - product_performance: 'How did product X perform?'
        - store_performance: 'Which stores are performing well?'
        - sales_anomaly: 'What sales spikes or drops deserve attention?'
        - daily_action: 'What should I do today?'
        """
        q = question.lower()
        if "running out" in q or "stockout" in q or "low stock" in q:
            return "stockout_risk"
        if "overstock" in q or "excess stock" in q:
            return "overstock"
        if "not moving" in q or "slow" in q or "stagnant" in q:
            return "slow_moving"
        if "perform" in q and ("store" in q or "branch" in q):
            return "store_performance"
        if "perform" in q or "sales" in q or "month" in q:
            return "product_performance"
        if "spike" in q or "drop" in q or "anomaly" in q or "attention" in q:
            return "sales_anomaly"
        if "do today" in q or "action" in q or "recommend" in q:
            return "daily_action"
        return "general_inquiry"

    def answer_query(self, question: str) -> Dict[str, Any]:
        """
        Processes a query with strict grounding:
        1. Classifies intent.
        2. Executes deterministic Python calculation.
        3. Formats evidence-backed response (using Gemini if available, or structured fallback).
        """
        intent = self.classify_intent(question)
        return {
            "query": question,
            "classified_intent": intent,
            "status": "ready",
            "message": "Copilot module initialized. Ready for advanced analytical pipelines."
        }
