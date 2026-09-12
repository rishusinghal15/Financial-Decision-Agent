"""
Data Loader for Buy or Wait? Financial Decision Agent.
Loads all dataset CSVs deterministically, creates typed models, and builds lookup indexes.
"""

import csv
import os
from datetime import date, datetime
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from models import (
    UserProfile,
    FinancialEvent,
    PaymentOption,
    Request,
    Message,
    ImageMapping,
    ExchangeRate,
)
from currency_normalizer import ExchangeRateTable


def parse_date(date_str: str) -> Optional[date]:
    if not date_str or not date_str.strip():
        return None
    return date.fromisoformat(date_str.strip())


def parse_datetime(dt_str: str) -> Optional[datetime]:
    if not dt_str or not dt_str.strip():
        return None
    cleaned = dt_str.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    return datetime.fromisoformat(cleaned)


def parse_decimal(val_str: str) -> Optional[Decimal]:
    if val_str is None or not val_str.strip():
        return None
    return Decimal(val_str.strip())


def parse_int(val_str: str) -> Optional[int]:
    if val_str is None or not val_str.strip():
        return None
    return int(val_str.strip())


def parse_pipe_list(val_str: str) -> List[str]:
    if not val_str or not val_str.strip():
        return []
    return [item.strip() for item in val_str.strip().split("|") if item.strip()]


class DatasetContainer:
    """Container holding loaded records and pre-indexed lookups."""

    def __init__(
        self,
        requests: List[Request],
        sample_requests: List[Request],
        profiles: List[UserProfile],
        events: List[FinancialEvent],
        payment_options: List[PaymentOption],
        exchange_rates: List[ExchangeRate],
        messages: List[Message],
        images: List[ImageMapping],
        rate_table: ExchangeRateTable,
    ):
        self.requests = requests
        self.sample_requests = sample_requests
        self.profiles = profiles
        self.events = events
        self.payment_options = payment_options
        self.exchange_rates = exchange_rates
        self.messages = messages
        self.images = images
        self.rate_table = rate_table

        # Pre-build deterministic indexes
        self.requests_by_id: Dict[str, Request] = {r.request_id: r for r in requests}
        self.sample_requests_by_id: Dict[str, Request] = {r.request_id: r for r in sample_requests}
        self.profiles_by_user_id: Dict[str, UserProfile] = {p.user_id: p for p in profiles}
        
        self.events_by_id: Dict[str, FinancialEvent] = {e.event_id: e for e in events}
        self.events_by_user_id: Dict[str, List[FinancialEvent]] = {}
        for e in events:
            self.events_by_user_id.setdefault(e.user_id, []).append(e)

        self.payment_options_by_request_id: Dict[str, List[PaymentOption]] = {}
        for opt in payment_options:
            self.payment_options_by_request_id.setdefault(opt.request_id, []).append(opt)

        self.messages_by_user_id: Dict[str, List[Message]] = {}
        self.messages_by_request_id: Dict[str, List[Message]] = {}
        self.messages_by_event_id: Dict[str, List[Message]] = {}
        for m in messages:
            self.messages_by_user_id.setdefault(m.user_id, []).append(m)
            if m.request_id:
                self.messages_by_request_id.setdefault(m.request_id, []).append(m)
            if m.related_event_id:
                self.messages_by_event_id.setdefault(m.related_event_id, []).append(m)

        self.images_by_user_id: Dict[str, List[ImageMapping]] = {}
        self.images_by_request_id: Dict[str, List[ImageMapping]] = {}
        self.images_by_event_id: Dict[str, List[ImageMapping]] = {}
        for img in images:
            self.images_by_user_id.setdefault(img.user_id, []).append(img)
            if img.request_id:
                self.images_by_request_id.setdefault(img.request_id, []).append(img)
            if img.related_event_id:
                self.images_by_event_id.setdefault(img.related_event_id, []).append(img)


