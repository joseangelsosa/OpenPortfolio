from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pandas as pd
import pytest

from openportfolio.domain import Instrument
from openportfolio.market_data import MarketDataNotFoundError, ProviderResponseError
from openportfolio.providers.yfinance import YFinanceMarketDataProvider


NOW = datetime(2026, 8, 3, 18, 0, tzinfo=timezone.utc)
OBSERVED = datetime(2026, 8, 1, 20, 0, tzinfo=timezone.utc)


class TickerStub:
    def __init__(self, history: Any, currency: Any = "USD") -> None:
        self._history = history
        self.fast_info = {"currency": currency}
        self.history_arguments: dict[str, Any] | None = None

    def history(self, **kwargs: Any) -> Any:
        self.history_arguments = kwargs
        return self._history


def instrument(currency: str = "USD") -> Instrument:
    return Instrument("asset", "Asset", "STOCK", currency, {"yfinance": "TEST"})


def history(close: Any = "123.4500", timestamp: Any = OBSERVED) -> pd.DataFrame:
    return pd.DataFrame({"Close": [close]}, index=[timestamp])


def provider(ticker: TickerStub) -> YFinanceMarketDataProvider:
    return YFinanceMarketDataProvider(ticker_factory=lambda _: ticker, now=lambda: NOW)


def test_valid_response_is_normalized_with_decimal_currency_and_provenance() -> None:
    ticker = TickerStub(history())
    quote = provider(ticker).get_quote(instrument())

    assert quote.price == Decimal("123.4500")
    assert quote.currency == "USD"
    assert quote.observed_at == OBSERVED
    assert quote.retrieved_at == NOW
    assert quote.provider_symbol == "TEST"
    assert quote.provider == "yfinance"
    assert ticker.history_arguments == {
        "period": "5d",
        "interval": "1d",
        "auto_adjust": False,
    }


def test_float_price_uses_safe_string_conversion_to_decimal() -> None:
    quote = provider(TickerStub(history(123.45))).get_quote(instrument())
    assert quote.price == Decimal("123.45")


@pytest.mark.parametrize("response", [None, pd.DataFrame()])
def test_empty_response_is_reported_as_not_found(response: Any) -> None:
    with pytest.raises(MarketDataNotFoundError, match="TEST"):
        provider(TickerStub(response)).get_quote(instrument())


def test_response_without_close_is_reported_as_missing_price() -> None:
    response = pd.DataFrame({"Volume": [10]}, index=[OBSERVED])
    with pytest.raises(MarketDataNotFoundError, match="sin precio.*TEST"):
        provider(TickerStub(response)).get_quote(instrument())


@pytest.mark.parametrize("price", ["not-a-price", "NaN", "Infinity", "0", "-1"])
def test_invalid_price_is_rejected_without_using_zero(price: str) -> None:
    with pytest.raises(ProviderResponseError, match="precio.*TEST"):
        provider(TickerStub(history(price))).get_quote(instrument())


def test_missing_currency_is_rejected() -> None:
    with pytest.raises(ProviderResponseError, match="moneda.*TEST"):
        provider(TickerStub(history(), None)).get_quote(instrument())


@pytest.mark.parametrize("timestamp", [None, "not-a-timestamp", datetime(2026, 8, 1)])
def test_missing_or_invalid_timestamp_is_rejected(timestamp: Any) -> None:
    with pytest.raises(ProviderResponseError, match="timestamp.*TEST"):
        provider(TickerStub(history(timestamp=timestamp))).get_quote(instrument())


def test_external_client_exception_is_sanitized_and_identifies_symbol() -> None:
    def failing_factory(symbol: str) -> Any:
        raise RuntimeError("sensitive external detail")

    adapter = YFinanceMarketDataProvider(ticker_factory=failing_factory, now=lambda: NOW)
    with pytest.raises(ProviderResponseError) as captured:
        adapter.get_quote(instrument())
    assert "TEST" in str(captured.value)
    assert "sensitive external detail" not in str(captured.value)


def test_currency_mismatch_is_explicit() -> None:
    with pytest.raises(ProviderResponseError, match="EUR.*TEST.*USD"):
        provider(TickerStub(history(), "EUR")).get_quote(instrument("USD"))
