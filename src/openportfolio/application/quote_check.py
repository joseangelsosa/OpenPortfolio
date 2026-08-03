from __future__ import annotations

from dataclasses import dataclass

from openportfolio.domain import Instrument, MarketQuote, Portfolio
from openportfolio.market_data import MarketDataError, MarketDataProvider


@dataclass(frozen=True, slots=True)
class QuoteCheckItem:
    instrument: Instrument
    requested_symbol: str | None
    quote: MarketQuote | None
    error: str | None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(frozen=True, slots=True)
class QuoteCheckResult:
    items: tuple[QuoteCheckItem, ...]

    @property
    def failed_symbols(self) -> tuple[str, ...]:
        return tuple(
            item.requested_symbol or item.instrument.id
            for item in self.items
            if not item.ok
        )

    @property
    def ok(self) -> bool:
        return not self.failed_symbols


def check_portfolio_quotes(
    portfolio: Portfolio,
    provider: MarketDataProvider,
) -> QuoteCheckResult:
    """Fetch and validate quotes without analysis, alerts, state, or notifications."""
    items: list[QuoteCheckItem] = []
    for instrument in portfolio.instruments:
        if not instrument.active:
            continue
        symbol = instrument.symbol_for(provider.name)
        try:
            quote = provider.get_quote(instrument)
        except MarketDataError as error:
            items.append(QuoteCheckItem(instrument, symbol, None, str(error)))
            continue

        error = None
        if quote.currency != instrument.currency:
            error = (
                f"moneda recibida {quote.currency}; "
                f"se esperaba {instrument.currency}"
            )
        items.append(QuoteCheckItem(instrument, symbol, quote, error))
    return QuoteCheckResult(tuple(items))
