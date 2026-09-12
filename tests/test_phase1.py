"""
Phase 1 Verification Tests: Models, Data Loader & Dated Currency Normalization.
"""

import os
import unittest
from datetime import date, datetime
from decimal import Decimal

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "code")))

from models import (
    UserProfile,
    FinancialEvent,
    PaymentOption,
    Request,
    Message,
    ImageMapping,
    ExchangeRate,
)
from currency_normalizer import ExchangeRateTable, MissingExchangeRateError
from data_loader import DataLoader, DatasetContainer


class TestPhase1(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "dataset"))
        cls.loader = DataLoader(cls.dataset_dir)
        cls.container = cls.loader.load_all()

    def test_a_all_csv_files_load_successfully(self):
        self.assertEqual(len(self.container.requests), 250)
        self.assertEqual(len(self.container.sample_requests), 25)
        self.assertEqual(len(self.container.profiles), 275)
        self.assertEqual(len(self.container.events), 25342)
        self.assertEqual(len(self.container.payment_options), 790)
        self.assertEqual(len(self.container.exchange_rates), 134)
        self.assertEqual(len(self.container.messages), 215)
        self.assertEqual(len(self.container.images), 16)

    def test_b_dates_parsed_consistently(self):
        req = self.container.requests[0]
        self.assertIsInstance(req.request_date, date)
        self.assertIsInstance(req.desired_completion_date, date)

        ev = self.container.events[0]
        self.assertIsInstance(ev.event_date, date)
        if ev.settlement_date:
            self.assertIsInstance(ev.settlement_date, date)

        msg = self.container.messages[0]
        self.assertIsInstance(msg.sent_at, datetime)

    def test_c_decimal_precision_preserved(self):
        # Sample with float precision like 15952906.67
        sample_req = self.container.sample_requests_by_id.get("request_02")
        self.assertIsNotNone(sample_req)
        self.assertIsInstance(sample_req.ground_truth_amount_safe_to_pay, Decimal)
        self.assertEqual(sample_req.ground_truth_amount_safe_to_pay, Decimal("17229139.2"))

        # Profile available balance
        prof = self.container.profiles_by_user_id.get("user_01")
        self.assertEqual(prof.current_available_balance, Decimal("58481.1"))
        self.assertEqual(prof.minimum_balance_to_keep, Decimal("18000"))

    def test_d_blank_event_amounts_remain_none(self):
        blank_amount_events = [e for e in self.container.events if e.amount is None]
        self.assertEqual(len(blank_amount_events), 16)

        for e in blank_amount_events:
            self.assertIsNone(e.amount, f"Event {e.event_id} amount should be None, not 0")
            # Verify each blank amount event is mapped in images.csv
            self.assertIn(e.event_id, self.container.images_by_event_id)

    def test_e_event_relationships_preserved(self):
        linked_events = [e for e in self.container.events if e.linked_event_id is not None]
        self.assertEqual(len(linked_events), 58)

        for le in linked_events:
            # Linked event should point to a valid earlier event ID
            self.assertIn(le.linked_event_id, self.container.events_by_id)

    def test_f_request_payment_options_relationship(self):
        for req in self.container.requests:
            opts = self.container.payment_options_by_request_id.get(req.request_id, [])
            self.assertGreaterEqual(len(opts), 2, f"Request {req.request_id} should have >= 2 payment options")

    def test_g_messages_and_images_relationships(self):
        # 16 images all linked to valid user, request, and related_event
        for img in self.container.images:
            self.assertIn(img.user_id, self.container.profiles_by_user_id)
            self.assertIn(img.related_event_id, self.container.events_by_id)

        # Messages with related_event_id point to valid events
        for msg in self.container.messages:
            if msg.related_event_id:
                self.assertIn(msg.related_event_id, self.container.events_by_id)

    def test_h_image_paths_resolve_correctly(self):
        for img in self.container.images:
            self.assertTrue(os.path.exists(img.file_path), f"Image file not found: {img.file_path}")
            self.assertTrue(img.file_path.endswith(".png"))

    def test_i_same_currency_conversion_returns_original_amount(self):
        amount = Decimal("12345.67")
        converted = self.container.rate_table.convert(
            amount=amount,
            from_currency="USD",
            to_currency="USD",
            rate_date=date(2025, 1, 15)
        )
        self.assertEqual(converted, amount)

    def test_j_exact_dated_fx_conversion(self):
        # 2023-10-15, USD->IDR rate is 15833.33
        rate = self.container.rate_table.get_rate(date(2023, 10, 15), "USD", "IDR")
        self.assertEqual(rate, Decimal("15833.33"))

        converted = self.container.rate_table.convert(
            amount=Decimal("1800"),
            from_currency="USD",
            to_currency="IDR",
            rate_date=date(2023, 10, 15)
        )
        self.assertEqual(converted, Decimal("1800") * Decimal("15833.33"))

        # 2025-11-15, USD->INR rate is 83.33
        converted_inr = self.container.rate_table.convert(
            amount=Decimal("100"),
            from_currency="USD",
            to_currency="INR",
            rate_date=date(2025, 11, 15)
        )
        self.assertEqual(converted_inr, Decimal("8333.00"))

    def test_k_missing_fx_rate_raises_explicit_error(self):
        with self.assertRaises(MissingExchangeRateError):
            self.container.rate_table.convert(
                amount=Decimal("100"),
                from_currency="USD",
                to_currency="XYZ",  # Unsupported currency
                rate_date=date(2025, 1, 15)
            )

        with self.assertRaises(MissingExchangeRateError):
            self.container.rate_table.convert(
                amount=Decimal("100"),
                from_currency="USD",
                to_currency="INR",
                rate_date=date(1990, 1, 1)  # Unsupported date
            )

    def test_l_all_foreign_currency_events_convertible(self):
        # Confirm that all 140 foreign currency events in dataset can be converted on their settlement_date
        foreign_events = [
            e for e in self.container.events
            if e.currency != self.container.profiles_by_user_id[e.user_id].home_currency
        ]
        self.assertEqual(len(foreign_events), 140)

        for fe in foreign_events:
            home_curr = self.container.profiles_by_user_id[fe.user_id].home_currency
            settle_date = fe.settlement_date or fe.event_date
            rate = self.container.rate_table.get_rate(settle_date, fe.currency, home_curr)
            self.assertIsNotNone(rate, f"Missing rate for {fe.currency}->{home_curr} on {settle_date}")
            if fe.amount is not None:
                converted = self.container.rate_table.convert(
                    amount=fe.amount,
                    from_currency=fe.currency,
                    to_currency=home_curr,
                    rate_date=settle_date
                )
                self.assertIsInstance(converted, Decimal)
                self.assertGreater(converted, Decimal(0))


if __name__ == "__main__":
    unittest.main()
