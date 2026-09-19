"""
Six-Key Deterministic Ranker for Buy or Wait? Financial Decision Agent.
Implements the exact 6-key comparison hierarchy specified in the challenge rules.
"""

from datetime import date
from decimal import Decimal
from typing import List, Optional, Tuple

from models import PlanCandidate


class PlanRanker:
    """
    Ranks safe eligible candidate payment plans using the locked 6-key hierarchy.

    Architectural Invariant:
    Safety validation is a hard gate. Ranking operates ONLY on candidates that have
    already been validated as safe (maintaining the minimum balance invariant across
    the forecast horizon). Unsafe candidates are discarded before ranking begins.

    Among safe candidates, deadline completion is prioritized first because completing
    the requested expense within its required deadline is a primary feasibility objective.
    The remaining keys progressively prefer fewer lifestyle changes, lower cost, earlier
    payment, fewer payments, and deterministic tie-breaking:

    1. Complete the full request by desired_completion_date (True before False).
    2. Require no spending changes (0 changes before 1, 2, 3).
    3. Minimize total amount paid (lowest total_cost first).
    4. Start payment earlier (earliest first_payment_date first).
    5. Use fewer payments (fewer payments first).
    6. Lowest payment_option_id as final tie-breaker (lexicographical order).
    """

    @staticmethod
    def get_ranking_key(candidate: PlanCandidate) -> Tuple[int, int, Decimal, date, int, str]:
        # Key 1: Completes by desired completion date (0 = True, 1 = False)
        k1 = 0 if candidate.completes_by_deadline else 1

        # Key 2: Number of spending changes needed (0 = none, 1, 2, 3)
        k2 = len(candidate.spending_changes)

        # Key 3: Total amount paid (Decimal)
        k3 = candidate.total_cost

        # Key 4: First payment date (date)
        first_date = candidate.payments[0][0] if candidate.payments else date.max
        k4 = first_date

        # Key 5: Number of payments (int)
        k5 = len(candidate.payments)

        # Key 6: Lowest payment_option_id (tie-breaker)
        # Note: If no payment_option_id (e.g. full/partial/wait), use empty string
        k6 = candidate.payment_option_id or ""

        return (k1, k2, k3, k4, k5, k6)

    def rank_candidates(self, candidates: List[PlanCandidate]) -> List[PlanCandidate]:
        """
        Sort safe eligible candidates according to the 6-key hierarchy.
        """
        return sorted(candidates, key=self.get_ranking_key)

    def select_best_candidate(self, candidates: List[PlanCandidate]) -> Optional[PlanCandidate]:
        """
        Select winning candidate plan. Returns None if candidate list is empty.
        """
        if not candidates:
            return None
        ranked = self.rank_candidates(candidates)
        return ranked[0]
