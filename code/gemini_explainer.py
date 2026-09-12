"""
Gemini Grounded Explanation Generator for Buy or Wait? Financial Decision Agent.
Produces concise, grounded natural-language explanations strictly from pre-computed DecisionTrace facts.
Includes deterministic fallback generation on any API failure, timeout, or malformed response.
"""

import os
import time
from decimal import Decimal
from typing import Optional, Dict, Any, List

from models import Request, UserProfile, DecisionTrace, PlanCandidate
from instrumentation import logger as global_logger, GeminiLogger

try:
    from google import genai
    from google.genai import types
    _HAS_GENAI = True
except ImportError:
    _HAS_GENAI = False


def _format_money(amount: Decimal, currency: str) -> str:
    """Format money string without trailing .00 if whole, or with 2 decimals."""
    if amount == amount.to_integral():
        return f"{currency} {amount:,.0f}"
    return f"{currency} {amount:,.2f}"


class GeminiExplainer:
    """Generates grounded financial explanations using Gemini with deterministic fallback."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: str = "gemini-2.5-flash",
        logger: Optional[GeminiLogger] = None
    ):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self.model_name = model_name
        self.logger = logger or global_logger
        self.client = None
        if _HAS_GENAI and self.api_key:
            try:
                self.client = genai.Client(api_key=self.api_key)
            except Exception as e:
                print(f"[GeminiExplainer Warning] Client initialization failed: {e}")

    def generate_explanation(
        self,
        request: Request,
        profile: UserProfile,
        trace: DecisionTrace,
        winning_plan: Optional[PlanCandidate] = None
    ) -> str:
        """
        Generate natural language explanation.
        Falls back to deterministic template on any failure.
        """
        fallback_text = self.generate_fallback_explanation(request, profile, trace, winning_plan)

        if not self.client:
            return fallback_text

        prompt = self._build_prompt(request, profile, trace, winning_plan)
        start_time = time.time()

        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    max_output_tokens=150,
                )
            )
            duration_ms = (time.time() - start_time) * 1000

            text = response.text.strip() if (response and response.text) else ""
            # Strip enclosing quotes if model added them
            if text.startswith('"') and text.endswith('"'):
                text = text[1:-1].strip()

            in_tok = 0
            out_tok = 0
            if hasattr(response, "usage_metadata") and response.usage_metadata:
                in_tok = getattr(response.usage_metadata, "prompt_token_count", 0) or 0
                out_tok = getattr(response.usage_metadata, "candidates_token_count", 0) or 0

            self.logger.log_call(
                purpose="explanation_generation",
                model_name=self.model_name,
                request_id=request.request_id,
                input_tokens=in_tok,
                output_tokens=out_tok,
                success=bool(text),
                duration_ms=duration_ms
            )

            if not text:
                return fallback_text

            # Basic validation of explanation quality
            if len(text) < 10 or "python" in text.lower() or "binary search" in text.lower():
                return fallback_text

            return text

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            self.logger.log_call(
                purpose="explanation_generation",
                model_name=self.model_name,
                request_id=request.request_id,
                input_tokens=0,
                output_tokens=0,
                success=False,
                error_msg=str(e),
                duration_ms=duration_ms
            )
            return fallback_text

    def _build_prompt(
        self,
        request: Request,
        profile: UserProfile,
        trace: DecisionTrace,
        winning_plan: Optional[PlanCandidate]
    ) -> str:
        curr = profile.home_currency
        req_amt_str = _format_money(request.requested_amount, curr)
        safe_amt_str = _format_money(trace.amount_safe_to_pay, curr)
        min_keep_str = _format_money(profile.minimum_balance_to_keep, curr)
        cur_bal_str = _format_money(profile.current_available_balance, curr)

        lines = [
            "You are a helpful and precise financial advisor explaining an already-computed financial decision.",
            "Explain the decision in 1-2 concise, natural sentences based strictly on the facts provided.",
            "Do NOT include internal code names, candidate IDs, or algorithms. Mention exact dates and money amounts where helpful.",
            "",
            "FACTS:",
            f"- User Home Currency: {curr}",
            f"- Request: {req_amt_str} for {request.request_type} on {request.request_date} (desired completion: {request.desired_completion_date})",
            f"- User's Available Balance: {cur_bal_str}, Minimum Balance Protected: {min_keep_str}",
            f"- Amount Safe Today: {safe_amt_str}",
            f"- Affordability Status: {trace.affordability_status}",
            f"- Recommended Method: {trace.recommended_payment_method}",
            f"- Payment Plan: {trace.payment_plan}",
            f"- Earliest Date for Full Payment: {trace.earliest_date_for_full_payment}",
            f"- Spending Changes Needed: {trace.spending_changes_needed}",
            "",
            "Instructions:",
            "- If affordable_now (full payment): Explain that paying today is safe and preserves the minimum balance.",
            "- If installments: Mention the number of installments, installment amount, start date, and that the minimum balance is kept safe.",
            "- If partial_payment: Mention paying the safe amount today and the remaining amount on the specified earliest date.",
            "- If wait: State to wait until the earliest date for full payment and note that paying earlier puts the minimum balance at risk.",
            "- If not_affordable: Explain that the payment cannot be safely completed by the desired date without risking the minimum balance.",
            "- If spending changes are needed: Mention stopping or reducing the specific flexible expenses first.",
            "",
            "Output ONLY the 1-2 sentence plain explanation text without any markdown or quotation marks."
        ]
        return "\n".join(lines)

    def generate_fallback_explanation(
        self,
        request: Request,
        profile: UserProfile,
        trace: DecisionTrace,
        winning_plan: Optional[PlanCandidate] = None
    ) -> str:
        """
        Deterministic, mathematically grounded fallback explanation generator.
        """
        curr = profile.home_currency
        req_amt_str = _format_money(request.requested_amount, curr)
        min_keep_str = _format_money(profile.minimum_balance_to_keep, curr)
        safe_amt_str = _format_money(trace.amount_safe_to_pay, curr)
        method = trace.recommended_payment_method
        status = trace.affordability_status
        due_date = request.desired_completion_date.isoformat()
        req_date = request.request_date.isoformat()
        earliest_dt = trace.earliest_date_for_full_payment.isoformat() if trace.earliest_date_for_full_payment else ""

        # 1. Full payment without spending changes
        if method == "full_payment" and status == "affordable_now":
            return f"Pay {req_amt_str} today. This leaves at least {min_keep_str} available over the next 90 days."

        # 2. Full payment with spending changes
        if method == "full_payment" and trace.spending_changes_needed != "none":
            changes_desc = self._describe_spending_changes(trace.spending_changes_needed, curr)
            return f"{changes_desc}, then pay {req_amt_str} today. This leaves at least {min_keep_str} available."

        # 3. Installments
        if method == "installments" and winning_plan and winning_plan.payments:
            n_inst = len(winning_plan.payments)
            inst_amt = _format_money(winning_plan.payments[0][1], curr)
            start_dt = winning_plan.payments[0][0].isoformat()
            return f"Use {n_inst} installments of {inst_amt}, starting {start_dt}. This leaves at least {min_keep_str} available."

        # 4. Partial payment
        if method == "partial_payment":
            rem_amt = request.requested_amount - trace.amount_safe_to_pay
            rem_amt_str = _format_money(rem_amt, curr)
            return (
                f"Pay {safe_amt_str} today and the remaining {rem_amt_str} on {earliest_dt}. "
                f"This completes the full request and keeps the {min_keep_str} minimum protected."
            )

        # 5. Wait
        if method == "wait":
            return f"Pay {req_amt_str} in full on {earliest_dt}. Paying earlier would take the balance below the {min_keep_str} minimum."

        # 6. Not recommended
        if trace.amount_safe_to_pay > Decimal("0"):
            return (
                f"Do not proceed with the {req_amt_str} request. "
                f"Although {safe_amt_str} is available today, the full amount cannot be completed safely within 90 days."
            )
        else:
            return f"Do not make this payment by {due_date}. None of the available options keeps the {min_keep_str} minimum protected."

    def _describe_spending_changes(self, spending_changes_str: str, currency: str) -> str:
        """Helper to format spending changes description."""
        parts = spending_changes_str.split("|")
        actions = []
        for p in parts:
            if p.startswith("stop:"):
                ev_id = p.split(":")[1]
                actions.append(f"stop the recurring expense ({ev_id})")
            elif p.startswith("reduce_to:"):
                ev_id = p.split(":")[1]
                new_amt = Decimal(p.split(":")[2])
                actions.append(f"reduce expense ({ev_id}) to {_format_money(new_amt, currency)}")
        if not actions:
            return "Adjust flexible spending"
        if len(actions) == 1:
            return actions[0].capitalize()
        return " and ".join(actions).capitalize()
