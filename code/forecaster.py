"""
90-Day Deterministic Financial Forecaster for Buy or Wait? Financial Decision Agent.
Reconstructs daily cash flow timeline and validates minimum balance safety over 90 days.
"""

from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
from collections import defaultdict
import calendar
from typing import Dict, List, Optional, Tuple, Set, Any

from models import (
    UserProfile,
    FinancialEvent,
    ReconciledEvent,
    ExtractedFact,
    PaymentOption,
)
from currency_normalizer import ExchangeRateTable


def add_months(sourcedate: date, months: int) -> date:
    """Add calendar months to a date safely handling month lengths."""
    month = sourcedate.month - 1 + months
    year = sourcedate.year + month // 12
    month = month % 12 + 1
    day = min(sourcedate.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


class FinancialForecaster:
    """Reconstructs cash flows and simulates 90-day balance trajectories."""

    def __init__(self, rate_table: ExchangeRateTable):
        self.rate_table = rate_table

    def build_forecast_timeline(
        self,
        user_profile: UserProfile,
        reconciled_events: List[ReconciledEvent],
        request_date: date,
        horizon_days: int = 90
    ) -> List[Dict[str, Any]]:
        """
        Builds a chronological list of cash flow events over [request_date, request_date + horizon_days].
        Accounts for reconciled facts, scheduled items, and historical recurring commitments.
        """
        events = [r.event for r in reconciled_events]
        home_curr = user_profile.home_currency
        end_date = request_date + timedelta(days=horizon_days)

        history = [
            e for e in events
            if (e.settlement_date or e.event_date) < request_date and e.status == "settled"
        ]
        future_csv = [
            e for e in events
            if (e.settlement_date or e.event_date) >= request_date
        ]

        timeline: List[Dict[str, Any]] = []

        # 1. Add valid future events from CSV
        for fe in future_csv:
            dt = fe.settlement_date or fe.event_date
            if dt > end_date:
                continue
            if fe.status in ("cancelled", "failed", "unrealized") or fe.direction == "non_cash":
                continue
            if fe.direction == "credit" and fe.status == "pending":
                continue  # Pending credits do not count as available cash

            amt = fe.amount
            if amt is not None:
                amt_home = self.rate_table.convert(amt, fe.currency, home_curr, dt)
                timeline.append({
                    "date": dt,
                    "amount": amt_home,
                    "direction": fe.direction,
                    "category": fe.category,
                    "event_id": fe.event_id,
                    "flexibility": fe.flexibility,
                    "minimum_allowed_amount": fe.minimum_allowed_amount,
                    "status": fe.status,
                    "from_csv": True,
                    "source_event_id": fe.event_id,
                    "description": fe.description
                })

        # 2. Add recurring projections from full known history + scheduled events
        by_cat: Dict[str, List[FinancialEvent]] = defaultdict(list)
        for e in events:
            if e.status in ("settled", "scheduled") and e.direction in ("credit", "debit") and e.amount is not None:
                by_cat[e.category].append(e)

        for cat, ev_list in by_cat.items():
            if len(ev_list) < 2:
                continue
            ev_list.sort(key=lambda e: e.settlement_date or e.event_date)
            last_ev = ev_list[-1]
            last_dt = last_ev.settlement_date or last_ev.event_date

            # Calculate intervals
            intervals = []
            for i in range(1, len(ev_list)):
                d1 = ev_list[i - 1].settlement_date or ev_list[i - 1].event_date
                d2 = ev_list[i].settlement_date or ev_list[i].event_date
                intervals.append((d2 - d1).days)
            
            avg_interval = sum(intervals[-3:]) / len(intervals[-3:])

            if 25 <= avg_interval <= 35:
                # Monthly recurring (salary, rent, utilities, subscriptions, debt repayments, etc.)
                for m in range(1, 5):
                    next_dt = add_months(last_dt, m)
                    if next_dt < request_date:
                        continue
                    if next_dt > end_date:
                        break
                    already_in = any(
                        e["category"] == cat and abs((e["date"] - next_dt).days) <= 7
                        for e in timeline
                    )
                    if not already_in and last_ev.amount is not None:
                        amt_home = self.rate_table.convert(last_ev.amount, last_ev.currency, home_curr, next_dt)
                        timeline.append({
                            "date": next_dt,
                            "amount": amt_home,
                            "direction": last_ev.direction,
                            "category": cat,
                            "event_id": f"proj_{last_ev.event_id}_{m}",
                            "flexibility": last_ev.flexibility,
                            "minimum_allowed_amount": last_ev.minimum_allowed_amount,
                            "status": "scheduled",
                            "from_csv": False,
                            "source_event_id": last_ev.event_id,
                            "description": last_ev.description
                        })
            elif avg_interval < 25:
                # Periodic recurring (groceries, transport, dining)
                step_days = max(1, round(avg_interval))
                next_dt = last_dt + timedelta(days=step_days)
                step_idx = 1
                while next_dt <= end_date:
                    if next_dt >= request_date:
                        already_in = any(
                            e["category"] == cat and abs((e["date"] - next_dt).days) <= 2
                            for e in timeline
                        )
                        if not already_in and last_ev.amount is not None:
                            amt_home = self.rate_table.convert(last_ev.amount, last_ev.currency, home_curr, next_dt)
                            timeline.append({
                                "date": next_dt,
                                "amount": amt_home,
                                "direction": last_ev.direction,
                                "category": cat,
                                "event_id": f"proj_{last_ev.event_id}_{step_idx}",
                                "flexibility": last_ev.flexibility,
                                "minimum_allowed_amount": last_ev.minimum_allowed_amount,
                                "status": "scheduled",
                                "from_csv": False,
                                "source_event_id": last_ev.event_id,
                                "description": last_ev.description
                            })
                    next_dt += timedelta(days=step_days)
                    step_idx += 1

        timeline.sort(key=lambda x: x["date"])
        return timeline

    def simulate(
        self,
        start_balance: Decimal,
        minimum_balance_to_keep: Decimal,
        timeline_events: List[Dict[str, Any]],
        candidate_payments: List[Tuple[date, Decimal]],
        spending_changes: Optional[List[str]] = None
    ) -> Tuple[bool, Decimal]:
        """
        Simulate chronological cash flows over 90 days.
        Enforces conservative debits-before-credits check on each day.
        Returns (is_safe, min_observed_balance).
        """
        spending_changes = spending_changes or []
        stop_ids = {sc.split(":")[1] for sc in spending_changes if sc.startswith("stop:")}
        reduce_map = {
            sc.split(":")[1]: Decimal(sc.split(":")[2])
            for sc in spending_changes if sc.startswith("reduce_to:")
        }

        actions: Dict[date, Dict[str, Decimal]] = defaultdict(lambda: {"credit": Decimal("0"), "debit": Decimal("0")})

        for ev in timeline_events:
            dt = ev["date"]
            ev_id = ev["event_id"]
            src_id = ev.get("source_event_id", ev_id)

            if ev_id in stop_ids or src_id in stop_ids:
                continue

            amt = ev["amount"]
            if ev_id in reduce_map:
                amt = reduce_map[ev_id]
            elif src_id in reduce_map:
                amt = reduce_map[src_id]

            if ev["direction"] == "credit":
                actions[dt]["credit"] += amt
            elif ev["direction"] == "debit":
                actions[dt]["debit"] += amt

        for p_dt, p_amt in candidate_payments:
            actions[p_dt]["debit"] += p_amt

        cur_bal = start_balance
        min_obs = cur_bal

        for dt in sorted(actions.keys()):
            # Credits applied first on the same day (e.g. salary received on payday)
            cur_bal += actions[dt]["credit"]
            
            # Debits applied next
            cur_bal -= actions[dt]["debit"]
            if cur_bal < min_obs:
                min_obs = cur_bal

        is_safe = (min_obs >= minimum_balance_to_keep)
        return is_safe, min_obs
