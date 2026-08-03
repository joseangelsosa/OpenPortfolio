from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from openportfolio.domain import AnalysisEvent, Instrument, MarketQuote, Severity


PRICE_REFERENCE_CHANGE = "PRICE_REFERENCE_CHANGE"


@dataclass(frozen=True, slots=True)
class PriceReferenceThresholds:
    reference_price: Decimal
    review_change_percent: Decimal
    high_change_percent: Decimal

    def __post_init__(self) -> None:
        for field_name in (
            "reference_price",
            "review_change_percent",
            "high_change_percent",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, Decimal):
                raise TypeError(f"{field_name} debe ser Decimal")
            if not value.is_finite() or value <= 0:
                raise ValueError(f"{field_name} debe ser finito y mayor que cero")
        if self.high_change_percent <= self.review_change_percent:
            raise ValueError("high_change_percent debe ser mayor que review_change_percent")


class PriceReferenceChangeRule:
    code = PRICE_REFERENCE_CHANGE

    def evaluate(
        self,
        *,
        portfolio_id: str,
        instrument: Instrument,
        quote: MarketQuote,
        thresholds: PriceReferenceThresholds,
    ) -> AnalysisEvent | None:
        change_percent = (
            (quote.price - thresholds.reference_price)
            / thresholds.reference_price
            * Decimal("100")
        )
        absolute_change = abs(change_percent)
        if absolute_change >= thresholds.high_change_percent:
            severity = Severity.HIGH
            crossed_threshold = thresholds.high_change_percent
        elif absolute_change >= thresholds.review_change_percent:
            severity = Severity.REVIEW
            crossed_threshold = thresholds.review_change_percent
        else:
            return None

        direction = "up" if change_percent >= 0 else "down"
        explanation = (
            f"{instrument.name} moved {direction} {abs(change_percent):.2f}% from its "
            f"configured reference price and crossed the {crossed_threshold:.2f}% threshold."
        )
        return AnalysisEvent.create(
            portfolio_id=portfolio_id,
            rule_code=self.code,
            title=f"{instrument.name} crossed a configured threshold",
            explanation=explanation,
            severity=severity,
            instrument_id=instrument.id,
            instrument_name=instrument.name,
            currency=quote.currency,
            current_price=quote.price,
            reference_price=thresholds.reference_price,
            change_percent=change_percent,
            threshold_percent=crossed_threshold,
            occurred_at=quote.observed_at,
        )