class DataLoader:
    """Loads all CSV datasets and constructs a DatasetContainer."""

    def __init__(self, dataset_dir: str):
        self.dataset_dir = os.path.abspath(dataset_dir)

    def load_all(self) -> DatasetContainer:
        requests = self.load_requests("requests.csv")
        sample_requests = self.load_sample_requests("sample_requests.csv")
        profiles = self.load_financial_profiles("financial_profiles.csv")
        events = self.load_financial_events("financial_events.csv")
        payment_options = self.load_payment_options("request_payment_options.csv")
        exchange_rates, rate_table = self.load_exchange_rates("exchange_rates.csv")
        messages = self.load_messages("messages.csv")
        images = self.load_images("images.csv")

        return DatasetContainer(
            requests=requests,
            sample_requests=sample_requests,
            profiles=profiles,
            events=events,
            payment_options=payment_options,
            exchange_rates=exchange_rates,
            messages=messages,
            images=images,
            rate_table=rate_table,
        )

    def _read_csv(self, filename: str) -> List[Dict[str, str]]:
        filepath = os.path.join(self.dataset_dir, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return list(reader)

    def load_requests(self, filename: str = "requests.csv") -> List[Request]:
        rows = self._read_csv(filename)
        requests = []
        for r in rows:
            req = Request(
                request_id=r["request_id"].strip(),
                user_id=r["user_id"].strip(),
                request_date=parse_date(r["request_date"]),
                request_type=r["request_type"].strip(),
                requested_amount=parse_decimal(r["requested_amount"]),
                desired_completion_date=parse_date(r["desired_completion_date"]),
                allows_partial_payment=r["allows_partial_payment"].strip().lower() == "true",
                request_text=r["request_text"].strip(),
            )
            requests.append(req)
        return requests

    def load_sample_requests(self, filename: str = "sample_requests.csv") -> List[Request]:
        rows = self._read_csv(filename)
        samples = []
        for r in rows:
            req = Request(
                request_id=r["request_id"].strip(),
                user_id=r["user_id"].strip(),
                request_date=parse_date(r["request_date"]),
                request_type=r["request_type"].strip(),
                requested_amount=parse_decimal(r["requested_amount"]),
                desired_completion_date=parse_date(r["desired_completion_date"]),
                allows_partial_payment=r["allows_partial_payment"].strip().lower() == "true",
                request_text=r["request_text"].strip(),
                ground_truth_amount_safe_to_pay=parse_decimal(r.get("amount_safe_to_pay")),
                ground_truth_affordability_status=r.get("affordability_status", "").strip() or None,
                ground_truth_recommended_payment_method=r.get("recommended_payment_method", "").strip() or None,
                ground_truth_payment_plan=r.get("payment_plan", "").strip() or None,
                ground_truth_earliest_date_for_full_payment=parse_date(r.get("earliest_date_for_full_payment", "")),
                ground_truth_spending_changes_needed=r.get("spending_changes_needed", "").strip() or None,
                ground_truth_decision_explanation=r.get("decision_explanation", "").strip() or None,
            )
            samples.append(req)
        return samples

    def load_financial_profiles(self, filename: str = "financial_profiles.csv") -> List[UserProfile]:
        rows = self._read_csv(filename)
        profiles = []
        for r in rows:
            prof = UserProfile(
                user_id=r["user_id"].strip(),
                home_currency=r["home_currency"].strip().upper(),
                current_available_balance=parse_decimal(r["current_available_balance"]),
                minimum_balance_to_keep=parse_decimal(r["minimum_balance_to_keep"]),
                financial_priorities=parse_pipe_list(r.get("financial_priorities", "")),
                expense_categories_to_protect=parse_pipe_list(r.get("expense_categories_to_protect", "")),
                expense_categories_user_is_willing_to_reduce=parse_pipe_list(r.get("expense_categories_user_is_willing_to_reduce", "")),
                expense_categories_user_is_willing_to_stop=parse_pipe_list(r.get("expense_categories_user_is_willing_to_stop", "")),
                payment_methods_user_will_consider=parse_pipe_list(r.get("payment_methods_user_will_consider", "")),
                max_installment_months=parse_int(r.get("max_installment_months")),
            )
            profiles.append(prof)
        return profiles

    def load_financial_events(self, filename: str = "financial_events.csv") -> List[FinancialEvent]:
        rows = self._read_csv(filename)
        events = []
        for r in rows:
            linked_id = r.get("linked_event_id", "").strip() or None
            flexibility = r.get("flexibility", "fixed").strip() or "fixed"
            min_amt = parse_decimal(r.get("minimum_allowed_amount"))
            
            # NOTE: amount must be None if blank in CSV (image-backed), NEVER 0!
            amount = parse_decimal(r.get("amount"))

            ev = FinancialEvent(
                event_id=r["event_id"].strip(),
                user_id=r["user_id"].strip(),
                event_type=r["event_type"].strip(),
                description=r["description"].strip(),
                category=r["category"].strip(),
                direction=r["direction"].strip(),
                amount=amount,
                currency=r["currency"].strip().upper(),
                event_date=parse_date(r["event_date"]),
                settlement_date=parse_date(r.get("settlement_date")),
                status=r["status"].strip(),
                linked_event_id=linked_id,
                flexibility=flexibility,
                minimum_allowed_amount=min_amt,
            )
            events.append(ev)
        return events

    def load_payment_options(self, filename: str = "request_payment_options.csv") -> List[PaymentOption]:
        rows = self._read_csv(filename)
        options = []
        for r in rows:
            opt = PaymentOption(
                payment_option_id=r["payment_option_id"].strip(),
                request_id=r["request_id"].strip(),
                payment_method=r["payment_method"].strip(),
                payment_amount=parse_decimal(r["payment_amount"]),
                number_of_payments=int(r["number_of_payments"].strip()),
                first_payment_date=parse_date(r["first_payment_date"]),
                payment_frequency_days=parse_int(r.get("payment_frequency_days")),
                financing_fee=parse_decimal(r["financing_fee"]),
                total_payable_amount=parse_decimal(r["total_payable_amount"]),
            )
            options.append(opt)
        return options

    def load_exchange_rates(self, filename: str = "exchange_rates.csv") -> Tuple[List[ExchangeRate], ExchangeRateTable]:
        rows = self._read_csv(filename)
        rates = []
        table = ExchangeRateTable()
        for r in rows:
            rate = ExchangeRate(
                rate_date=parse_date(r["rate_date"]),
                from_currency=r["from_currency"].strip().upper(),
                to_currency=r["to_currency"].strip().upper(),
                rate=parse_decimal(r["rate"]),
            )
            rates.append(rate)
            table.add_rate(rate)
        return rates, table

    def load_messages(self, filename: str = "messages.csv") -> List[Message]:
        rows = self._read_csv(filename)
        messages = []
        for r in rows:
            req_id = r.get("request_id", "").strip() or None
            rel_ev_id = r.get("related_event_id", "").strip() or None
            msg = Message(
                message_id=r["message_id"].strip(),
                user_id=r["user_id"].strip(),
                request_id=req_id,
                related_event_id=rel_ev_id,
                sent_at=parse_datetime(r["sent_at"]),
                source_type=r["source_type"].strip(),
                message_text=r["message_text"].strip(),
            )
            messages.append(msg)
        return messages

    def load_images(self, filename: str = "images.csv") -> List[ImageMapping]:
        rows = self._read_csv(filename)
        images = []
        for r in rows:
            img_id = r["image_id"].strip()
            file_path = os.path.join(self.dataset_dir, "media", "images", f"{img_id}.png")
            img = ImageMapping(
                image_id=img_id,
                user_id=r["user_id"].strip(),
                request_id=r["request_id"].strip(),
                related_event_id=r["related_event_id"].strip(),
                file_path=file_path,
            )
            images.append(img)
        return images
