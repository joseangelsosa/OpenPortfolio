from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import yaml

from openportfolio.analysis import PriceReferenceThresholds
from openportfolio.domain import Instrument, Portfolio, Position


class PortfolioConfigurationError(ValueError):
    """The YAML document does not satisfy the Phase 1 configuration contract."""


@dataclass(frozen=True, slots=True)
class PortfolioConfiguration:
    portfolio: Portfolio
    fake_prices: Mapping[str, Decimal]
    price_reference_rules: Mapping[str, PriceReferenceThresholds]


def load_portfolio(path: str | Path) -> PortfolioConfiguration:
    source = Path(path)
    try:
        with source.open(encoding="utf-8") as stream:
            raw = yaml.safe_load(stream)
    except OSError as error:
        raise PortfolioConfigurationError(f"no se puede leer {source}: {error}") from error
    except yaml.YAMLError as error:
        raise PortfolioConfigurationError(f"YAML inválido en {source}: {error}") from error
    try:
        root = _mapping(raw, "raíz")
        portfolio_data = _mapping(root.get("portfolio"), "portfolio")
        portfolio_id = _text(portfolio_data.get("id"), "portfolio.id")
        instruments = tuple(
            _instrument(item, index)
            for index, item in enumerate(_list(root.get("instruments"), "instruments"))
        )
        positions_data = _list(root.get("positions", []), "positions", allow_empty=True)
        positions = tuple(
            _position(item, index, portfolio_id)
            for index, item in enumerate(positions_data)
        )
        portfolio = Portfolio(
            id=portfolio_id,
            name=_text(portfolio_data.get("name"), "portfolio.name"),
            base_currency=_text(portfolio_data.get("base_currency"), "portfolio.base_currency"),
            instruments=instruments,
            positions=positions,
        )
        fake_prices_data = _mapping(root.get("fake_market_data"), "fake_market_data")
        fake_prices = {
            _text(symbol, "fake_market_data símbolo"): _decimal(
                price, f"fake_market_data.{symbol}"
            )
            for symbol, price in fake_prices_data.items()
        }
        if any(price <= 0 for price in fake_prices.values()):
            raise PortfolioConfigurationError("los precios ficticios deben ser mayores que cero")
        price_reference_rules: dict[str, PriceReferenceThresholds] = {}
        known_instrument_ids = {instrument.id for instrument in instruments}
        for index, (position, raw_position) in enumerate(
            zip(positions, positions_data, strict=True)
        ):
            thresholds = _price_reference_thresholds(raw_position, index)
            if thresholds is not None:
                price_reference_rules[position.instrument_id] = thresholds
        for index, raw_rule in enumerate(
            _list(root.get("review_rules", []), "review_rules", allow_empty=True)
        ):
            data = _mapping(raw_rule, f"review_rules[{index}]")
            instrument_id = _text(
                data.get("instrument_id"), f"review_rules[{index}].instrument_id"
            )
            if instrument_id not in known_instrument_ids:
                raise PortfolioConfigurationError(
                    f"review_rules[{index}] referencia un instrumento desconocido"
                )
            enabled = data.get("enabled", True)
            if not isinstance(enabled, bool):
                raise PortfolioConfigurationError(
                    f"review_rules[{index}].enabled debe ser booleano"
                )
            if not enabled:
                continue
            if instrument_id in price_reference_rules:
                raise PortfolioConfigurationError(
                    f"hay más de una regla para el instrumento {instrument_id!r}"
                )
            price_reference_rules[instrument_id] = _required_price_reference_thresholds(
                data, f"review_rules[{index}]"
            )
        return PortfolioConfiguration(
            portfolio=portfolio,
            fake_prices=MappingProxyType(fake_prices),
            price_reference_rules=MappingProxyType(price_reference_rules),
        )
    except (TypeError, ValueError) as error:
        if isinstance(error, PortfolioConfigurationError):
            raise
        raise PortfolioConfigurationError(f"configuración inválida en {source}: {error}") from error


