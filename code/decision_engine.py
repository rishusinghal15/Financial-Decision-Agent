"""
Deterministic Financial Decision Engine for Buy or Wait?
Generates candidate plans, evaluates safety via monotonic binary search and forward scanning,
ranks candidates with the 6-key ranker, and produces structured decision traces.
"""

from datetime import date, timedelta
from decimal import Decimal
from itertools import combinations
from typing import Dict, List, Optional, Tuple, Any

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


def _format_plan_amt(amt: Decimal) -> str:
    if amt == amt.to_integral():
        return str(int(amt))
    return f"{amt:.2f}"


def format_payment_plan(payments: List[Tuple[date, Decimal]]) -> str:
    """Format payments list into chronological 'YYYY-MM-DD:amount|...' string or 'none'."""
    if not payments:
        return "none"
    return "|".join(f"{dt.isoformat()}:{_format_plan_amt(amt)}" for dt, amt in sorted(payments, key=lambda x: x[0]))



class DecisionEngine:
    """Evaluates requests, explores candidate payment strategies, and selects winning plans."""

    def __init__(self, forecaster: FinancialForecaster, ranker: Optional[PlanRanker] = None):
        self.forecaster = forecaster
        self.ranker = ranker or PlanRanker()

    def evaluate_request(
        self,
        request: Request,
        user_profile: UserProfile,
        reconciled_events: List[ReconciledEvent],
        payment_options: List[PaymentOption]
    ) -> DecisionTrace:
        """
        Evaluate a single financial request and return a comprehensive DecisionTrace.
        """
        req_date = request.request_date
        req_amt = request.requested_amount
        due_date = request.desired_completion_date

        # 1. Build 90-day forecast timeline
        timeline = self.forecaster.build_forecast_timeline(
            user_profile=user_profile,
            reconciled_events=reconciled_events,
            request_date=req_date,
            horizon_days=90
        )

        # 2. Compute amount_safe_to_pay using MONOTONIC BINARY SEARCH
        amount_safe_to_pay = self.calculate_amount_safe_to_pay(
            start_balance=user_profile.current_available_balance,
            minimum_balance_to_keep=user_profile.minimum_balance_to_keep,
            timeline_events=timeline,
            request_date=req_date,
            requested_amount=req_amt
        )

        # 3. Compute earliest_date_for_full_payment using FORWARD CHRONOLOGICAL SCAN
        earliest_date_for_full_payment = self.calculate_earliest_date_for_full_payment(
            start_balance=user_profile.current_available_balance,
            minimum_balance_to_keep=user_profile.minimum_balance_to_keep,
            timeline_events=timeline,
            request_date=req_date,
            requested_amount=req_amt,
            horizon_days=90
        )

        # 4. Generate all candidate plans
        candidates = self.generate_all_candidates(
            request=request,
            user_profile=user_profile,
            timeline=timeline,
            payment_options=payment_options,
            amount_safe_to_pay=amount_safe_to_pay,
            earliest_date_for_full_payment=earliest_date_for_full_payment,
            reconciled_events=reconciled_events
        )

        # 5. Filter safe and eligible candidates (Hard Safety Gate)
        # Safety validation is a hard constraint: any candidate that breaches minimum_balance_to_keep
        # or violates user preferences is strictly rejected here. Unsafe candidates never proceed to ranking.
        safe_eligible_candidates = [c for c in candidates if c.is_safe and c.rejection_reason is None]

        # 6. Rank safe eligible candidates using the 6-key ranker
        # Ranking operates exclusively on candidates that have already been validated as safe.
        winning_candidate = self.ranker.select_best_candidate(safe_eligible_candidates)

        # 7. Formulate final status and output representations
        if winning_candidate is None or winning_candidate.payment_method == "not_recommended":
            affordability_status = "not_affordable"
            recommended_method = "not_recommended"
            payment_plan_str = "none"
            spending_changes_str = "none"
            final_earliest_date = earliest_date_for_full_payment if earliest_date_for_full_payment else None
        else:
            recommended_method = winning_candidate.payment_method
            payment_plan_str = winning_candidate.payment_plan_str
            spending_changes_str = (
                "|".join(winning_candidate.spending_changes)
                if winning_candidate.spending_changes else "none"
            )

            if recommended_method == "full_payment":
                if not winning_candidate.spending_changes:
                    affordability_status = "affordable_now"
                else:
                    affordability_status = "affordable_with_plan"
            elif recommended_method in ("partial_payment", "installments"):
                affordability_status = "affordable_with_plan"
            elif recommended_method == "wait":
                affordability_status = "affordable_later"
            else:
                affordability_status = "not_affordable"

            final_earliest_date = earliest_date_for_full_payment

        # For affordable_now, earliest_date_for_full_payment must equal request_date
        if affordability_status == "affordable_now":
            final_earliest_date = req_date

        trace = DecisionTrace(
            request_id=request.request_id,
            amount_safe_to_pay=amount_safe_to_pay,
            affordability_status=affordability_status,
            recommended_payment_method=recommended_method,
            payment_plan=payment_plan_str,
            earliest_date_for_full_payment=final_earliest_date,
            spending_changes_needed=spending_changes_str,
            selected_plan=winning_candidate,
            all_candidates=candidates,
            explanation_facts={
                "current_balance": user_profile.current_available_balance,
                "minimum_balance": user_profile.minimum_balance_to_keep,
                "home_currency": user_profile.home_currency,
                "requested_amount": req_amt,
                "desired_completion_date": due_date,
                "earliest_date_for_full_payment": final_earliest_date,
                "amount_safe_to_pay": amount_safe_to_pay,
            }
        )

        return trace

    def calculate_amount_safe_to_pay(
        self,
        start_balance: Decimal,
        minimum_balance_to_keep: Decimal,
        timeline_events: List[Dict[str, Any]],
        request_date: date,
        requested_amount: Decimal
    ) -> Decimal:
        """
        Calculates amount_safe_to_pay via MONOTONIC BINARY SEARCH on [0, requested_amount].
        """
        is_safe_0, _ = self.forecaster.simulate(
            start_balance=start_balance,
            minimum_balance_to_keep=minimum_balance_to_keep,
            timeline_events=timeline_events,
            candidate_payments=[(request_date, Decimal("0"))]
        )
        if not is_safe_0:
            return Decimal("0")

        is_safe_full, _ = self.forecaster.simulate(
            start_balance=start_balance,
            minimum_balance_to_keep=minimum_balance_to_keep,
            timeline_events=timeline_events,
            candidate_payments=[(request_date, requested_amount)]
        )
        if is_safe_full:
            return requested_amount

        # Binary search
        low = Decimal("0")
        high = requested_amount
        for _ in range(35):
            mid = (low + high) / Decimal("2")
            mid_round = round(mid, 2)
            is_safe, _ = self.forecaster.simulate(
                start_balance=start_balance,
                minimum_balance_to_keep=minimum_balance_to_keep,
                timeline_events=timeline_events,
                candidate_payments=[(request_date, mid_round)]
            )
            if is_safe:
                low = mid_round
            else:
                high = mid_round

            if high - low <= Decimal("0.01"):
                break

        res = round(low, 2)
        # Bounded between 0 and requested_amount
        return max(Decimal("0"), min(requested_amount, res))

    def calculate_earliest_date_for_full_payment(
        self,
        start_balance: Decimal,
        minimum_balance_to_keep: Decimal,
        timeline_events: List[Dict[str, Any]],
        request_date: date,
        requested_amount: Decimal,
        horizon_days: int = 90
    ) -> Optional[date]:
        """
        Calculates earliest_date_for_full_payment using a FORWARD CHRONOLOGICAL SCAN.
        Evaluates paying requested_amount on candidate dates within the horizon.
        """
        end_date = request_date + timedelta(days=horizon_days)

        # Collect candidate dates where balance changes or key events occur
        candidate_dates = {request_date}
        for ev in timeline_events:
            dt = ev["date"]
            if request_date <= dt <= end_date:
                candidate_dates.add(dt)
                # Also check day after income settlement
                if ev["direction"] == "credit":
                    candidate_dates.add(dt)

        # Also add all timeline dates
        for day_offset in range(horizon_days + 1):
            candidate_dates.add(request_date + timedelta(days=day_offset))

        sorted_dates = sorted(candidate_dates)

        for candidate_date in sorted_dates:
            is_safe, _ = self.forecaster.simulate(
                start_balance=start_balance,
                minimum_balance_to_keep=minimum_balance_to_keep,
                timeline_events=timeline_events,
                candidate_payments=[(candidate_date, requested_amount)]
            )
            if is_safe:
                return candidate_date

        return None

    def generate_all_candidates(
        self,
        request: Request,
        user_profile: UserProfile,
        timeline: List[Dict[str, Any]],
        payment_options: List[PaymentOption],
        amount_safe_to_pay: Decimal,
        earliest_date_for_full_payment: Optional[date],
        reconciled_events: List[ReconciledEvent]
    ) -> List[PlanCandidate]:
        """
        Generates full_payment, partial_payment, installments, wait, and spending-change candidates.
        """
        candidates: List[PlanCandidate] = []
        user_methods = set(user_profile.payment_methods_user_will_consider)

        req_date = request.request_date
        req_amt = request.requested_amount
        due_date = request.desired_completion_date

        # A. Full Payment Candidate (Baseline, no spending changes)
        full_payments = [(req_date, req_amt)]
        is_safe_full, _ = self.forecaster.simulate(
            start_balance=user_profile.current_available_balance,
            minimum_balance_to_keep=user_profile.minimum_balance_to_keep,
            timeline_events=timeline,
            candidate_payments=full_payments
        )
        rejection = None
        if "full_payment" not in user_methods:
            rejection = "User does not consider full_payment"
        elif not is_safe_full:
            rejection = "Violates minimum balance to keep on request date"

        candidates.append(
            PlanCandidate(
                payment_method="full_payment",
                payment_plan_str=format_payment_plan(full_payments),
                payments=full_payments,
                total_cost=req_amt,
                spending_changes=[],
                is_safe=is_safe_full,
                completion_date=req_date,
                completes_by_deadline=(req_date <= due_date),
                rejection_reason=rejection
            )
        )

        # B. Partial Payment Candidate
        if request.allows_partial_payment and "partial_payment" in user_methods:
            if Decimal("0") < amount_safe_to_pay < req_amt and earliest_date_for_full_payment is not None:
                part_payments = [
                    (req_date, amount_safe_to_pay),
                    (earliest_date_for_full_payment, req_amt - amount_safe_to_pay)
                ]
                is_safe_part, _ = self.forecaster.simulate(
                    start_balance=user_profile.current_available_balance,
                    minimum_balance_to_keep=user_profile.minimum_balance_to_keep,
                    timeline_events=timeline,
                    candidate_payments=part_payments
                )
                completes_deadline = (earliest_date_for_full_payment <= due_date)
                rejection = None if (is_safe_part and completes_deadline) else (
                    "Exceeds desired completion date" if not completes_deadline else "Unsafe cash flow"
                )

                candidates.append(
                    PlanCandidate(
                        payment_method="partial_payment",
                        payment_plan_str=format_payment_plan(part_payments),
                        payments=part_payments,
                        total_cost=req_amt,
                        spending_changes=[],
                        is_safe=is_safe_part,
                        completion_date=earliest_date_for_full_payment,
                        completes_by_deadline=completes_deadline,
                        rejection_reason=rejection
                    )
                )

        # C. Installment Candidates (from supplied options only)
        if "installments" in user_methods:
            max_inst_months = user_profile.max_installment_months
            for opt in payment_options:
                if opt.payment_method != "installments":
                    continue

                # Build schedule from option
                inst_payments = []
                n_payments = opt.number_of_payments
                freq_days = opt.payment_frequency_days or 30
                first_dt = opt.first_payment_date

                for i in range(n_payments):
                    p_dt = first_dt + timedelta(days=i * freq_days)
                    inst_payments.append((p_dt, opt.payment_amount))

                last_dt = inst_payments[-1][0]
                completes_deadline = (last_dt <= due_date)

                # Check max installment duration in months
                duration_days = (last_dt - first_dt).days
                approx_months = round(duration_days / 30) + 1
                exceeds_max_months = (max_inst_months is not None and approx_months > max_inst_months)

                is_safe_inst, _ = self.forecaster.simulate(
                    start_balance=user_profile.current_available_balance,
                    minimum_balance_to_keep=user_profile.minimum_balance_to_keep,
                    timeline_events=timeline,
                    candidate_payments=inst_payments
                )

                rejection = None
                if exceeds_max_months:
                    rejection = f"Installment duration ({approx_months} months) exceeds max permitted ({max_inst_months})"
                elif not is_safe_inst:
                    rejection = "Violates minimum balance during installment schedule"

                candidates.append(
                    PlanCandidate(
                        payment_method="installments",
                        payment_plan_str=format_payment_plan(inst_payments),
                        payments=inst_payments,
                        total_cost=opt.total_payable_amount,
                        spending_changes=[],
                        is_safe=is_safe_inst,
                        completion_date=last_dt,
                        completes_by_deadline=completes_deadline,
                        payment_option_id=opt.payment_option_id,
                        rejection_reason=rejection
                    )
                )

        # D. Wait Candidate
        if earliest_date_for_full_payment is not None and "full_payment" in user_methods:
            wait_payments = [(earliest_date_for_full_payment, req_amt)]
            is_safe_wait, _ = self.forecaster.simulate(
                start_balance=user_profile.current_available_balance,
                minimum_balance_to_keep=user_profile.minimum_balance_to_keep,
                timeline_events=timeline,
                candidate_payments=wait_payments
            )
            completes_deadline = (earliest_date_for_full_payment <= due_date)
            rejection = None if (is_safe_wait and completes_deadline) else (
                "Earliest safe date exceeds desired completion date" if not completes_deadline else "Unsafe cash flow"
            )

            candidates.append(
                PlanCandidate(
                    payment_method="wait",
                    payment_plan_str=format_payment_plan(wait_payments),
                    payments=wait_payments,
                    total_cost=req_amt,
                    spending_changes=[],
                    is_safe=is_safe_wait,
                    completion_date=earliest_date_for_full_payment,
                    completes_by_deadline=completes_deadline,
                    rejection_reason=rejection
                )
            )

        # E. Spending Changes Candidates (for full payment on request_date)
        if "full_payment" in user_methods and not is_safe_full:
            spending_candidates = self._generate_spending_change_candidates(
                user_profile=user_profile,
                timeline=timeline,
                reconciled_events=reconciled_events,
                req_date=req_date,
                req_amt=req_amt,
                due_date=due_date
            )
            candidates.extend(spending_candidates)

        return candidates

    def _generate_spending_change_candidates(
        self,
        user_profile: UserProfile,
        timeline: List[Dict[str, Any]],
        reconciled_events: List[ReconciledEvent],
        req_date: date,
        req_amt: Decimal,
        due_date: date
    ) -> List[PlanCandidate]:
        """
        Generate candidate plans with up to 3 permitted spending changes.
        """
        candidates: List[PlanCandidate] = []
        willing_to_stop = set(user_profile.expense_categories_user_is_willing_to_stop)
        willing_to_reduce = set(user_profile.expense_categories_user_is_willing_to_reduce)
        protected = set(user_profile.expense_categories_to_protect)

        # Identify candidate events to stop or reduce
        possible_changes: List[str] = []

        # Find unique recurring flexible events
        seen_events = set()
        for ev in reconciled_events:
            e = ev.event
            if e.event_id in seen_events:
                continue
            seen_events.add(e.event_id)

            if e.category in protected:
                continue

            # Stoppable
            if e.category in willing_to_stop and e.flexibility in ("stoppable", "reducible_or_stoppable"):
                possible_changes.append(f"stop:{e.event_id}")

            # Reducible
            if e.category in willing_to_reduce and e.flexibility in ("reducible", "reducible_or_stoppable"):
                min_amt = e.minimum_allowed_amount or Decimal("0")
                if e.amount is not None and e.amount > min_amt:
                    possible_changes.append(f"reduce_to:{e.event_id}:{min_amt}")

        # Try combinations of 1, 2, 3 changes
        for k in range(1, min(4, len(possible_changes) + 1)):
            for comb in combinations(possible_changes, k):
                # Ensure no event is both stopped and reduced
                event_targets = set()
                valid_comb = True
                for sc in comb:
                    target_id = sc.split(":")[1]
                    if target_id in event_targets:
                        valid_comb = False
                        break
                    event_targets.add(target_id)

                if not valid_comb:
                    continue

                changes_list = list(comb)
                full_payments = [(req_date, req_amt)]
                is_safe, _ = self.forecaster.simulate(
                    start_balance=user_profile.current_available_balance,
                    minimum_balance_to_keep=user_profile.minimum_balance_to_keep,
                    timeline_events=timeline,
                    candidate_payments=full_payments,
                    spending_changes=changes_list
                )

                if is_safe:
                    candidates.append(
                        PlanCandidate(
                            payment_method="full_payment",
                            payment_plan_str=format_payment_plan(full_payments),
                            payments=full_payments,
                            total_cost=req_amt,
                            spending_changes=changes_list,
                            is_safe=True,
                            completion_date=req_date,
                            completes_by_deadline=(req_date <= due_date),
                            rejection_reason=None
                        )
                    )

        return candidates
