"""
Regression Tests for Gemini Telemetry Provenance (Issue #3).
Proves that every Gemini telemetry record unambiguously identifies its execution context
(production, evaluation, smoke_test), preserves all schema fields, safely handles telemetry
failures, redacts sensitive keys, and executes without real API calls.
"""

import os
import json
import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "code")))

from instrumentation import (
    GeminiLogger,
    resolve_run_type,
    VALID_RUN_TYPES,
    DEFAULT_RUN_TYPE,
)
from gemini_extractor import GeminiExtractor
from gemini_explainer import GeminiExplainer
from models import Message, Request, UserProfile, DecisionTrace, PlanCandidate


class TestTelemetryProvenance(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.log_path = os.path.join(self.temp_dir.name, "test_gemini_calls.json")
        self.logger = GeminiLogger(log_path=self.log_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_mock_response(self, text: str, prompt_tokens: int = 100, candidate_tokens: int = 30):
        resp = MagicMock()
        resp.text = text
        resp.usage_metadata.prompt_token_count = prompt_tokens
        resp.usage_metadata.candidates_token_count = candidate_tokens
        return resp

    def test_1_production_gemini_calls_recorded_with_run_type_production(self):
        """1. Production Gemini calls are recorded with run_type='production'."""
        extractor = GeminiExtractor(
            api_key="mock_key",
            logger=self.logger,
            run_type="production"
        )
        extractor.client = MagicMock()
        extractor.client.models.generate_content.return_value = self._create_mock_response(
            '[{"event_id":"e1","operation":"confirm","field":"status","value":"settled","currency":"USD","effective_date":"2025-01-01","source":"m1"}]'
        )

        msg = Message("m1", "u1", "r1", "e1", datetime(2025, 1, 1), "bank", "Salary settled")
        facts = extractor.extract_from_message(msg)
        self.assertEqual(len(facts), 1)

        with open(self.log_path, "r", encoding="utf-8") as f:
            records = json.load(f)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["run_type"], "production")
        self.assertEqual(records[0]["purpose"], "message_fact_extraction")

        # Explainer production call
        explainer = GeminiExplainer(
            api_key="mock_key",
            logger=self.logger,
            run_type="production"
        )
        explainer.client = MagicMock()
        explainer.client.models.generate_content.return_value = self._create_mock_response(
            "Pay USD 500 today to keep balance safe."
        )

        req = Request("r1", "u1", date(2025, 1, 1), "purchase", Decimal("500"), date(2025, 1, 20), False, "buy")
        prof = UserProfile("u1", "USD", Decimal("1000"), Decimal("200"), [], [], [], [], ["full_payment"])
        trace = DecisionTrace("r1", Decimal("500"), "affordable_now", "full_payment", "2025-01-01:500", date(2025, 1, 1), "none")

        explanation = explainer.generate_explanation(req, prof, trace)
        self.assertIn("USD 500", explanation)

        with open(self.log_path, "r", encoding="utf-8") as f:
            records = json.load(f)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[1]["run_type"], "production")
        self.assertEqual(records[1]["purpose"], "explanation_generation")

    def test_2_evaluation_calls_recorded_with_run_type_evaluation(self):
        """2. Evaluation calls are recorded with run_type='evaluation'."""
        extractor = GeminiExtractor(
            api_key="mock_key",
            logger=self.logger,
            run_type="evaluation"
        )
        extractor.client = MagicMock()
        extractor.client.models.generate_content.return_value = self._create_mock_response("[]")

        msg = Message("m2", "u2", "r2", None, datetime(2025, 1, 1), "user", "No change")
        extractor.extract_from_message(msg)

        with open(self.log_path, "r", encoding="utf-8") as f:
            records = json.load(f)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["run_type"], "evaluation")

        # Test resolution via environment variable
        with patch.dict(os.environ, {"GEMINI_RUN_TYPE": "evaluation"}):
            self.assertEqual(resolve_run_type(), "evaluation")
            extractor_env = GeminiExtractor(api_key="mock_key", logger=self.logger)
            self.assertEqual(extractor_env.run_type, "evaluation")

    def test_3_smoke_test_calls_recorded_with_run_type_smoke_test(self):
        """3. Smoke-test calls, if present, are recorded with run_type='smoke_test'."""
        extractor = GeminiExtractor(
            api_key="mock_key",
            logger=self.logger,
            run_type="smoke_test"
        )
        extractor.client = MagicMock()
        extractor.client.models.generate_content.return_value = self._create_mock_response("[]")

        msg = Message("m3", "u3", "smoke_1", None, datetime(2025, 1, 1), "test", "Ping")
        extractor.extract_from_message(msg)

        # Direct logger call with smoke_test
        self.logger.log_call(
            purpose="api_health_check",
            model_name="gemini-3.6-flash",
            request_id="smoke_check_01",
            run_type="smoke_test",
            success=True
        )

        with open(self.log_path, "r", encoding="utf-8") as f:
            records = json.load(f)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["run_type"], "smoke_test")
        self.assertEqual(records[1]["run_type"], "smoke_test")
        self.assertEqual(records[1]["purpose"], "api_health_check")

    def test_4_existing_telemetry_fields_remain_intact(self):
        """4. Existing telemetry fields remain intact alongside run_type."""
        record = self.logger.log_call(
            purpose="message_fact_extraction",
            model_name="gemini-3.6-flash",
            provider="Google Gemini",
            request_id="req_99",
            input_tokens=450,
            output_tokens=120,
            success=True,
            duration_ms=123.45,
            run_type="production"
        )

        expected_fields = [
            "timestamp",
            "request_id",
            "purpose",
            "run_type",
            "model_name",
            "provider",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "estimated_cost_usd",
            "success",
            "duration_ms",
            "error"
        ]
        for f in expected_fields:
            self.assertIn(f, record, f"Missing expected field: {f}")

        self.assertEqual(record["total_tokens"], 570)
        self.assertGreater(record["estimated_cost_usd"], 0)
        self.assertIsNone(record["error"])
        self.assertEqual(record["run_type"], "production")

    def test_5_failed_gemini_calls_still_recorded_correctly(self):
        """5. Failed Gemini calls are still recorded correctly with run_type."""
        extractor = GeminiExtractor(
            api_key="mock_key",
            logger=self.logger,
            run_type="production"
        )
        extractor.client = MagicMock()
        extractor.client.models.generate_content.side_effect = RuntimeError("503 Service Unavailable")

        msg = Message("m5", "u5", "r5", None, datetime(2025, 1, 1), "user", "Hello")
        facts = extractor.extract_from_message(msg)
        self.assertEqual(facts, [])

        with open(self.log_path, "r", encoding="utf-8") as f:
            records = json.load(f)
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertFalse(rec["success"])
        self.assertEqual(rec["run_type"], "production")
        self.assertIn("503 Service Unavailable", rec["error"])

    def test_6_telemetry_failure_cannot_break_gemini_caller(self):
        """6. Telemetry failure cannot break the Gemini caller."""
        # Create a broken logger that always throws an unhandled exception on log_call
        broken_logger = MagicMock()
        broken_logger.log_call.side_effect = IOError("Disk write error: read-only filesystem")

        extractor = GeminiExtractor(
            api_key="mock_key",
            logger=broken_logger,
            run_type="production"
        )
        extractor.client = MagicMock()
        extractor.client.models.generate_content.return_value = self._create_mock_response(
            '[{"event_id":"e10","operation":"cancel","field":"status","value":"cancelled","currency":"EUR","effective_date":"2025-02-01","source":"m10"}]'
        )

        msg = Message("m10", "u10", "r10", "e10", datetime(2025, 1, 1), "bank", "Cancel sub")
        # Should not raise any exception despite broken logger
        facts = extractor.extract_from_message(msg)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].operation, "cancel")

        # Similarly test explainer with broken logger
        explainer = GeminiExplainer(
            api_key="mock_key",
            logger=broken_logger,
            run_type="production"
        )
        explainer.client = MagicMock()
        explainer.client.models.generate_content.return_value = self._create_mock_response(
            "Pay EUR 100 today."
        )

        req = Request("r10", "u10", date(2025, 1, 1), "purchase", Decimal("100"), date(2025, 1, 20), False, "buy")
        prof = UserProfile("u10", "EUR", Decimal("1000"), Decimal("200"), [], [], [], [], ["full_payment"])
        trace = DecisionTrace("r10", Decimal("100"), "affordable_now", "full_payment", "2025-01-01:100", date(2025, 1, 1), "none")

        explanation = explainer.generate_explanation(req, prof, trace)
        self.assertIn("EUR 100", explanation)

    def test_7_api_keys_are_never_written_to_telemetry(self):
        """7. API keys are never written to telemetry."""
        fake_secret_key = "AIzaSyFakeSecretKeyForTesting12345"
        error_with_key = f"400 Request failed for key={fake_secret_key}: Invalid key api_key={fake_secret_key}"

        rec = self.logger.log_call(
            purpose="test_error_logging",
            model_name="gemini-3.6-flash",
            success=False,
            error_msg=error_with_key,
            run_type="smoke_test"
        )

        self.assertNotIn(fake_secret_key, rec["error"])
        self.assertIn("[REDACTED_API_KEY]", rec["error"])

        # Check raw disk log file as well
        with open(self.log_path, "r", encoding="utf-8") as f:
            raw_content = f.read()
        self.assertNotIn(fake_secret_key, raw_content)

    def test_8_no_real_gemini_api_calls_required(self):
        """8. No real Gemini API calls are required by the tests."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GEMINI_API_KEY", None)
            extractor = GeminiExtractor()
            explainer = GeminiExplainer()

            # Client remains None without API key -> zero network I/O
            self.assertIsNone(extractor.client)
            self.assertIsNone(explainer.client)

            # Execution gracefully returns empty/fallback without network error
            msg = Message("m_offline", "u1", "r1", None, datetime(2025, 1, 1), "user", "offline")
            facts = extractor.extract_from_message(msg)
            self.assertEqual(facts, [])


if __name__ == "__main__":
    unittest.main()