def _instrument(raw: Any, index: int) -> Instrument:
    data = _mapping(raw, f"instruments[{index}]")
    symbols_data = _mapping(data.get("provider_symbols"), f"instruments[{index}].provider_symbols")
    symbols = {
        _text(provider, "provider"): _text(symbol, "provider_symbol")
        for provider, symbol in symbols_data.items()
    }
    active = data.get("active", True)
    if not isinstance(active, bool):
        raise PortfolioConfigurationError(f"instruments[{index}].active debe ser booleano")
    return Instrument(
        id=_text(data.get("id"), f"instruments[{index}].id"),
        name=_text(data.get("name"), f"instruments[{index}].name"),
        asset_type=_text(data.get("asset_type"), f"instruments[{index}].asset_type"),
        currency=_text(data.get("currency"), f"instruments[{index}].currency"),
        provider_symbols=symbols,
        active=active,
    )


def _position(raw: Any, index: int, portfolio_id: str) -> Position:
    data = _mapping(raw, f"positions[{index}]")
    configured_portfolio_id = data.get("portfolio_id", portfolio_id)
    return Position(
        id=_text(data.get("id"), f"positions[{index}].id"),
        portfolio_id=_text(configured_portfolio_id, f"positions[{index}].portfolio_id"),
        instrument_id=_text(data.get("instrument_id"), f"positions[{index}].instrument_id"),
        quantity=_decimal(data.get("quantity"), f"positions[{index}].quantity"),
        as_of=_timestamp(data.get("as_of"), f"positions[{index}].as_of"),
    )


def _price_reference_thresholds(
    raw: Any, index: int
) -> PriceReferenceThresholds | None:
    data = _mapping(raw, f"positions[{index}]")
    required = ("reference_price", "review_change_percent", "high_change_percent")
    missing = [field for field in required if field not in data]
    if len(missing) == len(required):
        return None
    if missing:
        raise PortfolioConfigurationError(
            f"positions[{index}] debe configurar juntos {', '.join(required)}"
        )
    return PriceReferenceThresholds(
        reference_price=_decimal(
            data["reference_price"], f"positions[{index}].reference_price"
        ),
        review_change_percent=_decimal(
            data["review_change_percent"], f"positions[{index}].review_change_percent"
        ),
        high_change_percent=_decimal(
            data["high_change_percent"], f"positions[{index}].high_change_percent"
        ),
    )


def _required_price_reference_thresholds(
    data: dict[Any, Any], field_name: str
) -> PriceReferenceThresholds:
    required = ("reference_price", "review_change_percent", "high_change_percent")
    missing = [field for field in required if field not in data]
    if missing:
        raise PortfolioConfigurationError(
            f"{field_name} debe configurar {', '.join(required)} cuando está activa"
        )
    return PriceReferenceThresholds(
        reference_price=_decimal(data["reference_price"], f"{field_name}.reference_price"),
        review_change_percent=_decimal(
            data["review_change_percent"], f"{field_name}.review_change_percent"
        ),
        high_change_percent=_decimal(
            data["high_change_percent"], f"{field_name}.high_change_percent"
        ),
    )


def _mapping(value: Any, field_name: str) -> dict[Any, Any]:
    if not isinstance(value, dict):
        raise PortfolioConfigurationError(f"{field_name} debe ser un mapa")
    return value


def _list(value: Any, field_name: str, *, allow_empty: bool = False) -> list[Any]:
    if not isinstance(value, list) or (not value and not allow_empty):
        requirement = "una lista" if allow_empty else "una lista no vacía"
        raise PortfolioConfigurationError(f"{field_name} debe ser {requirement}")
    return value


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PortfolioConfigurationError(f"{field_name} debe ser texto no vacío")
    return value.strip()


def _decimal(value: Any, field_name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise PortfolioConfigurationError(
            f"{field_name} debe ser un decimal escrito como texto para preservar precisión"
        )
    try:
        decimal = Decimal(str(value))
    except InvalidOperation as error:
        raise PortfolioConfigurationError(f"{field_name} no es un decimal válido") from error
    if not decimal.is_finite():
        raise PortfolioConfigurationError(f"{field_name} debe ser finito")
    return decimal


def _timestamp(value: Any, field_name: str) -> datetime:
    text = _text(value, field_name)
    try:
        timestamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise PortfolioConfigurationError(f"{field_name} no es un timestamp ISO 8601") from error
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise PortfolioConfigurationError(f"{field_name} debe incluir zona horaria")
    return timestamp
