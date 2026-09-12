"""
Data Models for Buy or Wait? Financial Decision Agent.
All models use Python dataclasses, explicit date types, and Decimal for monetary amounts.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List, Dict, Any, Tuple


@dataclass
class UserProfile:
    user_id: str
    home_currency: str
    current_available_balance: Decimal
    minimum_balance_to_keep: Decimal
    financial_priorities: List[str]
    expense_categories_to_protect: List[str]
    expense_categories_user_is_willing_to_reduce: List[str]
    expense_categories_user_is_willing_to_stop: List[str]
    payment_methods_user_will_consider: List[str]
    max_installment_months: Optional[int] = None


@dataclass
class FinancialEvent:
    event_id: str
    user_id: str
    event_type: str  # 'expense', 'subscription', 'income', 'debt_payment', 'investment_purchase', 'refund', 'investment_valuation', 'investment_sale'
    description: str
    category: str
    direction: str   # 'debit', 'credit', 'non_cash'
    amount: Optional[Decimal]  # None if blank in CSV (image-backed), NEVER 0!
    currency: str
    event_date: date
    settlement_date: Optional[date]
    status: str      # 'settled', 'pending', 'scheduled', 'cancelled', 'failed', 'unrealized'
    linked_event_id: Optional[str] = None
    flexibility: str = "fixed"  # 'fixed', 'reducible', 'stoppable', 'reducible_or_stoppable'
    minimum_allowed_amount: Optional[Decimal] = None


@dataclass
class PaymentOption:
    payment_option_id: str
    request_id: str
    payment_method: str  # 'full_payment', 'installments'
    payment_amount: Decimal
    number_of_payments: int
    first_payment_date: date
    payment_frequency_days: Optional[int]
    financing_fee: Decimal
    total_payable_amount: Decimal


@dataclass
class Request:
    request_id: str
    user_id: str
    request_date: date
    request_type: str
    requested_amount: Decimal
    desired_completion_date: date
    allows_partial_payment: bool
    request_text: str
    # Ground truth fields for sample_requests.csv:
    ground_truth_amount_safe_to_pay: Optional[Decimal] = None
    ground_truth_affordability_status: Optional[str] = None
    ground_truth_recommended_payment_method: Optional[str] = None
    ground_truth_payment_plan: Optional[str] = None
    ground_truth_earliest_date_for_full_payment: Optional[date] = None
    ground_truth_spending_changes_needed: Optional[str] = None
    ground_truth_decision_explanation: Optional[str] = None


@dataclass
class Message:
    message_id: str
    user_id: str
    request_id: Optional[str]
    related_event_id: Optional[str]
    sent_at: datetime
    source_type: str  # 'employer', 'service_provider', 'financial_service', 'bank', 'merchant'
    message_text: str


@dataclass
class ImageMapping:
    image_id: str
    user_id: str
    request_id: str
    related_event_id: str
    file_path: str


@dataclass
class ExchangeRate:
    rate_date: date
    from_currency: str
    to_currency: str
    rate: Decimal


@dataclass
class ExtractedFact:
    """Provenance-preserving structured fact extracted from message or image."""
    event_id: str
    operation: str  # 'amend', 'cancel', 'confirm', 'new'
    field: str
    value: str
    currency: str
    effective_date: str
    source: str


@dataclass
class PlanCandidate:
    payment_method: str  # 'full_payment', 'partial_payment', 'installments', 'wait', 'not_recommended'
    payment_plan_str: str  # e.g. "2024-03-03:25256" or "none"
    payments: List[Tuple[date, Decimal]]
    total_cost: Decimal
    spending_changes: List[str]  # e.g. ["stop:event_14", "reduce_to:event_21:100"]
    is_safe: bool
    completion_date: Optional[date]
    completes_by_deadline: bool
    payment_option_id: Optional[str] = None
    rejection_reason: Optional[str] = None


@dataclass
class ReconciledEvent:
    """Represents a financial event after applying reconciled facts while preserving original data."""
    event: FinancialEvent
    original_event: FinancialEvent
    provenance: List[ExtractedFact] = field(default_factory=list)
    is_cancelled: bool = False
    is_amended: bool = False
    conflict_notes: Optional[str] = None


@dataclass
class DecisionTrace:
    request_id: str
    amount_safe_to_pay: Decimal
    affordability_status: str
    recommended_payment_method: str
    payment_plan: str
    earliest_date_for_full_payment: Optional[date]
    spending_changes_needed: str
    selected_plan: Optional[PlanCandidate] = None
    all_candidates: List[PlanCandidate] = field(default_factory=list)
    explanation_facts: Dict[str, Any] = field(default_factory=dict)

