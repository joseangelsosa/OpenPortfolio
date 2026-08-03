from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

import yfinance as yf

from openportfolio.domain import Instrument, MarketQuote
from openportfolio.market_data import (
    MarketDataNotFoundError,
    ProviderResponseError,
    ProviderSymbolError,
)


class YFinanceMarketDataProvider:
    """Adapter that keeps every yfinance detail outside the domain."""

    name = "yfinance"

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
        try:
            ticker = self._ticker_factory(symbol)
            history = ticker.history(period="5d", interval="1d", auto_adjust=False)
        except Exception as error:
            raise ProviderResponseError(
                f"yfinance falló al consultar {symbol!r}"
            ) from error
        try:
            if history is None or history.empty:
                raise MarketDataNotFoundError(
                    f"yfinance no encontró cotizaciones para {symbol!r}"
                )
            if "Close" not in history:
                raise MarketDataNotFoundError(
                    f"yfinance devolvió cotizaciones sin precio para {symbol!r}"
                )
            usable_history = history.dropna(subset=["Close"])
            if usable_history.empty:
                raise MarketDataNotFoundError(
                    f"yfinance devolvió cotizaciones sin precio para {symbol!r}"
                )
            row = usable_history.iloc[-1]
            price = self._to_decimal(row["Close"], symbol)
            observed_at = self._to_datetime(row.name, symbol)
            reported_currency = self._currency(ticker, symbol)
            retrieved_at = self._retrieved_at(symbol)
            if observed_at > retrieved_at:
                raise ProviderResponseError(
                    f"yfinance devolvió un timestamp futuro para {symbol!r}"
                )
        except MarketDataNotFoundError:
            raise
        except ProviderResponseError:
            raise
        except Exception as error:
            raise ProviderResponseError(
                f"respuesta no utilizable de yfinance para {symbol!r}"
            ) from error
        try:
            return MarketQuote(
                id=f"yfinance:{instrument.id}:{observed_at.isoformat()}",
                instrument_id=instrument.id,
                price=price,
                currency=reported_currency,
                observed_at=observed_at,
                retrieved_at=retrieved_at,
                provider=self.name,
                provider_symbol=symbol,
                kind="close",
            )
        except (TypeError, ValueError) as error:
            raise ProviderResponseError(
                f"respuesta no utilizable de yfinance para {symbol!r}"
            ) from error

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
        return observed_at.astimezone(timezone.utc)

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
