from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


MARKET_MAPPING_VERSION = 1


def _required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} debe ser texto no vacío")
    return value.strip()


def _currency(value: str) -> str:
    normalized = _required_text(value, "expected_currency").upper()
    if len(normalized) != 3 or not normalized.isascii() or not normalized.isalpha():
        raise ValueError("expected_currency debe ser un código de tres letras")
    return normalized


@dataclass(frozen=True, slots=True)
class MarketMappingEntry:
    """Provider-specific quote coordinates for one imported instrument."""

    enabled: bool
    market_symbol: str | None = None
    provider: str | None = None
    expected_currency: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise TypeError("enabled debe ser booleano")
        market_fields = (self.market_symbol, self.provider, self.expected_currency)
        if self.enabled:
            if any(value is None for value in market_fields):
                raise ValueError(
                    "una entrada habilitada debe declarar símbolo, proveedor y moneda"
                )
            assert self.market_symbol is not None
            assert self.provider is not None
            assert self.expected_currency is not None
            object.__setattr__(
                self, "market_symbol", _required_text(self.market_symbol, "market_symbol")
            )
            object.__setattr__(
                self, "provider", _required_text(self.provider, "provider").lower()
            )
            object.__setattr__(
                self, "expected_currency", _currency(self.expected_currency)
            )
        elif any(value is not None for value in market_fields):
            raise ValueError(
                "una entrada excluida no puede declarar símbolo, proveedor ni moneda"
            )


@dataclass(frozen=True, slots=True)
class MarketMapping:
    version: int
    instruments: Mapping[str, MarketMappingEntry]

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != MARKET_MAPPING_VERSION:
            raise ValueError(f"version no soportada: {self.version!r}")
        normalized: dict[str, MarketMappingEntry] = {}
        for instrument_id, entry in self.instruments.items():
            identifier = _required_text(instrument_id, "identificador de instrumento")
            if identifier in normalized:
                raise ValueError("los identificadores de instrumento deben ser únicos")
            if not isinstance(entry, MarketMappingEntry):
                raise TypeError("cada instrumento debe contener una correspondencia válida")
            normalized[identifier] = entry
        object.__setattr__(self, "instruments", MappingProxyType(normalized))
