from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pandas as pd
import pytest

from openportfolio.domain import Instrument, QuoteSource
from openportfolio.market_data import ProviderResponseError
from openportfolio.providers.yfinance import YFinanceMarketDataProvider


NOW = datetime(2026, 8, 3, 18, 0, tzinfo=timezone.utc)
INTRADAY_1 = datetime(2026, 8, 3, 17, 50, tzinfo=timezone.utc)
INTRADAY_2 = datetime(2026, 8, 3, 17, 55, tzinfo=timezone.utc)
INCOMPLETE = datetime(2026, 8, 3, 18, 0, tzinfo=timezone.utc)
DAILY = datetime(2026, 8, 1, 20, 0, tzinfo=timezone.utc)


class TickerStub:
    def __init__(self, histories: list[Any], currency: Any = "USD") -> None:
        self._histories = iter(histories)
        self.fast_info = {"currency": currency}
        self.history_arguments: list[dict[str, Any]] = []

    def history(self, **kwargs: Any) -> Any:
        self.history_arguments.append(kwargs)
        result = next(self._histories)
        if isinstance(result, Exception):
            raise result
        return result


def instrument(currency: str = "USD") -> Instrument:
    return Instrument("asset", "Asset", "STOCK", currency, {"yfinance": "TEST"})


def history(rows: list[tuple[Any, Any]]) -> pd.DataFrame:
    return pd.DataFrame(
        {"Close": [close for _, close in rows]},
        index=pd.Index([timestamp for timestamp, _ in rows], dtype=object),
    )


def daily(close: Any = "120.00", timestamp: Any = DAILY) -> pd.DataFrame:
    return history([(timestamp, close)])


def provider(ticker: TickerStub) -> YFinanceMarketDataProvider:
    return YFinanceMarketDataProvider(ticker_factory=lambda _: ticker, now=lambda: NOW)


def test_valid_intraday_response_is_normalized_with_explicit_source() -> None:
    ticker = TickerStub([history([(INTRADAY_1, "123.4500")])])
    quote = provider(ticker).get_quote(instrument())

    assert quote.price == Decimal("123.4500")
    assert quote.currency == "USD"
    assert quote.observed_at == INTRADAY_1
    assert quote.retrieved_at == NOW
    assert quote.provider_symbol == "TEST"
    assert quote.provider == "yfinance"
    assert quote.source is QuoteSource.INTRADAY
    assert ticker.history_arguments == [
        {"period": "1d", "interval": "5m", "prepost": False}
    ]


def test_last_complete_valid_intraday_candle_is_selected() -> None:
    ticker = TickerStub(
        [
            history(
                [
                    (INTRADAY_1, "121.00"),
                    (INTRADAY_2, "122.00"),
                    (INCOMPLETE, "999.00"),
                ]
            )
        ]
    )

    quote = provider(ticker).get_quote(instrument())

    assert quote.price == Decimal("122.00")
    assert quote.observed_at == INTRADAY_2
    assert quote.source is QuoteSource.INTRADAY


@pytest.mark.parametrize("intraday", [None, pd.DataFrame()])
def test_empty_intraday_response_uses_daily_close(intraday: Any) -> None:
    ticker = TickerStub([intraday, daily()])

    quote = provider(ticker).get_quote(instrument())

    assert quote.price == Decimal("120.00")
    assert quote.observed_at == DAILY
    assert quote.source is QuoteSource.DAILY_CLOSE
    assert ticker.history_arguments == [
        {"period": "1d", "interval": "5m", "prepost": False},
        {"period": "5d", "interval": "1d", "auto_adjust": False},
    ]


@pytest.mark.parametrize("price", [None, "not-a-price", "NaN", "Infinity", "0", "-1"])
def test_invalid_intraday_price_uses_daily_close(price: Any) -> None:
    quote = provider(TickerStub([history([(INTRADAY_1, price)]), daily()])).get_quote(
        instrument()
    )
    assert quote.source is QuoteSource.DAILY_CLOSE
    assert quote.price == Decimal("120.00")


@pytest.mark.parametrize("timestamp", [None, "not-a-timestamp", datetime(2026, 8, 3, 17, 50)])
def test_invalid_intraday_timestamp_uses_daily_close(timestamp: Any) -> None:
    quote = provider(TickerStub([history([(timestamp, "123")]), daily()])).get_quote(
        instrument()
    )
    assert quote.source is QuoteSource.DAILY_CLOSE


@pytest.mark.parametrize(
    "timestamp",
    [
        datetime(2026, 8, 2, 17, 50, tzinfo=timezone.utc),
        INCOMPLETE,
        datetime(2026, 8, 3, 18, 5, tzinfo=timezone.utc),
    ],
)
def test_stale_incomplete_or_future_intraday_candle_uses_daily_close(
    timestamp: datetime,
) -> None:
    quote = provider(TickerStub([history([(timestamp, "123")]), daily()])).get_quote(
        instrument()
    )
    assert quote.source is QuoteSource.DAILY_CLOSE


def test_intraday_client_exception_still_attempts_daily_close() -> None:
    ticker = TickerStub([RuntimeError("sensitive intraday detail"), daily("124")])

    quote = provider(ticker).get_quote(instrument())

    assert quote.price == Decimal("124")
    assert quote.source is QuoteSource.DAILY_CLOSE
    assert len(ticker.history_arguments) == 2


def test_intraday_and_daily_failures_identify_symbol_without_inventing_price() -> None:
    ticker = TickerStub(
        [RuntimeError("sensitive intraday detail"), RuntimeError("sensitive daily detail")]
    )

    with pytest.raises(ProviderResponseError) as captured:
        provider(ticker).get_quote(instrument())

    message = str(captured.value)
    assert "TEST" in message
    assert "intradía" in message
    assert "cierre diario" in message
    assert "sensitive" not in message
    assert len(ticker.history_arguments) == 2


def test_missing_currency_remains_a_safe_error_after_both_attempts() -> None:
    ticker = TickerStub([history([(INTRADAY_1, "123")]), daily()], None)
    with pytest.raises(ProviderResponseError, match="TEST"):
        provider(ticker).get_quote(instrument())


def test_reported_currency_is_preserved_for_application_validation() -> None:
    ticker = TickerStub([history([(INTRADAY_1, "123")])], "EUR")
    quote = provider(ticker).get_quote(instrument("USD"))
    assert quote.currency == "EUR"


def test_float_price_uses_safe_string_conversion_to_decimal() -> None:
    quote = provider(TickerStub([history([(INTRADAY_1, 123.45)])])).get_quote(
        instrument()
    )
    assert quote.price == Decimal("123.45")
