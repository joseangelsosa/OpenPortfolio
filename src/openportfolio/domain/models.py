from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping


def _required_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} no puede estar vacío")
    return normalized


def _currency(value: str) -> str:
    normalized = value.strip().upper()
    if len(normalized) != 3 or not normalized.isalpha() or not normalized.isascii():
        raise ValueError("currency debe ser un código de tres letras")
    return normalized


def _aware(timestamp: datetime, field_name: str) -> datetime:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError(f"{field_name} debe incluir zona horaria")
    return timestamp


@dataclass(frozen=True, slots=True)
class Instrument:
    id: str
    name: str
    asset_type: str
    currency: str
    provider_symbols: Mapping[str, str] = field(default_factory=dict)
    active: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required_text(self.id, "instrument.id"))
        object.__setattr__(self, "name", _required_text(self.name, "instrument.name"))
        object.__setattr__(self, "asset_type", _required_text(self.asset_type, "instrument.asset_type"))
        object.__setattr__(self, "currency", _currency(self.currency))
        symbols: dict[str, str] = {}
        for provider, symbol in self.provider_symbols.items():
            normalized_provider = _required_text(provider, "provider").lower()
            symbols[normalized_provider] = _required_text(symbol, "provider_symbol")
        object.__setattr__(self, "provider_symbols", MappingProxyType(symbols))

    def symbol_for(self, provider: str) -> str | None:
        return self.provider_symbols.get(provider.lower())


@dataclass(frozen=True, slots=True)
class Position:
    id: str
    portfolio_id: str
    instrument_id: str
    quantity: Decimal
    as_of: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required_text(self.id, "position.id"))
        object.__setattr__(self, "portfolio_id", _required_text(self.portfolio_id, "position.portfolio_id"))
        object.__setattr__(self, "instrument_id", _required_text(self.instrument_id, "position.instrument_id"))
        if not isinstance(self.quantity, Decimal):
            raise TypeError("position.quantity debe ser Decimal")
        if not self.quantity.is_finite() or self.quantity <= 0:
            raise ValueError("position.quantity debe ser finita y mayor que cero")
        _aware(self.as_of, "position.as_of")

    def market_value(self, quote: MarketQuote) -> Decimal:
        if quote.instrument_id != self.instrument_id:
            raise ValueError("la cotización no corresponde al instrumento de la posición")
        return self.quantity * quote.price


@dataclass(frozen=True, slots=True)
class MarketQuote:
    id: str
    instrument_id: str
    price: Decimal
    currency: str
    observed_at: datetime
    retrieved_at: datetime
    provider: str
    provider_symbol: str
    kind: str = "last_available"

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required_text(self.id, "quote.id"))
        object.__setattr__(self, "instrument_id", _required_text(self.instrument_id, "quote.instrument_id"))
        if not isinstance(self.price, Decimal):
            raise TypeError("quote.price debe ser Decimal")
        if not self.price.is_finite() or self.price <= 0:
            raise ValueError("quote.price debe ser finito y mayor que cero")
        object.__setattr__(self, "currency", _currency(self.currency))
        _aware(self.observed_at, "quote.observed_at")
        _aware(self.retrieved_at, "quote.retrieved_at")
        if self.observed_at > self.retrieved_at:
            raise ValueError("quote.observed_at no puede ser posterior a retrieved_at")
        object.__setattr__(self, "provider", _required_text(self.provider, "quote.provider"))
        object.__setattr__(self, "provider_symbol", _required_text(self.provider_symbol, "quote.provider_symbol"))
        object.__setattr__(self, "kind", _required_text(self.kind, "quote.kind"))


@dataclass(frozen=True, slots=True)
class Portfolio:
    id: str
    name: str
    base_currency: str
    instruments: tuple[Instrument, ...]
    positions: tuple[Position, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required_text(self.id, "portfolio.id"))
        object.__setattr__(self, "name", _required_text(self.name, "portfolio.name"))
        object.__setattr__(self, "base_currency", _currency(self.base_currency))
        instrument_ids = [instrument.id for instrument in self.instruments]
        if len(instrument_ids) != len(set(instrument_ids)):
            raise ValueError("los identificadores de instrumento deben ser únicos")
        position_ids = [position.id for position in self.positions]
        if len(position_ids) != len(set(position_ids)):
            raise ValueError("los identificadores de posición deben ser únicos")
        known_instruments = set(instrument_ids)
        seen_instruments: set[str] = set()
        for position in self.positions:
            if position.portfolio_id != self.id:
                raise ValueError(f"la posición {position.id} pertenece a otra cartera")
            if position.instrument_id not in known_instruments:
                raise ValueError(f"instrumento desconocido en la posición {position.id}")
            if position.instrument_id in seen_instruments:
                raise ValueError("solo puede haber una posición agregada por instrumento")
            seen_instruments.add(position.instrument_id)

    def instrument(self, instrument_id: str) -> Instrument:
        for instrument in self.instruments:
            if instrument.id == instrument_id:
                return instrument
        raise KeyError(f"instrumento desconocido: {instrument_id}")

    def values_by_position(self, quotes: Mapping[str, MarketQuote]) -> dict[str, Decimal | None]:
        values: dict[str, Decimal | None] = {}
        for position in self.positions:
            quote = quotes.get(position.instrument_id)
            if quote is None:
                values[position.id] = None
                continue
            instrument = self.instrument(position.instrument_id)
            if quote.currency != instrument.currency:
                raise ValueError(
                    f"moneda incompatible para {instrument.id}: {quote.currency} != {instrument.currency}"
                )
            values[position.id] = position.market_value(quote)
        return values

    def totals_by_currency(self, quotes: Mapping[str, MarketQuote]) -> dict[str, Decimal]:
        values = self.values_by_position(quotes)
        totals: dict[str, Decimal] = {}
        for position in self.positions:
            value = values[position.id]
            if value is None:
                continue
            currency = self.instrument(position.instrument_id).currency
            totals[currency] = totals.get(currency, Decimal("0")) + value
        return totals
