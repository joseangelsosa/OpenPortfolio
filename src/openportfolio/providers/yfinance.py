from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

import yfinance as yf

from openportfolio.domain import Instrument, MarketQuote, QuoteSource
from openportfolio.market_data import (
    MarketDataError,
    MarketDataNotFoundError,
    ProviderResponseError,
    ProviderSymbolError,
)


class YFinanceMarketDataProvider:
    """Adapter that keeps every yfinance detail outside the domain.

    A 5-minute candle is usable when it has finished according to the injected
    clock and belongs to the clock's current date in the candle's own timezone.
    This deliberately rejects stale sessions (for example, Friday data on a
    weekend) and lets the daily-close fallback handle them.
    """

    name = "yfinance"
    _intraday_interval = timedelta(minutes=5)

    def __init__(
        self,
        *,
        ticker_factory: Callable[[str], Any] = yf.Ticker,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._ticker_factory = ticker_factory
        self._now = now or (lambda: datetime.now(timezone.utc))

    def get_quote(self, instrument: Instrument) -> MarketQuote:
        symbol = instrument.symbol_for(self.name)
        if symbol is None:
            raise ProviderSymbolError(
                f"el instrumento {instrument.id!r} no tiene símbolo para yfinance"
            )
        retrieved_at = self._retrieved_at(symbol)

        try:
            return self._intraday_quote(instrument, symbol, retrieved_at)
        except MarketDataError as error:
            intraday_failure = str(error)
        except Exception:
            intraday_failure = f"fallo técnico al consultar intradía para {symbol!r}"

        try:
            return self._daily_quote(instrument, symbol, retrieved_at)
        except MarketDataError as error:
            daily_failure = str(error)
        except Exception:
            daily_failure = f"fallo técnico al consultar el cierre diario para {symbol!r}"

        raise ProviderResponseError(
            f"yfinance no obtuvo una cotización para {symbol!r}; "
            f"intradía: {intraday_failure}; cierre diario: {daily_failure}"
        )

    def _intraday_quote(
        self,
        instrument: Instrument,
        symbol: str,
        retrieved_at: datetime,
    ) -> MarketQuote:
        ticker = self._ticker(symbol, "intradía")
        try:
            history = ticker.history(period="1d", interval="5m", prepost=False)
        except Exception as error:
            raise ProviderResponseError(
                f"yfinance falló al consultar intradía para {symbol!r}"
            ) from error
        if history is None or history.empty:
            raise MarketDataNotFoundError(
                f"yfinance no encontró datos intradía para {symbol!r}"
            )
        if "Close" not in history:
            raise MarketDataNotFoundError(
                f"yfinance devolvió datos intradía sin precio para {symbol!r}"
            )

        candidates: list[tuple[datetime, Decimal]] = []
        for timestamp, row in history.iterrows():
            try:
                observed_at = self._to_datetime(timestamp, symbol)
                price = self._to_decimal(row["Close"], symbol)
            except MarketDataError:
                continue
            if self._is_usable_intraday(observed_at, retrieved_at):
                candidates.append((observed_at, price))
        if not candidates:
            raise ProviderResponseError(
                f"yfinance no devolvió una vela intradía completa, reciente y válida "
                f"para {symbol!r}"
            )
        observed_at, price = max(candidates, key=lambda candidate: candidate[0])
        observed_at = observed_at.astimezone(timezone.utc)
        return self._quote(
            instrument,
            ticker,
            symbol,
            price,
            observed_at,
            retrieved_at,
            QuoteSource.INTRADAY,
            "5m_close",
        )

    def _daily_quote(
        self,
        instrument: Instrument,
        symbol: str,
        retrieved_at: datetime,
    ) -> MarketQuote:
        ticker = self._ticker(symbol, "cierre diario")
        try:
            history = ticker.history(period="5d", interval="1d", auto_adjust=False)
        except Exception as error:
            raise ProviderResponseError(
                f"yfinance falló al consultar el cierre diario para {symbol!r}"
            ) from error
        if history is None or history.empty:
            raise MarketDataNotFoundError(
                f"yfinance no encontró cierres diarios para {symbol!r}"
            )
        if "Close" not in history:
            raise MarketDataNotFoundError(
                f"yfinance devolvió cierres diarios sin precio para {symbol!r}"
            )
        usable_history = history.dropna(subset=["Close"])
        if usable_history.empty:
            raise MarketDataNotFoundError(
                f"yfinance devolvió cierres diarios sin precio para {symbol!r}"
            )
        row = usable_history.iloc[-1]
        price = self._to_decimal(row["Close"], symbol)
        observed_at = self._to_datetime(row.name, symbol)
        if observed_at > retrieved_at:
            raise ProviderResponseError(
                f"yfinance devolvió un cierre diario futuro para {symbol!r}"
            )
        observed_at = observed_at.astimezone(timezone.utc)
        return self._quote(
            instrument,
            ticker,
            symbol,
            price,
            observed_at,
            retrieved_at,
            QuoteSource.DAILY_CLOSE,
            "close",
        )

    def _quote(
        self,
        instrument: Instrument,
        ticker: Any,
        symbol: str,
        price: Decimal,
        observed_at: datetime,
        retrieved_at: datetime,
        source: QuoteSource,
        kind: str,
    ) -> MarketQuote:
        try:
            return MarketQuote(
                id=f"yfinance:{instrument.id}:{observed_at.isoformat()}",
                instrument_id=instrument.id,
                price=price,
                currency=self._currency(ticker, symbol),
                observed_at=observed_at,
                retrieved_at=retrieved_at,
                provider=self.name,
                provider_symbol=symbol,
                source=source,
                kind=kind,
            )
        except (TypeError, ValueError) as error:
            raise ProviderResponseError(
                f"respuesta no utilizable de yfinance para {symbol!r}"
            ) from error

    def _ticker(self, symbol: str, query: str) -> Any:
        try:
            return self._ticker_factory(symbol)
        except Exception as error:
            raise ProviderResponseError(
                f"yfinance falló al preparar la consulta de {query} para {symbol!r}"
            ) from error

    @classmethod
    def _is_usable_intraday(
        cls, observed_at: datetime, retrieved_at: datetime
    ) -> bool:
        local_retrieved_at = retrieved_at.astimezone(observed_at.tzinfo)
        return (
            observed_at.date() == local_retrieved_at.date()
            and observed_at + cls._intraday_interval <= local_retrieved_at
        )

    @staticmethod
    def _to_decimal(value: Any, symbol: str) -> Decimal:
        try:
            price = Decimal(str(value))
        except (InvalidOperation, ValueError) as error:
            raise ProviderResponseError(
                f"yfinance devolvió un precio inválido para {symbol!r}"
            ) from error
        if not price.is_finite() or price <= 0:
            raise ProviderResponseError(
                f"yfinance devolvió un precio no positivo para {symbol!r}"
            )
        return price

    @staticmethod
    def _to_datetime(value: Any, symbol: str) -> datetime:
        converter = getattr(value, "to_pydatetime", None)
        observed_at = converter() if converter is not None else value
        if not isinstance(observed_at, datetime):
            raise ProviderResponseError(
                f"yfinance devolvió un timestamp inválido para {symbol!r}"
            )
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ProviderResponseError(
                f"yfinance devolvió un timestamp sin zona horaria para {symbol!r}"
            )
        return observed_at

    def _retrieved_at(self, symbol: str) -> datetime:
        value = self._now()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ProviderResponseError(
                f"no se pudo fechar la consulta de yfinance para {symbol!r}"
            )
        return value.astimezone(timezone.utc)

    @staticmethod
    def _currency(ticker: Any, symbol: str) -> str:
        try:
            currency = ticker.fast_info.get("currency")
        except Exception as error:
            raise ProviderResponseError(
                f"yfinance no pudo informar la moneda de {symbol!r}"
            ) from error
        if not isinstance(currency, str) or not currency.strip():
            raise ProviderResponseError(
                f"yfinance devolvió una moneda vacía para {symbol!r}"
            )
        return currency.strip().upper()
