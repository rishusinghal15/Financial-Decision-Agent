"""
Phase 4 Unit & Integration Tests: Grounded Explanations, Output Validation, and Pipeline Integration.
"""

import os
import unittest
from datetime import date
from decimal import Decimal

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "code")))

from models import Request, UserProfile, DecisionTrace, PlanCandidate, PaymentOption
from gemini_explainer import GeminiExplainer
from validator import OutputValidator, ValidationError
from data_loader import DataLoader


class TestPhase4(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.explainer = GeminiExplainer(api_key=None)  # Tests deterministic fallback
        cls.validator = OutputValidator()
        cls.dataset_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "dataset"))
        cls.loader = DataLoader(cls.dataset_dir)
        cls.container = cls.loader.load_all()

    def test_1_explainer_fallback_affordable_now(self):
        req = Request("r1", "u1", date(2025, 1, 1), "purchase", Decimal("500"), date(2025, 1, 20), False, "buy")
        prof = UserProfile("u1", "USD", Decimal("1000"), Decimal("200"), [], [], [], [], ["full_payment"])
        trace = DecisionTrace(
            request_id="r1",
            amount_safe_to_pay=Decimal("500"),
            affordability_status="affordable_now",
            recommended_payment_method="full_payment",
            payment_plan="2025-01-01:500",
            earliest_date_for_full_payment=date(2025, 1, 1),
            spending_changes_needed="none"
        )
        cand = PlanCandidate("full_payment", "2025-01-01:500", [(date(2025, 1, 1), Decimal("500"))], Decimal("500"), [], True, date(2025, 1, 1), True)
        exp = self.explainer.generate_fallback_explanation(req, prof, trace, cand)
        self.assertIn("Pay USD 500 today", exp)
        self.assertIn("USD 200", exp)

    def test_2_explainer_fallback_installments(self):
        req = Request("r2", "u2", date(2025, 1, 1), "travel", Decimal("900"), date(2025, 4, 1), False, "trip")
        prof = UserProfile("u2", "EUR", Decimal("1000"), Decimal("300"), [], [], [], [], ["installments"])
        trace = DecisionTrace(
            request_id="r2",
            amount_safe_to_pay=Decimal("300"),
            affordability_status="affordable_with_plan",
            recommended_payment_method="installments",
            payment_plan="2025-01-01:300|2025-02-01:300|2025-03-01:300",
            earliest_date_for_full_payment=date(2025, 3, 1),
            spending_changes_needed="none"
        )
        cand = PlanCandidate("installments", trace.payment_plan, [
            (date(2025, 1, 1), Decimal("300")),
            (date(2025, 2, 1), Decimal("300")),
            (date(2025, 3, 1), Decimal("300"))
        ], Decimal("900"), [], True, date(2025, 3, 1), True)
        exp = self.explainer.generate_fallback_explanation(req, prof, trace, cand)
        self.assertIn("Use 3 installments of EUR 300", exp)

    def test_3_explainer_fallback_wait(self):
        req = Request("r3", "u3", date(2025, 1, 1), "education", Decimal("1000"), date(2025, 2, 1), False, "course")
        prof = UserProfile("u3", "ZAR", Decimal("500"), Decimal("200"), [], [], [], [], ["full_payment"])
        trace = DecisionTrace(
            request_id="r3",
            amount_safe_to_pay=Decimal("100"),
            affordability_status="affordable_later",
            recommended_payment_method="wait",
            payment_plan="2025-01-15:1000",
            earliest_date_for_full_payment=date(2025, 1, 15),
            spending_changes_needed="none"
        )
        exp = self.explainer.generate_fallback_explanation(req, prof, trace, None)
        self.assertIn("Pay ZAR 1,000 in full on 2025-01-15", exp)

    def test_4_explainer_fallback_not_affordable(self):
        req = Request("r4", "u4", date(2025, 1, 1), "purchase", Decimal("5000"), date(2025, 1, 10), False, "buy")
        prof = UserProfile("u4", "INR", Decimal("500"), Decimal("300"), [], [], [], [], ["full_payment"])
        trace = DecisionTrace(
            request_id="r4",
            amount_safe_to_pay=Decimal("0"),
            affordability_status="not_affordable",
            recommended_payment_method="not_recommended",
            payment_plan="none",
            earliest_date_for_full_payment=None,
            spending_changes_needed="none"
        )
        exp = self.explainer.generate_fallback_explanation(req, prof, trace, None)
        self.assertIn("Do not make this payment by 2025-01-10", exp)

    def test_5_validator_valid_row_passes(self):
        req = Request("r_valid", "u1", date(2025, 1, 1), "purchase", Decimal("500"), date(2025, 1, 20), False, "buy")
        row = {
            "request_id": "r_valid",
            "amount_safe_to_pay": "500",
            "affordability_status": "affordable_now",
            "recommended_payment_method": "full_payment",
            "payment_plan": "2025-01-01:500",
            "earliest_date_for_full_payment": "2025-01-01",
            "spending_changes_needed": "none",
            "decision_explanation": "Pay USD 500 today. This leaves at least USD 200 available."
        }
        errors = self.validator.validate_row(row, req)
        self.assertEqual(len(errors), 0)

    def test_6_validator_rejects_negative_safe_amount(self):
        req = Request("r_neg", "u1", date(2025, 1, 1), "purchase", Decimal("500"), date(2025, 1, 20), False, "buy")
        row = {
            "request_id": "r_neg",
            "amount_safe_to_pay": "-50",
            "affordability_status": "affordable_now",
            "recommended_payment_method": "full_payment",
            "payment_plan": "2025-01-01:500",
            "earliest_date_for_full_payment": "2025-01-01",
            "spending_changes_needed": "none",
            "decision_explanation": "Test explanation."
        }
        errors = self.validator.validate_row(row, req)
        self.assertTrue(any("cannot be negative" in e for e in errors))

    def test_7_validator_rejects_exceeding_safe_amount(self):
        req = Request("r_exc", "u1", date(2025, 1, 1), "purchase", Decimal("500"), date(2025, 1, 20), False, "buy")
        row = {
            "request_id": "r_exc",
            "amount_safe_to_pay": "600",
            "affordability_status": "affordable_now",
            "recommended_payment_method": "full_payment",
            "payment_plan": "2025-01-01:500",
            "earliest_date_for_full_payment": "2025-01-01",
            "spending_changes_needed": "none",
            "decision_explanation": "Test explanation."
        }
        errors = self.validator.validate_row(row, req)
        self.assertTrue(any("exceeds requested_amount" in e for e in errors))

    def test_8_validator_rejects_status_method_mismatch(self):
        req = Request("r_mis", "u1", date(2025, 1, 1), "purchase", Decimal("500"), date(2025, 1, 20), False, "buy")
        row = {
            "request_id": "r_mis",
            "amount_safe_to_pay": "500",
            "affordability_status": "affordable_now",
            "recommended_payment_method": "wait",
            "payment_plan": "2025-01-01:500",
            "earliest_date_for_full_payment": "2025-01-01",
            "spending_changes_needed": "none",
            "decision_explanation": "Test explanation."
        }
        errors = self.validator.validate_row(row, req)
        self.assertTrue(any("must have recommended_payment_method='full_payment'" in e for e in errors))

    def test_9_validator_rejects_malformed_payment_plan(self):
        req = Request("r_plan", "u1", date(2025, 1, 1), "purchase", Decimal("500"), date(2025, 1, 20), False, "buy")
        row = {
            "request_id": "r_plan",
            "amount_safe_to_pay": "500",
            "affordability_status": "affordable_now",
            "recommended_payment_method": "full_payment",
            "payment_plan": "invalid_plan_syntax",
            "earliest_date_for_full_payment": "2025-01-01",
            "spending_changes_needed": "none",
            "decision_explanation": "Test explanation."
        }
        errors = self.validator.validate_row(row, req)
        self.assertTrue(len(errors) > 0)

    def test_10_validator_dataset_checks_duplicates_and_counts(self):
        req1 = Request("r1", "u1", date(2025, 1, 1), "purchase", Decimal("100"), date(2025, 1, 20), False, "buy")
        req2 = Request("r2", "u2", date(2025, 1, 1), "purchase", Decimal("200"), date(2025, 1, 20), False, "buy")
        
        # Duplicate r1
        rows = [
            {
                "request_id": "r1", "amount_safe_to_pay": "100", "affordability_status": "affordable_now",
                "recommended_payment_method": "full_payment", "payment_plan": "2025-01-01:100",
                "earliest_date_for_full_payment": "2025-01-01", "spending_changes_needed": "none",
                "decision_explanation": "Valid test explanation text."
            },
            {
                "request_id": "r1", "amount_safe_to_pay": "100", "affordability_status": "affordable_now",
                "recommended_payment_method": "full_payment", "payment_plan": "2025-01-01:100",
                "earliest_date_for_full_payment": "2025-01-01", "spending_changes_needed": "none",
                "decision_explanation": "Valid test explanation text."
            }
        ]
        errors = self.validator.validate_dataset(rows, [req1, req2])
        self.assertTrue(any("Duplicate" in e for e in errors))
        self.assertTrue(any("Missing" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
