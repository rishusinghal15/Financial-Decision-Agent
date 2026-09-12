"""
Strict Output Validator for Buy or Wait? Financial Decision Agent.
Validates all final output rows and complete datasets against the challenge contract.
"""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Any, Tuple

from models import Request, DecisionTrace, PaymentOption

REQUIRED_COLUMNS = [
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
]

VALID_STATUSES = {
    "affordable_now",
    "affordable_with_plan",
    "affordable_later",
    "not_affordable",
}

VALID_METHODS = {
    "full_payment",
    "partial_payment",
    "installments",
    "wait",
    "not_recommended",
}

DATE_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ValidationError(Exception):
    """Raised when an output row fails validation."""
    pass


class OutputValidator:
    """Validates output rows and complete datasets."""

    @staticmethod
    def validate_row(
        row: Dict[str, Any],
        request: Request,
        trace: Optional[DecisionTrace] = None,
        payment_options: Optional[List[PaymentOption]] = None
    ) -> List[str]:
        """
        Validates a single output row dictionary against all specification rules.
        Returns a list of error message strings (empty list means 100% valid).
        """
        errors: List[str] = []

        # 1. Column presence
        for col in REQUIRED_COLUMNS:
            if col not in row:
                errors.append(f"Missing required column: '{col}'")

        # 2. request_id match
        req_id = str(row.get("request_id", "")).strip()
        if req_id != request.request_id:
            errors.append(f"request_id mismatch: expected '{request.request_id}', got '{req_id}'")

        # 3. amount_safe_to_pay validation
        raw_safe = row.get("amount_safe_to_pay")
        safe_amt = None
        try:
            safe_amt = Decimal(str(raw_safe))
            if safe_amt.is_nan() or safe_amt.is_infinite():
                errors.append(f"amount_safe_to_pay is NaN or Infinite: {raw_safe}")
            elif safe_amt < Decimal("0"):
                errors.append(f"amount_safe_to_pay cannot be negative: {safe_amt}")
            elif safe_amt > request.requested_amount:
                errors.append(f"amount_safe_to_pay ({safe_amt}) exceeds requested_amount ({request.requested_amount})")
        except (InvalidOperation, TypeError, ValueError):
            errors.append(f"amount_safe_to_pay is not a valid Decimal: {raw_safe}")

        # 4. affordability_status validation
        status = str(row.get("affordability_status", "")).strip()
        if status not in VALID_STATUSES:
            errors.append(f"Invalid affordability_status: '{status}'")

        # 5. recommended_payment_method validation
        method = str(row.get("recommended_payment_method", "")).strip()
        if method not in VALID_METHODS:
            errors.append(f"Invalid recommended_payment_method: '{method}'")

        # 6. Status & Method Consistency
        if status == "affordable_now":
            if method != "full_payment":
                errors.append(f"affordable_now must have recommended_payment_method='full_payment', got '{method}'")
        elif status == "affordable_later":
            if method != "wait":
                errors.append(f"affordable_later must have recommended_payment_method='wait', got '{method}'")
        elif status == "not_affordable":
            if method != "not_recommended":
                errors.append(f"not_affordable must have recommended_payment_method='not_recommended', got '{method}'")
        elif status == "affordable_with_plan":
            if method not in ("partial_payment", "installments", "full_payment"):
                errors.append(f"affordable_with_plan must have method in ('partial_payment', 'installments', 'full_payment'), got '{method}'")

        # 7. earliest_date_for_full_payment validation
        raw_earliest = row.get("earliest_date_for_full_payment")
        earliest_dt = None
        earliest_str_clean = str(raw_earliest).strip() if raw_earliest is not None else ""
        if (
            raw_earliest is not None
            and earliest_str_clean
            and earliest_str_clean.lower() not in ("none", "nan", "")
        ):
            if not DATE_REGEX.match(earliest_str_clean):
                errors.append(f"earliest_date_for_full_payment is not valid ISO YYYY-MM-DD: '{earliest_str_clean}'")
            else:
                try:
                    earliest_dt = date.fromisoformat(earliest_str_clean)
                except ValueError:
                    errors.append(f"earliest_date_for_full_payment is invalid date: '{earliest_str_clean}'")
        else:
            earliest_dt = None

        if status == "affordable_now":
            if earliest_dt != request.request_date:
                errors.append(
                    f"For affordable_now, earliest_date_for_full_payment must equal request_date ({request.request_date}), got '{earliest_dt}'"
                )

        # 8. payment_plan validation
        plan_str = str(row.get("payment_plan", "")).strip()
        if method == "not_recommended":
            if plan_str != "none":
                errors.append(f"For not_recommended, payment_plan must be 'none', got '{plan_str}'")
        else:
            if not plan_str or plan_str == "none":
                errors.append(f"payment_plan cannot be 'none' for method '{method}'")
            else:
                parsed_payments = []
                chunks = plan_str.split("|")
                for chunk in chunks:
                    if ":" not in chunk:
                        errors.append(f"Malformed payment item in plan: '{chunk}'")
                        continue
                    p_dt_str, p_amt_str = chunk.split(":", 1)
                    p_dt_str = p_dt_str.strip()
                    p_amt_str = p_amt_str.strip()
                    if not DATE_REGEX.match(p_dt_str):
                        errors.append(f"Invalid date in payment item: '{p_dt_str}'")
                        continue
                    try:
                        p_dt = date.fromisoformat(p_dt_str)
                        p_amt = Decimal(p_amt_str)
                        if p_amt < Decimal("0"):
                            errors.append(f"Payment amount cannot be negative: {p_amt}")
                        parsed_payments.append((p_dt, p_amt))
                    except (ValueError, InvalidOperation):
                        errors.append(f"Invalid amount or date in payment plan: '{chunk}'")

                # Check chronological order
                for i in range(1, len(parsed_payments)):
                    if parsed_payments[i][0] < parsed_payments[i - 1][0]:
                        errors.append(f"Payment plan is not strictly chronological: {parsed_payments[i-1][0]} > {parsed_payments[i][0]}")

                # Method specific plan rules
                if method == "full_payment":
                    if len(parsed_payments) != 1:
                        errors.append(f"full_payment must contain exactly 1 payment, got {len(parsed_payments)}")
                    elif parsed_payments[0][0] != request.request_date:
                        errors.append(f"full_payment date must be request_date ({request.request_date}), got {parsed_payments[0][0]}")
                    elif parsed_payments[0][1] != request.requested_amount:
                        errors.append(f"full_payment amount ({parsed_payments[0][1]}) must equal requested_amount ({request.requested_amount})")

                elif method == "wait":
                    if len(parsed_payments) != 1:
                        errors.append(f"wait must contain exactly 1 future payment, got {len(parsed_payments)}")
                    else:
                        w_dt, w_amt = parsed_payments[0]
                        if earliest_dt and w_dt != earliest_dt:
                            errors.append(f"wait payment date ({w_dt}) must equal earliest_date_for_full_payment ({earliest_dt})")
                        if w_dt > request.desired_completion_date:
                            errors.append(f"wait payment date ({w_dt}) exceeds desired_completion_date ({request.desired_completion_date})")
                        if w_amt != request.requested_amount:
                            errors.append(f"wait payment amount ({w_amt}) must equal requested_amount ({request.requested_amount})")

                elif method == "partial_payment":
                    if len(parsed_payments) != 2:
                        errors.append(f"partial_payment must contain exactly 2 payments, got {len(parsed_payments)}")
                    else:
                        p1_dt, p1_amt = parsed_payments[0]
                        p2_dt, p2_amt = parsed_payments[1]
                        if p1_dt != request.request_date:
                            errors.append(f"partial payment 1 date must be request_date ({request.request_date}), got {p1_dt}")
                        if safe_amt is not None and p1_amt != safe_amt:
                            errors.append(f"partial payment 1 amount ({p1_amt}) must equal amount_safe_to_pay ({safe_amt})")
                        if earliest_dt and p2_dt != earliest_dt:
                            errors.append(f"partial payment 2 date ({p2_dt}) must equal earliest_date_for_full_payment ({earliest_dt})")
                        if p2_dt > request.desired_completion_date:
                            errors.append(f"partial payment 2 date ({p2_dt}) exceeds desired_completion_date ({request.desired_completion_date})")
                        if p1_amt + p2_amt != request.requested_amount:
                            errors.append(f"partial payments sum ({p1_amt + p2_amt}) must equal requested_amount ({request.requested_amount})")

        # 9. spending_changes_needed validation
        sc_str = str(row.get("spending_changes_needed", "")).strip()
        if sc_str != "none":
            sc_items = sc_str.split("|")
            if len(sc_items) > 3:
                errors.append(f"spending_changes_needed has {len(sc_items)} changes, maximum permitted is 3")
            target_ids = set()
            for sc_item in sc_items:
                if sc_item.startswith("stop:"):
                    parts = sc_item.split(":")
                    if len(parts) != 2 or not parts[1]:
                        errors.append(f"Malformed stop spending change: '{sc_item}'")
                    else:
                        tid = parts[1]
                        if tid in target_ids:
                            errors.append(f"Duplicate target event in spending changes: '{tid}'")
                        target_ids.add(tid)
                elif sc_item.startswith("reduce_to:"):
                    parts = sc_item.split(":")
                    if len(parts) != 3 or not parts[1] or not parts[2]:
                        errors.append(f"Malformed reduce_to spending change: '{sc_item}'")
                    else:
                        tid = parts[1]
                        if tid in target_ids:
                            errors.append(f"Duplicate target event in spending changes: '{tid}'")
                        target_ids.add(tid)
                        try:
                            r_amt = Decimal(parts[2])
                            if r_amt < Decimal("0"):
                                errors.append(f"reduce_to amount cannot be negative: '{parts[2]}'")
                        except InvalidOperation:
                            errors.append(f"Invalid Decimal amount in reduce_to: '{parts[2]}'")
                else:
                    errors.append(f"Invalid spending change format: '{sc_item}' (must start with stop: or reduce_to:)")

        # 10. decision_explanation validation
        exp_text = str(row.get("decision_explanation", "")).strip()
        if not exp_text:
            errors.append("decision_explanation cannot be empty")
        elif len(exp_text) < 10:
            errors.append(f"decision_explanation is too short: '{exp_text}'")

        # 11. Consistency with DecisionTrace if provided
        if trace is not None:
            if status != trace.affordability_status:
                errors.append(f"Output status '{status}' disagrees with DecisionTrace '{trace.affordability_status}'")
            if method != trace.recommended_payment_method:
                errors.append(f"Output method '{method}' disagrees with DecisionTrace '{trace.recommended_payment_method}'")
            if plan_str != trace.payment_plan:
                errors.append(f"Output plan '{plan_str}' disagrees with DecisionTrace '{trace.payment_plan}'")
            if sc_str != trace.spending_changes_needed:
                errors.append(f"Output spending changes '{sc_str}' disagrees with DecisionTrace '{trace.spending_changes_needed}'")

        return errors

    @staticmethod
    def validate_dataset(rows: Any, expected_requests: List[Request]) -> List[str]:
        """
        Validates complete dataset output rows.
        Checks row count, unique IDs, order, and per-row validation.
        """
        if hasattr(rows, "to_dict"):
            rows = rows.to_dict(orient="records")

        dataset_errors: List[str] = []

        if len(rows) != len(expected_requests):
            dataset_errors.append(f"Row count mismatch: expected {len(expected_requests)} rows, got {len(rows)}")

        seen_ids = set()
        req_map = {r.request_id: r for r in expected_requests}

        for i, row in enumerate(rows):
            req_id = row.get("request_id")
            if not req_id:
                dataset_errors.append(f"Row {i} is missing request_id")
                continue
            if req_id in seen_ids:
                dataset_errors.append(f"Duplicate request_id in dataset: '{req_id}'")
            seen_ids.add(req_id)

            if req_id not in req_map:
                dataset_errors.append(f"Unknown request_id in output: '{req_id}'")
                continue

            req_obj = req_map[req_id]
            row_errors = OutputValidator.validate_row(row, req_obj)
            for err in row_errors:
                dataset_errors.append(f"[{req_id}] {err}")

        # Check for missing request IDs
        for expected_req in expected_requests:
            if expected_req.request_id not in seen_ids:
                dataset_errors.append(f"Missing expected request_id in output: '{expected_req.request_id}'")

        return dataset_errors
