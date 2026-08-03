from typing import Protocol

from openportfolio.domain import Instrument, MarketQuote


class MarketDataError(RuntimeError):
    """Base error exposed by all market-data adapters."""


class ProviderSymbolError(MarketDataError):
    """The instrument has no usable symbol for the selected provider."""


class MarketDataNotFoundError(MarketDataError):
    """The provider returned no quote for the requested symbol."""


class ProviderResponseError(MarketDataError):
    """The provider failed or returned an unusable response."""


class MarketDataProvider(Protocol):
    name: str

    def get_quote(self, instrument: Instrument) -> MarketQuote:
        """Return a normalized quote or raise an explicit market-data error."""
        ...

