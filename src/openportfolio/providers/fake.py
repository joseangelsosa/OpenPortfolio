from datetime import datetime, timezone
from decimal import Decimal
from typing import Mapping

from openportfolio.domain import Instrument, MarketQuote
from openportfolio.market_data import MarketDataNotFoundError, ProviderSymbolError


class FakeMarketDataProvider:
    """Deterministic, offline market-data provider."""

    name = "fake"
    _timestamp = datetime(2026, 1, 2, 16, 0, tzinfo=timezone.utc)

    def __init__(self, prices: Mapping[str, Decimal]) -> None:
        self._prices = dict(prices)

    def get_quote(self, instrument: Instrument) -> MarketQuote:
        symbol = instrument.symbol_for(self.name)
        if symbol is None:
            raise ProviderSymbolError(
                f"el instrumento {instrument.id!r} no tiene símbolo para el proveedor fake"
            )
        try:
            price = self._prices[symbol]
        except KeyError as error:
            raise MarketDataNotFoundError(
                f"fake no tiene cotización configurada para {symbol!r}"
            ) from error
        return MarketQuote(
            id=f"fake:{instrument.id}:2026-01-02T16:00:00Z",
            instrument_id=instrument.id,
            price=price,
            currency=instrument.currency,
            observed_at=self._timestamp,
            retrieved_at=self._timestamp,
            provider=self.name,
            provider_symbol=symbol,
        )

