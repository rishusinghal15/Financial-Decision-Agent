import os
import sys
from datetime import date, timedelta
from decimal import Decimal
from collections import defaultdict
import calendar

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from data_loader import DataLoader
from models import ReconciledEvent, UserProfile, FinancialEvent
from currency_normalizer import ExchangeRateTable
from ranker import PlanRanker
from decision_engine import DecisionEngine

def add_months(sourcedate: date, months: int) -> date:
    month = sourcedate.month - 1 + months
    year = sourcedate.year + month // 12
    month = month % 12 + 1
    day = min(sourcedate.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)

class ConfirmedIncomeForecaster:
    def __init__(self, rate_table: ExchangeRateTable):
        self.rate_table = rate_table

    def build_forecast_timeline(self, user_profile: UserProfile, reconciled_events: list, request_date: date, horizon_days: int = 90):
        events = [r.event for r in reconciled_events]
        home_curr = user_profile.home_currency
        end_date = request_date + timedelta(days=horizon_days)

        future_csv = [e for e in events if (e.settlement_date or e.event_date) >= request_date]
        timeline = []

        # 1. Add valid future events from CSV
        for fe in future_csv:
            dt = fe.settlement_date or fe.event_date
            if dt > end_date:
                continue
            if fe.status in ("cancelled", "failed", "unrealized") or fe.direction == "non_cash":
                continue
            if fe.direction == "credit" and fe.status == "pending":
                continue  # Pending credits do not count

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

        # 2. Add recurring EXPENSES only (income only comes from confirmed CSV events/messages)
        by_cat = defaultdict(list)
        for e in events:
            if e.direction == "debit" and e.status in ("settled", "scheduled") and e.amount is not None:
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
                # Monthly recurring expenses
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
                    if not already_in:
                        amt_home = self.rate_table.convert(last_ev.amount, last_ev.currency, home_curr, next_dt)
                        timeline.append({
                            "date": next_dt,
                            "amount": amt_home,
                            "direction": "debit",
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
                # Periodic recurring expenses
                step_days = max(1, round(avg_interval))
                next_dt = last_dt + timedelta(days=step_days)
                step_idx = 1
                while next_dt <= end_date:
                    if next_dt >= request_date:
                        already_in = any(
                            e["category"] == cat and abs((e["date"] - next_dt).days) <= 2
                            for e in timeline
                        )
                        if not already_in:
                            amt_home = self.rate_table.convert(last_ev.amount, last_ev.currency, home_curr, next_dt)
                            timeline.append({
                                "date": next_dt,
                                "amount": amt_home,
                                "direction": "debit",
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

    def simulate(self, start_balance, minimum_balance_to_keep, timeline_events, candidate_payments, spending_changes=None):
        spending_changes = spending_changes or []
        stop_ids = {sc.split(":")[1] for sc in spending_changes if sc.startswith("stop:")}
        reduce_map = {
            sc.split(":")[1]: Decimal(sc.split(":")[2])
            for sc in spending_changes if sc.startswith("reduce_to:")
        }

        actions = defaultdict(lambda: {"credit": Decimal("0"), "debit": Decimal("0")})

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
            cur_bal += actions[dt]["credit"]
            cur_bal -= actions[dt]["debit"]
            if cur_bal < min_obs:
                min_obs = cur_bal

        is_safe = (min_obs >= minimum_balance_to_keep)
        return is_safe, min_obs


loader = DataLoader(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "dataset")))
c = loader.load_all()
forecaster = ConfirmedIncomeForecaster(c.rate_table)
ranker = PlanRanker()
engine = DecisionEngine(forecaster, ranker)

import pandas as pd
df_samples = pd.read_csv(os.path.join(os.path.dirname(__file__), "..", "..", "dataset", "sample_requests.csv"))

print("Testing ConfirmedIncomeForecaster on all 25 samples:")
for req in c.sample_requests:
    u = req.user_id
    prof = c.profiles_by_user_id[u]
    evs = c.events_by_user_id[u]
    opts = c.payment_options_by_request_id.get(req.request_id, [])
    reconciled = [ReconciledEvent(e, e) for e in evs]
    trace = engine.evaluate_request(req, prof, reconciled, opts)

    row = df_samples[df_samples['request_id'] == req.request_id].iloc[0]
    exp_safe = str(row['amount_safe_to_pay'])
    gen_safe = str(trace.amount_safe_to_pay)
    print(f"[{req.request_id}] Safe: Gen={gen_safe:<12} Exp={exp_safe:<12} | Status: Gen={trace.affordability_status:<16} Exp={row['affordability_status']}")
