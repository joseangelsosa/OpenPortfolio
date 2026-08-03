from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from openportfolio.alerts import Notifier, alerts_from_events
from openportfolio.analysis import PriceReferenceChangeRule
from openportfolio.domain import Alert, AnalysisEvent, MarketQuote
from openportfolio.market_data import MarketDataError, MarketDataProvider
from openportfolio.persistence import PortfolioConfiguration


@dataclass(frozen=True, slots=True)
class ReviewResult:
    quotes: Mapping[str, MarketQuote]
    events: tuple[AnalysisEvent, ...]
    alerts: tuple[Alert, ...]
    quote_errors: Mapping[str, str]
    notification_errors: tuple[str, ...]

    @property
    def is_partial(self) -> bool:
        return bool(self.quote_errors)

    @property
    def notifications_sent(self) -> int:
        return len(self.alerts) - len(self.notification_errors)


def run_portfolio_review(
    configuration: PortfolioConfiguration,
    provider: MarketDataProvider,
    notifier: Notifier,
) -> ReviewResult:
    quotes: dict[str, MarketQuote] = {}
    quote_errors: dict[str, str] = {}
    events: list[AnalysisEvent] = []
    rule = PriceReferenceChangeRule()

    for position in configuration.portfolio.positions:
        instrument = configuration.portfolio.instrument(position.instrument_id)
        if not instrument.active:
            continue
        try:
            quote = provider.get_quote(instrument)
        except MarketDataError as error:
            quote_errors[instrument.id] = str(error)
            continue
        quotes[instrument.id] = quote
        thresholds = configuration.price_reference_rules.get(position.id)
        if thresholds is None:
            continue
        event = rule.evaluate(
            portfolio_id=configuration.portfolio.id,
            instrument=instrument,
            quote=quote,
            thresholds=thresholds,
        )
        if event is not None:
            events.append(event)

    alerts = alerts_from_events(events)
    notification_errors: list[str] = []
    for alert in alerts:
        try:
            notifier.send(alert)
        except RuntimeError as error:
            notification_errors.append(str(error))

    return ReviewResult(
        quotes=MappingProxyType(quotes),
        events=tuple(events),
        alerts=alerts,
        quote_errors=MappingProxyType(quote_errors),
        notification_errors=tuple(notification_errors),
    )
