from datetime import datetime, timezone
from decimal import Decimal

import pytest

from openportfolio.application import check_portfolio_quotes
from openportfolio.cli import main
from openportfolio.domain import Instrument, MarketQuote, Portfolio, QuoteSource
from openportfolio.market_data import MarketDataNotFoundError


NOW = datetime(2026, 8, 3, 18, 0, tzinfo=timezone.utc)
SYMBOLS = ("H4ZF.DE", "NVDA", "GOOGL", "MSFT", "NESR.DE")
CURRENCIES = ("EUR", "USD", "USD", "USD", "EUR")


def _portfolio() -> Portfolio:
    instruments = tuple(
        Instrument(
            id=f"asset-{index}",
            name=f"Instrument {index}",
            asset_type="STOCK",
            currency=currency,
            provider_symbols={"test": symbol, "yfinance": symbol},
        )
        for index, (symbol, currency) in enumerate(
            zip(SYMBOLS, CURRENCIES, strict=True), start=1
        )
    )
    return Portfolio("operational", "Operational", "EUR", instruments, ())


def _quote(
    instrument: Instrument,
    currency: str | None = None,
    provider_name: str = "test",
    source: QuoteSource = QuoteSource.INTRADAY,
) -> MarketQuote:
    symbol = instrument.symbol_for(provider_name)
    assert symbol is not None
    return MarketQuote(
        id=f"test:{instrument.id}",
        instrument_id=instrument.id,
        price=Decimal("123.45"),
        currency=currency or instrument.currency,
        observed_at=NOW,
        retrieved_at=NOW,
        provider=provider_name,
        provider_symbol=symbol,
        source=source,
    )


class StubProvider:
    def __init__(
        self,
        *,
        mismatch: str | None = None,
        failing: str | None = None,
        name: str = "test",
    ) -> None:
        self.name = name
        self.mismatch = mismatch
        self.failing = failing
        self.requested: list[str] = []

    def get_quote(self, instrument: Instrument) -> MarketQuote:
        symbol = instrument.symbol_for(self.name)
        assert symbol is not None
        self.requested.append(symbol)
        if symbol == self.failing:
            raise MarketDataNotFoundError(f"sin cotización para {symbol}")
        currency = "CHF" if symbol == self.mismatch else instrument.currency
        return _quote(instrument, currency, self.name)


def test_five_valid_quotes_are_checked() -> None:
    provider = StubProvider()
    result = check_portfolio_quotes(_portfolio(), provider)

    assert result.ok
    assert len(result.items) == 5
    assert all(item.ok for item in result.items)
    assert provider.requested == list(SYMBOLS)


def test_currency_mismatch_is_an_instrument_failure_with_received_quote() -> None:
    result = check_portfolio_quotes(_portfolio(), StubProvider(mismatch="NESR.DE"))
    item = result.items[-1]

    assert not result.ok
    assert item.quote is not None
    assert item.quote.currency == "CHF"
    assert item.error == "moneda recibida CHF; se esperaba EUR"
    assert result.failed_symbols == ("NESR.DE",)


def test_failed_symbol_does_not_prevent_checking_remaining_instruments() -> None:
    provider = StubProvider(failing="NVDA")
    result = check_portfolio_quotes(_portfolio(), provider)

    assert provider.requested == list(SYMBOLS)
    assert result.failed_symbols == ("NVDA",)
    assert len([item for item in result.items if item.ok]) == 4


def test_cli_returns_nonzero_summarizes_all_results_and_uses_no_side_effects(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    provider = StubProvider(mismatch="NESR.DE", failing="NVDA", name="yfinance")
    monkeypatch.setattr("openportfolio.cli._provider", lambda *_: provider)
    monkeypatch.setattr(
        "openportfolio.cli.run_portfolio_review",
        lambda *args, **kwargs: pytest.fail("no deben ejecutarse reglas"),
    )
    monkeypatch.setattr(
        "openportfolio.cli.JsonAlertStateStore",
        lambda *args, **kwargs: pytest.fail("no debe accederse al estado"),
    )
    monkeypatch.setattr(
        "openportfolio.cli.ConsoleNotifier",
        lambda *args, **kwargs: pytest.fail("no debe crearse un notifier"),
    )
    monkeypatch.setenv("OPENPORTFOLIO_NTFY_TOPIC", "TOPIC-MUY-SECRETO")

    result = main(
        [
            "--check-quotes",
            "--portfolio",
            "examples/operational_review.yaml",
            "--provider",
            "yfinance",
        ]
    )
    captured = capsys.readouterr()

    assert result == 1
    assert captured.err == ""
    assert provider.requested == list(SYMBOLS)
    assert "Símbolos fallidos: NVDA, NESR.DE" in captured.out
    assert "moneda recibida CHF; se esperaba EUR" in captured.out
    assert "H4ZF.DE" in captured.out and "GOOGL" in captured.out and "MSFT" in captured.out
    assert "TOPIC-MUY-SECRETO" not in captured.out
    assert "ntfy" not in captured.out.lower()


@pytest.mark.parametrize("source", list(QuoteSource))
def test_cli_displays_quote_source(
    source: QuoteSource,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class SourceProvider(StubProvider):
        def get_quote(self, instrument: Instrument) -> MarketQuote:
            self.requested.append(instrument.symbol_for(self.name) or "")
            return _quote(instrument, provider_name=self.name, source=source)

    monkeypatch.setattr("openportfolio.cli._provider", lambda *_: SourceProvider(name="yfinance"))
    result = main(
        [
            "--check-quotes",
            "--portfolio",
            "examples/operational_review.yaml",
            "--provider",
            "yfinance",
        ]
    )

    assert result == 0
    assert f"source: {source.value}" in capsys.readouterr().out
