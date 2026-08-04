from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from openportfolio.domain import (
    MARKET_MAPPING_VERSION,
    MarketMapping,
    MarketMappingEntry,
)
from openportfolio.market_data import SUPPORTED_MAPPING_PROVIDER_NAMES


_ROOT_FIELDS = frozenset({"version", "instruments"})
_ENTRY_FIELDS = frozenset(
    {"market_symbol", "provider", "expected_currency", "enabled"}
)


class MarketMappingError(RuntimeError):
    """A market mapping could not be loaded or validated safely."""


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[object, object]:
    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as error:
            raise MarketMappingError("una clave del mapping no es válida") from error
        if duplicate:
            raise MarketMappingError("el mapping contiene una clave duplicada")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def load_market_mapping(path: str | Path) -> MarketMapping:
    source = Path(path)
    try:
        with source.open(encoding="utf-8") as stream:
            raw = yaml.load(stream, Loader=_UniqueKeyLoader)
        return _decode(raw)
    except MarketMappingError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError, TypeError, ValueError) as error:
        raise MarketMappingError("no se puede leer o interpretar el mapping") from error


def _decode(raw: object) -> MarketMapping:
    root = _mapping(raw, "raíz")
    _reject_unknown_fields(root, _ROOT_FIELDS, "raíz")
    version = root.get("version")
    if type(version) is not int or version != MARKET_MAPPING_VERSION:
        raise MarketMappingError("version no soportada")
    raw_instruments = _mapping(root.get("instruments"), "instruments")
    entries: dict[str, MarketMappingEntry] = {}
    for raw_identifier, raw_entry in raw_instruments.items():
        identifier = _text(raw_identifier, "identificador de instrumento")
        if identifier in entries:
            raise MarketMappingError(
                "los identificadores de instrumento deben ser únicos"
            )
        data = _mapping(raw_entry, "entrada de instrumento")
        _reject_unknown_fields(data, _ENTRY_FIELDS, "entrada de instrumento")
        enabled = data.get("enabled")
        if not isinstance(enabled, bool):
            raise MarketMappingError("enabled debe ser booleano")
        if enabled:
            symbol = _text(data.get("market_symbol"), "market_symbol")
            provider = _text(data.get("provider"), "provider").lower()
            if provider not in SUPPORTED_MAPPING_PROVIDER_NAMES:
                raise MarketMappingError("provider no está admitido")
            currency = _currency(data.get("expected_currency"))
            entry = MarketMappingEntry(
                enabled=True,
                market_symbol=symbol,
                provider=provider,
                expected_currency=currency,
            )
        else:
            if any(
                field in data
                for field in ("market_symbol", "provider", "expected_currency")
            ):
                raise MarketMappingError(
                    "una entrada excluida no puede declarar datos de mercado"
                )
            entry = MarketMappingEntry(enabled=False)
        entries[identifier] = entry
    try:
        return MarketMapping(version=version, instruments=entries)
    except (TypeError, ValueError) as error:
        raise MarketMappingError(f"mapping inválido: {error}") from error


def _mapping(value: object, field_name: str) -> dict[Any, Any]:
    if not isinstance(value, dict):
        raise MarketMappingError(f"{field_name} debe ser un mapa")
    return value


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MarketMappingError(f"{field_name} debe ser texto no vacío")
    return value.strip()


def _currency(value: object) -> str:
    currency = _text(value, "expected_currency").upper()
    if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
        raise MarketMappingError(
            "expected_currency debe ser un código de tres letras"
        )
    return currency


def _reject_unknown_fields(
    data: dict[Any, Any], allowed: frozenset[str], field_name: str
) -> None:
    if any(not isinstance(key, str) or key not in allowed for key in data):
        raise MarketMappingError(f"{field_name} contiene campos desconocidos")
