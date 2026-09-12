"""
Phase 2 Verification Tests: Gemini Extractor, Provenance & Conflict Resolution.
"""

import os
import unittest
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "code")))

from models import (
    FinancialEvent,
    ExtractedFact,
    Message,
    ImageMapping,
    UserProfile,
    ReconciledEvent,
)
from gemini_extractor import GeminiExtractor
from conflict_resolver import ConflictResolver
from data_loader import DataLoader


class TestPhase2(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "dataset"))
        cls.loader = DataLoader(cls.dataset_dir)
        cls.container = cls.loader.load_all()

    def test_1_valid_structured_message_extraction_schema(self):
        extractor = GeminiExtractor(api_key="mock_key")
        extractor.client = MagicMock()
        
        mock_response = MagicMock()
        mock_response.text = '''[
          {
            "event_id": "event_100",
            "operation": "amend",
            "field": "amount",
            "value": "42750000",
            "currency": "IDR",
            "effective_date": "2025-08-15",
            "source": "message_01"
          }
        ]'''
        mock_response.usage_metadata.prompt_token_count = 150
        mock_response.usage_metadata.candidates_token_count = 50
        extractor.client.models.generate_content.return_value = mock_response

        msg = Message(
            message_id="message_01",
            user_id="user_02",
            request_id="request_02",
            related_event_id="event_100",
            sent_at=datetime(2025, 7, 29, 9, 30),
            source_type="employer",
            message_text="Monthly salary increased to IDR 42,750,000 effective 2025-08-15."
        )

        facts = extractor.extract_from_message(msg)
        self.assertEqual(len(facts), 1)
        f = facts[0]
        self.assertEqual(f.event_id, "event_100")
        self.assertEqual(f.operation, "amend")
        self.assertEqual(f.field, "amount")
        self.assertEqual(f.value, "42750000")
        self.assertEqual(f.currency, "IDR")
        self.assertEqual(f.effective_date, "2025-08-15")
        self.assertEqual(f.source, "message_01")

    def test_2_invalid_gemini_response_rejected_safely(self):
        extractor = GeminiExtractor(api_key="mock_key")
        extractor.client = MagicMock()
        
        # Malformed / invalid json
        mock_response = MagicMock()
        mock_response.text = "This is not JSON at all."
        extractor.client.models.generate_content.return_value = mock_response

        msg = self.container.messages[0]
        facts = extractor.extract_from_message(msg)
        self.assertEqual(facts, [])

    def test_3_api_failure_does_not_crash_pipeline(self):
        extractor = GeminiExtractor(api_key="mock_key")
        extractor.client = MagicMock()
        extractor.client.models.generate_content.side_effect = RuntimeError("API connection timeout")

        msg = self.container.messages[0]
        facts = extractor.extract_from_message(msg)
        self.assertEqual(facts, [])

    def test_4_failed_extraction_does_not_replace_original_csv_data(self):
        raw_event = self.container.events_by_id["event_01"]
        resolver = ConflictResolver()
        
        # Empty facts due to failed extraction
        reconciled = resolver.reconcile_events([raw_event], facts=[])
        self.assertEqual(len(reconciled), 1)
        r = reconciled[0]
        self.assertEqual(r.event.amount, raw_event.amount)
        self.assertEqual(r.event.status, raw_event.status)
        self.assertFalse(r.is_amended)

    def test_5_image_mapping_correctly_identifies_associated_event(self):
        extractor = GeminiExtractor(api_key="mock_key")
        extractor.client = MagicMock()
        
        mock_response = MagicMock()
        mock_response.text = '''[
          {
            "event_id": "event_253",
            "operation": "amend",
            "field": "amount",
            "value": "1425000",
            "currency": "IDR",
            "effective_date": "2019-08-31",
            "source": "image_01"
          }
        ]'''
        extractor.client.models.generate_content.return_value = mock_response

        img_mapping = self.container.images[0]  # image_01 -> event_253
        rel_event = self.container.events_by_id.get(img_mapping.related_event_id)

        facts = extractor.extract_from_image(img_mapping, related_event=rel_event)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].event_id, img_mapping.related_event_id)
        self.assertEqual(facts[0].value, "1425000")
        self.assertEqual(facts[0].source, "image_01")

    def test_6_blank_image_backed_amount_remains_unresolved_on_failure(self):
        # Event with blank amount
        raw_event = self.container.events_by_id["event_253"]
        self.assertIsNone(raw_event.amount)

        resolver = ConflictResolver()
        reconciled = resolver.reconcile_events([raw_event], facts=[])
        self.assertIsNone(reconciled[0].event.amount)

    def test_7_provenance_fields_preserved_exactly(self):
        raw_event = self.container.events_by_id["event_01"]
        fact = ExtractedFact(
            event_id="event_01",
            operation="amend",
            field="amount",
            value="6000",
            currency="ZAR",
            effective_date="2023-11-01",
            source="message_99"
        )
        resolver = ConflictResolver()
        reconciled = resolver.reconcile_events([raw_event], facts=[fact])
        self.assertEqual(len(reconciled[0].provenance), 1)
        self.assertEqual(reconciled[0].provenance[0], fact)

    def test_8_explicit_related_event_id_conflict_detected(self):
        raw_event = self.container.events_by_id["event_01"]
        fact = ExtractedFact(
            event_id="event_01",
            operation="cancel",
            field="status",
            value="cancelled",
            currency="ZAR",
            effective_date="2023-10-02",
            source="message_cancel"
        )
        resolver = ConflictResolver()
        reconciled = resolver.reconcile_events([raw_event], facts=[fact])
        self.assertTrue(reconciled[0].is_cancelled)
        self.assertEqual(reconciled[0].event.status, "cancelled")

    def test_9_missing_related_event_id_conflict_detected_via_category_date(self):
        # Create unlinked message fact for user_01 salary
        salary_event = FinancialEvent(
            event_id="event_sal_01",
            user_id="user_test",
            event_type="income",
            description="Monthly salary",
            category="salary",
            direction="credit",
            amount=Decimal("50000"),
            currency="INR",
            event_date=date(2025, 8, 15),
            settlement_date=date(2025, 8, 15),
            status="scheduled"
        )
        unlinked_msg = Message(
            message_id="msg_unlinked_01",
            user_id="user_test",
            request_id=None,
            related_event_id=None,
            sent_at=datetime(2025, 8, 1, 10, 0),
            source_type="employer",
            message_text="Your monthly salary is revised to INR 55,000 effective 2025-08-15."
        )
        fact = ExtractedFact(
            event_id="",  # unlinked!
            operation="amend",
            field="amount",
            value="55000",
            currency="INR",
            effective_date="2025-08-15",
            source="msg_unlinked_01"
        )

        resolver = ConflictResolver()
        reconciled = resolver.reconcile_events(
            events=[salary_event],
            facts=[fact],
            messages=[unlinked_msg]
        )
        self.assertEqual(len(reconciled), 1)
        self.assertEqual(reconciled[0].event.amount, Decimal("55000"))
        self.assertTrue(reconciled[0].is_amended)

    def test_10_conflict_resolution_is_deterministic(self):
        event = FinancialEvent(
            event_id="event_test_10",
            user_id="user_test",
            event_type="expense",
            description="Gym subscription",
            category="gym",
            direction="debit",
            amount=Decimal("100"),
            currency="USD",
            event_date=date(2025, 1, 1),
            settlement_date=date(2025, 1, 1),
            status="settled"
        )
        msg1 = Message("msg_1", "user_test", None, "event_test_10", datetime(2025, 1, 2, 10, 0), "gym", "price 110")
        msg2 = Message("msg_2", "user_test", None, "event_test_10", datetime(2025, 1, 3, 10, 0), "gym", "price 120")

        f1 = ExtractedFact("event_test_10", "amend", "amount", "110", "USD", "2025-01-02", "msg_1")
        f2 = ExtractedFact("event_test_10", "amend", "amount", "120", "USD", "2025-01-03", "msg_2")

        resolver = ConflictResolver()
        # Run multiple times with different permutations
        res1 = resolver.reconcile_events([event], [f1, f2], [msg1, msg2])
        res2 = resolver.reconcile_events([event], [f2, f1], [msg1, msg2])

        # Latest message msg_2 (Tier 2) must win deterministically
        self.assertEqual(res1[0].event.amount, Decimal("120"))
        self.assertEqual(res2[0].event.amount, Decimal("120"))

    def test_11_original_source_data_remains_available(self):
        event = FinancialEvent(
            event_id="event_test_11",
            user_id="user_test",
            event_type="expense",
            description="Internet bill",
            category="utilities",
            direction="debit",
            amount=Decimal("50"),
            currency="EUR",
            event_date=date(2025, 2, 1),
            settlement_date=date(2025, 2, 1),
            status="scheduled"
        )
        fact = ExtractedFact("event_test_11", "amend", "amount", "65", "EUR", "2025-02-01", "msg_bill")
        resolver = ConflictResolver()
        reconciled = resolver.reconcile_events([event], [fact])

        # Active event is amended
        self.assertEqual(reconciled[0].event.amount, Decimal("65"))
        # Original event remains untouched
        self.assertEqual(reconciled[0].original_event.amount, Decimal("50"))

    def test_12_no_financial_arithmetic_performed_in_extraction_layer(self):
        # Verification that fact values remain raw strings until parsed and no balance calculation is done
        fact = ExtractedFact("ev_1", "amend", "amount", "100.50", "USD", "2025-01-01", "src")
        self.assertIsInstance(fact.value, str)


if __name__ == "__main__":
    unittest.main()
