from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from openportfolio.alerts import Notifier, alerts_from_events
from openportfolio.analysis import PriceReferenceChangeRule
from openportfolio.domain import Alert, AnalysisEvent, MarketQuote
from openportfolio.market_data import MarketDataError, MarketDataProvider
from openportfolio.persistence import AlertState, AlertStateStore, PortfolioConfiguration


@dataclass(frozen=True, slots=True)
class ReviewResult:
    quotes: Mapping[str, MarketQuote]
    events: tuple[AnalysisEvent, ...]
    alerts: tuple[Alert, ...]
    delivered_alerts: tuple[Alert, ...]
    suppressed_alerts: tuple[Alert, ...]
    quote_errors: Mapping[str, str]
    notification_errors: tuple[str, ...]

    @property
    def is_partial(self) -> bool:
        return bool(self.quote_errors)

    @property
    def notifications_sent(self) -> int:
        return len(self.delivered_alerts)


def run_portfolio_review(
    configuration: PortfolioConfiguration,
    provider: MarketDataProvider,
    notifier: Notifier,
    *,
    state_store: AlertStateStore | None = None,
    dry_run: bool = False,
) -> ReviewResult:
    state = state_store.load() if state_store is not None else AlertState.empty()
    quotes: dict[str, MarketQuote] = {}
    quote_errors: dict[str, str] = {}
    events: list[AnalysisEvent] = []
    rule = PriceReferenceChangeRule()

    for instrument in configuration.portfolio.instruments:
        if not instrument.active:
            continue
        try:
            quote = provider.get_quote(instrument)
        except MarketDataError as error:
            quote_errors[instrument.id] = str(error)
            continue
        if quote.currency != instrument.currency:
            quote_errors[instrument.id] = (
                f"moneda recibida {quote.currency}; se esperaba {instrument.currency}"
            )
            continue
        quotes[instrument.id] = quote
        thresholds = configuration.price_reference_rules.get(instrument.id)
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
    delivered_alerts: list[Alert] = []
    suppressed_alerts: list[Alert] = []
    notification_errors: list[str] = []
    for alert in alerts:
        if state.was_delivered(alert):
            suppressed_alerts.append(alert)
            continue
        try:
            notifier.send(alert)
        except RuntimeError as error:
            notification_errors.append(str(error))
            continue
        if dry_run:
            continue
        delivered_alerts.append(alert)
        if state_store is not None:
            state = state.with_delivered(alert)
            state_store.save(state)

    return ReviewResult(
        quotes=MappingProxyType(quotes),
        events=tuple(events),
        alerts=alerts,
        delivered_alerts=tuple(delivered_alerts),
        suppressed_alerts=tuple(suppressed_alerts),
        quote_errors=MappingProxyType(quote_errors),
        notification_errors=tuple(notification_errors),
    )
