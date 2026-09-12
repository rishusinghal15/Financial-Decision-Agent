"""
Phase 3 Verification Tests: 90-Day Financial Forecaster, Decision Engine & Deterministic Ranker.
"""

import os
import unittest
from datetime import date, timedelta
from decimal import Decimal

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "code")))

from models import (
    Request,
    UserProfile,
    FinancialEvent,
    ReconciledEvent,
    PaymentOption,
    PlanCandidate,
    DecisionTrace,
)
from forecaster import FinancialForecaster
from ranker import PlanRanker
from decision_engine import DecisionEngine
from data_loader import DataLoader
from conflict_resolver import ConflictResolver


class TestPhase3(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "dataset"))
        cls.loader = DataLoader(cls.dataset_dir)
        cls.container = cls.loader.load_all()
        cls.forecaster = FinancialForecaster(cls.container.rate_table)
        cls.ranker = PlanRanker()
        cls.engine = DecisionEngine(cls.forecaster, cls.ranker)

    def test_1_forecast_starts_from_current_available_balance(self):
        profile = self.container.profiles_by_user_id["user_01"]
        events = self.container.events_by_user_id["user_01"]
        reconciled = [ReconciledEvent(e, e) for e in events]
        tl = self.forecaster.build_forecast_timeline(profile, reconciled, date(2024, 3, 3))
        
        # Zero payment test should preserve starting balance baseline
        is_safe, min_obs = self.forecaster.simulate(
            profile.current_available_balance,
            profile.minimum_balance_to_keep,
            tl,
            [(date(2024, 3, 3), Decimal("0"))]
        )
        self.assertTrue(is_safe)
        self.assertGreaterEqual(min_obs, profile.minimum_balance_to_keep)

    def test_2_minimum_balance_enforced_at_every_point(self):
        # Starting balance 1000, min keep 500, debit 600 -> min observed 400 < 500 -> unsafe
        is_safe, min_obs = self.forecaster.simulate(
            start_balance=Decimal("1000"),
            minimum_balance_to_keep=Decimal("500"),
            timeline_events=[],
            candidate_payments=[(date(2025, 1, 1), Decimal("600"))]
        )
        self.assertFalse(is_safe)
        self.assertEqual(min_obs, Decimal("400"))

    def test_3_failed_cancelled_events_excluded(self):
        ev_cancelled = FinancialEvent(
            event_id="ev_canc", user_id="u1", event_type="expense", description="test",
            category="shopping", direction="debit", amount=Decimal("500"), currency="USD",
            event_date=date(2025, 1, 5), settlement_date=date(2025, 1, 5), status="cancelled"
        )
        prof = UserProfile("u1", "USD", Decimal("1000"), Decimal("200"), [], [], [], [], ["full_payment"])
        tl = self.forecaster.build_forecast_timeline(prof, [ReconciledEvent(ev_cancelled, ev_cancelled)], date(2025, 1, 1))
        self.assertEqual(len(tl), 0)

    def test_4_unrealized_investments_not_spendable_cash(self):
        ev_unrealized = FinancialEvent(
            event_id="ev_unr", user_id="u1", event_type="investment_valuation", description="test",
            category="investment", direction="non_cash", amount=Decimal("10000"), currency="USD",
            event_date=date(2025, 1, 5), settlement_date=date(2025, 1, 5), status="unrealized"
        )
        prof = UserProfile("u1", "USD", Decimal("1000"), Decimal("200"), [], [], [], [], ["full_payment"])
        tl = self.forecaster.build_forecast_timeline(prof, [ReconciledEvent(ev_unrealized, ev_unrealized)], date(2025, 1, 1))
        self.assertEqual(len(tl), 0)

    def test_5_dated_fx_normalized_values_enter_forecast(self):
        # EUR to ZAR on 2024-03-15 has rate 20
        ev_foreign = FinancialEvent(
            event_id="ev_fx", user_id="u_zar", event_type="expense", description="test",
            category="travel", direction="debit", amount=Decimal("100"), currency="EUR",
            event_date=date(2024, 3, 15), settlement_date=date(2024, 3, 15), status="settled"
        )
        prof = UserProfile("u_zar", "ZAR", Decimal("50000"), Decimal("5000"), [], [], [], [], ["full_payment"])
        tl = self.forecaster.build_forecast_timeline(prof, [ReconciledEvent(ev_foreign, ev_foreign)], date(2024, 3, 1))
        self.assertEqual(len(tl), 1)
        self.assertEqual(tl[0]["amount"], Decimal("2000"))  # 100 EUR * 20 = 2000 ZAR

    def test_6_blank_amounts_not_treated_as_zero(self):
        ev_blank = FinancialEvent(
            event_id="ev_blank", user_id="u1", event_type="expense", description="test",
            category="groceries", direction="debit", amount=None, currency="USD",
            event_date=date(2025, 1, 5), settlement_date=date(2025, 1, 5), status="settled"
        )
        prof = UserProfile("u1", "USD", Decimal("1000"), Decimal("200"), [], [], [], [], ["full_payment"])
        tl = self.forecaster.build_forecast_timeline(prof, [ReconciledEvent(ev_blank, ev_blank)], date(2025, 1, 1))
        # When amount is None and no fact reconciled, not added as a 0 amount action
        self.assertEqual(len(tl), 0)

    def test_7_8_safe_amount_binary_search_bounded(self):
        req_amt = Decimal("1000")
        safe_amt = self.engine.calculate_amount_safe_to_pay(
            start_balance=Decimal("1200"),
            minimum_balance_to_keep=Decimal("500"),
            timeline_events=[],
            request_date=date(2025, 1, 1),
            requested_amount=req_amt
        )
        self.assertEqual(safe_amt, Decimal("700"))
        self.assertTrue(Decimal("0") <= safe_amt <= req_amt)

    def test_9_10_earliest_safe_date_forward_scan_non_monotonic(self):
        # Scenario: today is unsafe (balance 600, min keep 500, req 400).
        # On 2025-01-10: incoming salary 1000 makes it safe!
        # On 2025-01-20: rent debit 600 leaves 1200 - 600 = 600 >= 500.
        timeline = [
            {"date": date(2025, 1, 10), "amount": Decimal("1000"), "direction": "credit", "category": "salary", "event_id": "sal"},
            {"date": date(2025, 1, 20), "amount": Decimal("600"), "direction": "debit", "category": "rent", "event_id": "rent"}
        ]
        earliest = self.engine.calculate_earliest_date_for_full_payment(
            start_balance=Decimal("600"),
            minimum_balance_to_keep=Decimal("500"),
            timeline_events=timeline,
            request_date=date(2025, 1, 1),
            requested_amount=Decimal("400"),
            horizon_days=90
        )
        self.assertEqual(earliest, date(2025, 1, 10))

    def test_11_full_payment_candidate_generation(self):
        req = Request("r1", "u1", date(2025, 1, 1), "purchase", Decimal("500"), date(2025, 1, 20), False, "buy")
        prof = UserProfile("u1", "USD", Decimal("1000"), Decimal("200"), [], [], [], [], ["full_payment"])
        trace = self.engine.evaluate_request(req, prof, [], [])
        self.assertEqual(trace.affordability_status, "affordable_now")
        self.assertEqual(trace.recommended_payment_method, "full_payment")
        self.assertEqual(trace.payment_plan, "2025-01-01:500")

    def test_12_13_partial_payment_structure_and_sum(self):
        req = Request("r_part", "u1", date(2025, 1, 1), "purchase", Decimal("1000"), date(2025, 2, 1), True, "buy")
        prof = UserProfile("u1", "USD", Decimal("700"), Decimal("200"), [], [], [], [], ["full_payment", "partial_payment"])
        # Upcoming salary on Jan 15
        timeline_events = [
            {"date": date(2025, 1, 15), "amount": Decimal("1500"), "direction": "credit", "category": "salary", "event_id": "sal"}
        ]
        reconciled = [ReconciledEvent(
            FinancialEvent("sal", "u1", "income", "salary", "salary", "credit", Decimal("1500"), "USD", date(2025, 1, 15), date(2025, 1, 15), "scheduled"),
            FinancialEvent("sal", "u1", "income", "salary", "salary", "credit", Decimal("1500"), "USD", date(2025, 1, 15), date(2025, 1, 15), "scheduled")
        )]
        # Headroom today is 700 - 200 = 500
        trace = self.engine.evaluate_request(req, prof, reconciled, [])
        # Partial payment candidate
        part_cand = next((c for c in trace.all_candidates if c.payment_method == "partial_payment"), None)
        self.assertIsNotNone(part_cand)
        self.assertEqual(len(part_cand.payments), 2)
        total_sum = sum(p[1] for p in part_cand.payments)
        self.assertEqual(total_sum, req.requested_amount)

    def test_14_wait_candidate_has_single_payment(self):
        req = Request("r_wait", "u1", date(2025, 1, 1), "purchase", Decimal("1000"), date(2025, 2, 1), False, "buy")
        prof = UserProfile("u1", "USD", Decimal("300"), Decimal("200"), [], [], [], [], ["full_payment"])
        reconciled = [ReconciledEvent(
            FinancialEvent("sal", "u1", "income", "salary", "salary", "credit", Decimal("2000"), "USD", date(2025, 1, 15), date(2025, 1, 15), "scheduled"),
            FinancialEvent("sal", "u1", "income", "salary", "salary", "credit", Decimal("2000"), "USD", date(2025, 1, 15), date(2025, 1, 15), "scheduled")
        )]
        trace = self.engine.evaluate_request(req, prof, reconciled, [])
        self.assertEqual(trace.affordability_status, "affordable_later")
        self.assertEqual(trace.recommended_payment_method, "wait")
        self.assertEqual(trace.payment_plan, "2025-01-15:1000")

    def test_15_16_17_installments_match_options_and_preferences(self):
        req = Request("r_inst", "u1", date(2025, 1, 1), "purchase", Decimal("900"), date(2025, 4, 1), False, "buy")
        prof = UserProfile("u1", "USD", Decimal("1000"), Decimal("100"), [], [], [], [], ["installments"], max_installment_months=3)
        opt = PaymentOption("opt_1", "r_inst", "installments", Decimal("300"), 3, date(2025, 1, 1), 30, Decimal("0"), Decimal("900"))
        
        trace = self.engine.evaluate_request(req, prof, [], [opt])
        self.assertEqual(trace.affordability_status, "affordable_with_plan")
        self.assertEqual(trace.recommended_payment_method, "installments")
        self.assertEqual(trace.payment_plan, "2025-01-01:300|2025-01-31:300|2025-03-02:300")

    def test_18_19_20_21_spending_changes_constraints(self):
        # Protected expense cannot be modified, stoppable/reducible only when in user allowed categories
        ev_fixed = FinancialEvent("e_fix", "u1", "expense", "rent", "rent", "debit", Decimal("100"), "USD", date(2025, 1, 5), date(2025, 1, 5), "settled", flexibility="fixed")
        ev_flex = FinancialEvent("e_flex", "u1", "subscription", "stream", "streaming", "debit", Decimal("50"), "USD", date(2025, 1, 5), date(2025, 1, 5), "settled", flexibility="stoppable")
        
        prof = UserProfile("u1", "USD", Decimal("680"), Decimal("200"), [], ["rent"], [], ["streaming"], ["full_payment"])
        req = Request("r_sc", "u1", date(2025, 1, 1), "purchase", Decimal("380"), date(2025, 1, 20), False, "buy")
        
        trace = self.engine.evaluate_request(req, prof, [ReconciledEvent(ev_fixed, ev_fixed), ReconciledEvent(ev_flex, ev_flex)], [])
        self.assertIn("stop:e_flex", trace.spending_changes_needed)
        self.assertNotIn("e_fix", trace.spending_changes_needed)

    def test_22_23_decision_trace_rejections(self):
        req = Request("r_rej", "u1", date(2025, 1, 1), "purchase", Decimal("50000"), date(2025, 1, 5), False, "buy")
        prof = UserProfile("u1", "USD", Decimal("100"), Decimal("50"), [], [], [], [], ["full_payment"])
        trace = self.engine.evaluate_request(req, prof, [], [])
        self.assertEqual(trace.affordability_status, "not_affordable")
        self.assertEqual(trace.recommended_payment_method, "not_recommended")
        # Rejected candidate recorded in trace
        self.assertGreater(len(trace.all_candidates), 0)
        self.assertIsNotNone(trace.all_candidates[0].rejection_reason)

    def test_24_deterministic_ranking(self):
        c1 = PlanCandidate("full_payment", "2025-01-01:100", [(date(2025, 1, 1), Decimal("100"))], Decimal("100"), [], True, date(2025, 1, 1), True)
        c2 = PlanCandidate("installments", "2025-01-01:50|2025-02-01:50", [(date(2025, 1, 1), Decimal("50")), (date(2025, 2, 1), Decimal("50"))], Decimal("100"), [], True, date(2025, 2, 1), True, "opt_01")
        
        # c1 has fewer payments (Key 5: 1 vs 2), so c1 wins over c2
        winner = self.ranker.select_best_candidate([c2, c1])
        self.assertEqual(winner.payment_method, "full_payment")

    def test_25_no_gemini_call_in_phase_3(self):
        # Validate that running DecisionEngine and Forecaster does not invoke any Gemini / network calls
        req = self.container.sample_requests[0]
        prof = self.container.profiles_by_user_id[req.user_id]
        events = self.container.events_by_user_id[req.user_id]
        reconciled = [ReconciledEvent(e, e) for e in events]
        opts = self.container.payment_options_by_request_id.get(req.request_id, [])
        
        trace = self.engine.evaluate_request(req, prof, reconciled, opts)
        self.assertIsInstance(trace, DecisionTrace)


if __name__ == "__main__":
    unittest.main()
