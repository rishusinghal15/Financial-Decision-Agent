"""
Deterministic Conflict Resolver for Buy or Wait? Financial Decision Agent.
Implements 4-tier conflict resolution hierarchy to reconcile financial events with extracted facts.
"""

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
from typing import Dict, List, Optional, Tuple, Any

from models import (
    FinancialEvent,
    ExtractedFact,
    Message,
    UserProfile,
    ReconciledEvent,
)


def _parse_date_safe(date_str: str) -> Optional[date]:
    if not date_str or not date_str.strip():
        return None
    try:
        return date.fromisoformat(date_str.strip())
    except Exception:
        return None


def _parse_decimal_safe(val_str: str) -> Optional[Decimal]:
    if not val_str or not val_str.strip():
        return None
    try:
        # Clean currency symbols or commas if any slipped through
        cleaned = val_str.replace(",", "").replace("$", "").replace("€", "").replace("₹", "").strip()
        return Decimal(cleaned)
    except Exception:
        return None


class ConflictResolver:
    """
    Reconciles raw FinancialEvents against extracted AI facts using a 4-tier deterministic resolution hierarchy.
    
    4-Tier Priority:
    1. Explicit cancellation, settlement, or amendment from authoritative source.
    2. Newer record from the same source (latest timestamp wins).
    3. Settled event over estimate or forecast.
    4. Financially safer interpretation when unresolved (conservative bounds).
    """

    def __init__(self, user_profile: Optional[UserProfile] = None):
        self.user_profile = user_profile

    def reconcile_events(
        self,
        events: List[FinancialEvent],
        facts: List[ExtractedFact],
        messages: Optional[List[Message]] = None
    ) -> List[ReconciledEvent]:
        """
        Produce ReconciledEvent list preserving both original event data and provenance.
        """
        events_by_id: Dict[str, FinancialEvent] = {e.event_id: e for e in events}
        messages_by_id: Dict[str, Message] = {m.message_id: m for m in (messages or [])}

        # Step 1: Detect event associations for all facts (explicit & proximity-based)
        fact_targets: Dict[str, List[ExtractedFact]] = {}
        for fact in facts:
            target_id = self._detect_target_event_id(fact, events, messages_by_id)
            if target_id and target_id in events_by_id:
                fact_targets.setdefault(target_id, []).append(fact)

        # Step 2: For each event, apply 4-tier conflict resolution to select winning facts
        reconciled_list: List[ReconciledEvent] = []
        for event in events:
            ev_facts = fact_targets.get(event.event_id, [])
            if not ev_facts:
                # No amendments
                reconciled_list.append(
                    ReconciledEvent(
                        event=deepcopy(event),
                        original_event=event,
                        provenance=[],
                        is_cancelled=(event.status == "cancelled"),
                        is_amended=False
                    )
                )
                continue

            winning_facts, notes = self._resolve_conflicts_for_event(event, ev_facts, messages_by_id)
            amended_event = deepcopy(event)
            is_cancelled = (event.status == "cancelled")
            is_amended = False

            for wf in winning_facts:
                if wf.operation == "cancel" or (wf.field == "status" and wf.value.lower() == "cancelled"):
                    amended_event.status = "cancelled"
                    is_cancelled = True
                    is_amended = True
                elif wf.field == "amount":
                    parsed_amt = _parse_decimal_safe(wf.value)
                    if parsed_amt is not None:
                        amended_event.amount = parsed_amt
                        is_amended = True
                    if wf.currency:
                        amended_event.currency = wf.currency
                elif wf.field in ("settlement_date", "event_date"):
                    parsed_dt = _parse_date_safe(wf.value)
                    if parsed_dt is not None:
                        amended_event.settlement_date = parsed_dt
                        is_amended = True
                elif wf.field == "status":
                    amended_event.status = wf.value.lower().strip()
                    is_amended = True

            reconciled_list.append(
                ReconciledEvent(
                    event=amended_event,
                    original_event=event,
                    provenance=winning_facts,
                    is_cancelled=is_cancelled,
                    is_amended=is_amended,
                    conflict_notes=notes
                )
            )

        return reconciled_list

    def _detect_target_event_id(
        self,
        fact: ExtractedFact,
        events: List[FinancialEvent],
        messages_by_id: Dict[str, Message]
    ) -> Optional[str]:
        """
        Detect target event ID using explicit IDs or user/category/date proximity.
        """
        # A. Explicit fact event_id
        if fact.event_id and fact.event_id.strip():
            return fact.event_id.strip()

        # B. Message related_event_id
        msg = messages_by_id.get(fact.source)
        if msg and msg.related_event_id:
            return msg.related_event_id.strip()

        # C. Proximity matching for unlinked facts
        if msg:
            msg_text_lower = msg.message_text.lower()
            eff_date = _parse_date_safe(fact.effective_date) or msg.sent_at.date()

            # Category mapping heuristics
            target_categories = set()
            if "payroll" in msg_text_lower or "salary" in msg_text_lower or "gaji" in msg_text_lower:
                target_categories.add("salary")
            if "streaming" in msg_text_lower:
                target_categories.add("streaming")
            if "backup" in msg_text_lower or "cloud" in msg_text_lower:
                target_categories.add("cloud_storage")
            if "rent" in msg_text_lower or "apartment" in msg_text_lower:
                target_categories.add("rent")
            if "food" in msg_text_lower or "delivery" in msg_text_lower:
                target_categories.add("delivery_membership")

            # Find matching events in same user list
            candidates = [
                e for e in events
                if (not target_categories or e.category in target_categories)
            ]

            if candidates:
                # Pick the event with closest date to eff_date
                candidates.sort(key=lambda e: abs(((e.settlement_date or e.event_date) - eff_date).days))
                return candidates[0].event_id

        return None

    def _resolve_conflicts_for_event(
        self,
        event: FinancialEvent,
        facts: List[ExtractedFact],
        messages_by_id: Dict[str, Message]
    ) -> Tuple[List[ExtractedFact], Optional[str]]:
        """
        Apply 4-Tier conflict resolution across multiple facts targeting the same event.
        """
        if len(facts) == 1:
            return facts, None

        # Group facts by field
        by_field: Dict[str, List[ExtractedFact]] = {}
        for f in facts:
            by_field.setdefault(f.field, []).append(f)

        winning_facts: List[ExtractedFact] = []
        notes = []

        for field_name, field_facts in by_field.items():
            if len(field_facts) == 1:
                winning_facts.append(field_facts[0])
                continue

            # Tier 1: Explicit cancellation over amendment
            cancels = [f for f in field_facts if f.operation == "cancel"]
            if cancels:
                winning_facts.append(cancels[-1])
                notes.append(f"Tier 1: cancellation applied for field {field_name}")
                continue

            # Tier 2: Newer record from same source (latest timestamp)
            dated_facts = []
            for f in field_facts:
                msg = messages_by_id.get(f.source)
                ts = msg.sent_at if msg else datetime.min
                dated_facts.append((ts, f))

            dated_facts.sort(key=lambda x: x[0], reverse=True)
            if dated_facts[0][0] > dated_facts[1][0]:
                winning_facts.append(dated_facts[0][1])
                notes.append(f"Tier 2: latest record from {dated_facts[0][1].source} selected")
                continue

            # Tier 3: Settled event over estimate/forecast
            if event.status == "settled":
                # Keep original or settled fact
                winning_facts.append(dated_facts[0][1])
                notes.append(f"Tier 3: settled baseline preserved")
                continue

            # Tier 4: Financially safer interpretation
            if field_name == "amount":
                # For income: choose lower amount (safer)
                # For expense: choose higher amount (safer)
                parsed_facts = []
                for _, f in dated_facts:
                    val = _parse_decimal_safe(f.value)
                    if val is not None:
                        parsed_facts.append((val, f))

                if parsed_facts:
                    if event.direction == "credit":
                        # Lower income is safer
                        parsed_facts.sort(key=lambda x: x[0])
                    else:
                        # Higher expense is safer
                        parsed_facts.sort(key=lambda x: x[0], reverse=True)
                    winning_facts.append(parsed_facts[0][1])
                    notes.append(f"Tier 4: financially safer value {parsed_facts[0][0]} selected")
                    continue

            # Default fallback: latest
            winning_facts.append(dated_facts[0][1])

        return winning_facts, "; ".join(notes) if notes else None

    # Alias for pipeline compatibility
    resolve_conflicts = reconcile_events

