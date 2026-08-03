from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
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


class Severity(StrEnum):
    INFO = "INFO"
    REVIEW = "REVIEW"
    HIGH = "HIGH"


class QuoteSource(StrEnum):
    INTRADAY = "INTRADAY"
    DAILY_CLOSE = "DAILY_CLOSE"


def _stable_identifier(prefix: str, payload: Mapping[str, str]) -> str:
    canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return f"{prefix}-{sha256(canonical.encode('utf-8')).hexdigest()[:24]}"


def _canonical_decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")


@dataclass(frozen=True, slots=True)
class AnalysisEvent:
    id: str
    portfolio_id: str
    rule_code: str
    title: str
    explanation: str
    severity: Severity
    instrument_id: str | None
    instrument_name: str | None
    currency: str | None
    current_price: Decimal
    reference_price: Decimal
    change_percent: Decimal
    threshold_percent: Decimal
    occurred_at: datetime
    provider_symbol: str | None = None
    source: QuoteSource | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required_text(self.id, "analysis_event.id"))
        object.__setattr__(
            self,
            "portfolio_id",
            _required_text(self.portfolio_id, "analysis_event.portfolio_id"),
        )
        object.__setattr__(self, "rule_code", _required_text(self.rule_code, "analysis_event.rule_code"))
        object.__setattr__(self, "title", _required_text(self.title, "analysis_event.title"))
        object.__setattr__(
            self,
            "explanation",
            _required_text(self.explanation, "analysis_event.explanation"),
        )
        if not isinstance(self.severity, Severity):
            raise TypeError("analysis_event.severity debe ser Severity")
        if self.instrument_id is not None:
            object.__setattr__(
                self,
                "instrument_id",
                _required_text(self.instrument_id, "analysis_event.instrument_id"),
            )
        if self.instrument_name is not None:
            object.__setattr__(
                self,
                "instrument_name",
                _required_text(self.instrument_name, "analysis_event.instrument_name"),
            )
        if self.currency is not None:
            object.__setattr__(self, "currency", _currency(self.currency))
        for field_name in (
            "current_price",
            "reference_price",
            "change_percent",
            "threshold_percent",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, Decimal):
                raise TypeError(f"analysis_event.{field_name} debe ser Decimal")
            if not value.is_finite():
                raise ValueError(f"analysis_event.{field_name} debe ser finito")
        if self.current_price <= 0 or self.reference_price <= 0 or self.threshold_percent <= 0:
            raise ValueError("precios y umbral del evento deben ser mayores que cero")
        _aware(self.occurred_at, "analysis_event.occurred_at")
        if self.provider_symbol is not None:
            object.__setattr__(
                self,
                "provider_symbol",
                _required_text(self.provider_symbol, "analysis_event.provider_symbol"),
            )
        if self.source is not None and not isinstance(self.source, QuoteSource):
            raise TypeError("analysis_event.source debe ser QuoteSource")

    @classmethod
    def create(
        cls,
        *,
        portfolio_id: str,
        rule_code: str,
        title: str,
        explanation: str,
        severity: Severity,
        instrument_id: str,
        instrument_name: str,
        currency: str,
        current_price: Decimal,
        reference_price: Decimal,
        change_percent: Decimal,
        threshold_percent: Decimal,
        occurred_at: datetime,
        provider_symbol: str | None = None,
        source: QuoteSource | None = None,
    ) -> AnalysisEvent:
        identifier = _stable_identifier(
            "event",
            {
                "portfolio_id": portfolio_id,
                "rule_code": rule_code,
                "severity": severity.value,
                "instrument_id": instrument_id,
                "current_price": _canonical_decimal(current_price),
                "reference_price": _canonical_decimal(reference_price),
                "change_percent": _canonical_decimal(change_percent),
                "threshold_percent": _canonical_decimal(threshold_percent),
                "occurred_at": occurred_at.isoformat(),
            },
        )
        return cls(
            id=identifier,
            portfolio_id=portfolio_id,
            rule_code=rule_code,
            title=title,
            explanation=explanation,
            severity=severity,
            instrument_id=instrument_id,
            instrument_name=instrument_name,
            currency=currency,
            current_price=current_price,
            reference_price=reference_price,
            change_percent=change_percent,
            threshold_percent=threshold_percent,
            occurred_at=occurred_at,
            provider_symbol=provider_symbol,
            source=source,
        )


@dataclass(frozen=True, slots=True)
class Alert:
    id: str
    event_id: str
    severity: Severity
    title: str
    body: str
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required_text(self.id, "alert.id"))
        object.__setattr__(self, "event_id", _required_text(self.event_id, "alert.event_id"))
        object.__setattr__(self, "title", _required_text(self.title, "alert.title"))
        object.__setattr__(self, "body", _required_text(self.body, "alert.body"))
        if not isinstance(self.severity, Severity):
            raise TypeError("alert.severity debe ser Severity")
        _aware(self.created_at, "alert.created_at")

    @classmethod
    def from_event(cls, event: AnalysisEvent, *, body: str) -> Alert:
        if event.instrument_id is None:
            raise ValueError("el evento de alerta debe identificar un instrumento")
        direction = "up" if event.change_percent >= 0 else "down"
        return cls(
            id=cls.condition_id(
                portfolio_id=event.portfolio_id,
                rule_code=event.rule_code,
                instrument_id=event.instrument_id,
                direction=direction,
            ),
            event_id=event.id,
            severity=event.severity,
            title=f"OpenPortfolio · {event.severity.value}",
            body=body,
            created_at=event.occurred_at,
        )

    @staticmethod
    def condition_id(
        *, portfolio_id: str, rule_code: str, instrument_id: str, direction: str
    ) -> str:
        if direction not in {"up", "down"}:
            raise ValueError("direction debe ser up o down")
        return _stable_identifier(
            "alert-condition",
            {
                "portfolio_id": portfolio_id,
                "rule_code": rule_code,
                "instrument_id": instrument_id,
                "direction": direction,
            },
        )


@dataclass(frozen=True, slots=True)
class OperationalNotification:
    """Operational review outcome; deliberately not a market alert."""

    title: str
    body: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "title",
            _required_text(self.title, "notification.title"),
        )
        object.__setattr__(self, "body", _required_text(self.body, "notification.body"))


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
    source: QuoteSource
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
        if not isinstance(self.source, QuoteSource):
            raise TypeError("quote.source debe ser QuoteSource")
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
