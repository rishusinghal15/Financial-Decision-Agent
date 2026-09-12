"""
Dated Currency Normalizer for Buy or Wait? Financial Decision Agent.
Strict deterministic conversion using exact dated exchange rates from exchange_rates.csv.
"""

from datetime import date
from decimal import Decimal
from typing import Dict, Tuple, List, Optional
from models import ExchangeRate


class MissingExchangeRateError(Exception):
    """Raised when an exact dated exchange rate is missing from the dataset."""
    pass


class ExchangeRateTable:
    """Index of fixed dated exchange rates."""

    def __init__(self, rates: Optional[List[ExchangeRate]] = None):
        self._rates: Dict[Tuple[date, str, str], Decimal] = {}
        if rates:
            for r in rates:
                self.add_rate(r)

    def add_rate(self, rate: ExchangeRate) -> None:
        key = (rate.rate_date, rate.from_currency.strip().upper(), rate.to_currency.strip().upper())
        self._rates[key] = rate.rate

    def get_rate(self, rate_date: date, from_currency: str, to_currency: str) -> Optional[Decimal]:
        from_curr = from_currency.strip().upper()
        to_curr = to_currency.strip().upper()
        if from_curr == to_curr:
            return Decimal("1.0")
        return self._rates.get((rate_date, from_curr, to_curr))

    def convert(
        self,
        amount: Decimal,
        from_currency: str,
        to_currency: str,
        rate_date: date
    ) -> Decimal:
        """
        Convert amount from from_currency to to_currency on rate_date.
        
        Rules:
        - If from_currency == to_currency, return amount directly.
        - Exact lookup of (rate_date, from_currency, to_currency).
        - If no exact dated rate is found, raise MissingExchangeRateError.
        - Never guesses, interpolates, or invents conversion rates.
        """
        from_curr = from_currency.strip().upper()
        to_curr = to_currency.strip().upper()

        if from_curr == to_curr:
            return amount

        rate = self._rates.get((rate_date, from_curr, to_curr))
        if rate is None:
            raise MissingExchangeRateError(
                f"Missing exact dated exchange rate for {from_curr}->{to_curr} on {rate_date.isoformat()}"
            )

        return amount * rate
