from decimal import Decimal

import pytest

from openportfolio.domain import Instrument
from openportfolio.market_data import MarketDataNotFoundError, ProviderSymbolError
from openportfolio.providers import FakeMarketDataProvider


def make_instrument(symbol: str | None = "FAKE-ASSET") -> Instrument:
    symbols = {} if symbol is None else {"fake": symbol}
    return Instrument("asset", "Activo ficticio", "ETF", "EUR", symbols)


def test_fake_provider_is_deterministic_and_offline() -> None:
    provider = FakeMarketDataProvider({"FAKE-ASSET": Decimal("12.34")})
    first = provider.get_quote(make_instrument())
    second = provider.get_quote(make_instrument())
    assert first == second
    assert first.price == Decimal("12.34")
    assert first.provider == "fake"
    assert first.observed_at.tzinfo is not None


def test_fake_provider_reports_missing_quote() -> None:
    provider = FakeMarketDataProvider({})
    with pytest.raises(MarketDataNotFoundError, match="no tiene cotización"):
        provider.get_quote(make_instrument())


def test_fake_provider_reports_missing_symbol() -> None:
    provider = FakeMarketDataProvider({})
    with pytest.raises(ProviderSymbolError, match="no tiene símbolo"):
        provider.get_quote(make_instrument(None))

