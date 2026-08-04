from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from openportfolio.application.market_mapping_validation import validate_market_mapping
from openportfolio.domain import (
    CostBasisStatus,
    ImportedPosition,
    Instrument,
    MarketMapping,
    MarketQuote,
    PortfolioSnapshot,
)
from openportfolio.market_data import MarketDataError, MarketDataProvider


PORTFOLIO_VALUATION_SCHEMA_VERSION = 1
NO_CURRENCY_CONVERSION_WARNING = (
    "No existe conversión entre monedas; los subtotales se mantienen separados."
)


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


@dataclass(frozen=True, slots=True)
class ValuationError:
    code: str
    message: str
    instrument_id: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "code": self.code,
            "instrument_id": self.instrument_id,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class ValuedPosition:
    instrument_id: str
    quantity: Decimal
    average_cost: Decimal | None
    accumulated_cost: Decimal | None
    market_price: Decimal | None
    market_value: Decimal | None
    unrealized_gain_loss: Decimal | None
    return_percent: Decimal | None
    currency: str
    quote: MarketQuote | None
    status: str
    unavailable_fields: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        quote = self.quote
        return {
            "instrument_id": self.instrument_id,
            "quantity": _decimal(self.quantity),
            "average_cost": _decimal(self.average_cost),
            "accumulated_cost": _decimal(self.accumulated_cost),
            "market_price": _decimal(self.market_price),
            "market_value": _decimal(self.market_value),
            "unrealized_gain_loss": _decimal(self.unrealized_gain_loss),
            "return_percent": _decimal(self.return_percent),
            "currency": self.currency,
            "quote": (
                None
                if quote is None
                else {
                    "observed_at": quote.observed_at.isoformat(),
                    "retrieved_at": quote.retrieved_at.isoformat(),
                    "provider": quote.provider,
                    "provider_symbol": quote.provider_symbol,
                    "source": quote.source.value,
                    "kind": quote.kind,
                }
            ),
            "status": self.status,
            "unavailable_fields": list(self.unavailable_fields),
        }


@dataclass(frozen=True, slots=True)
class ValuationExclusion:
    instrument_id: str
    status: str = "excluded"
    reason: str = "market_mapping_disabled"

    def as_dict(self) -> dict[str, str]:
        return {
            "instrument_id": self.instrument_id,
            "status": self.status,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class CurrencyTotal:
    currency: str
    enabled_positions: int
    valued_positions: int
    accumulated_cost: Decimal | None
    market_value: Decimal | None
    unrealized_gain_loss: Decimal | None
    return_percent: Decimal | None
    unavailable_fields: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "currency": self.currency,
            "enabled_positions": self.enabled_positions,
            "valued_positions": self.valued_positions,
            "accumulated_cost": _decimal(self.accumulated_cost),
            "market_value": _decimal(self.market_value),
            "unrealized_gain_loss": _decimal(self.unrealized_gain_loss),
            "return_percent": _decimal(self.return_percent),
            "unavailable_fields": list(self.unavailable_fields),
        }


@dataclass(frozen=True, slots=True)
class PortfolioValuation:
    executed_at: datetime
    snapshot_generated_at: datetime
    positions_total: int
    enabled_positions: int
    valued_positions: int
    excluded_positions: int
    failed_positions: int
    partially_calculable_positions: int
    missing_mapping_positions: int
    mapping_currency_mismatches: int
    providers: tuple[str, ...]
    quote_types: tuple[str, ...]
    currency_totals: tuple[CurrencyTotal, ...]
    positions: tuple[ValuedPosition, ...]
    exclusions: tuple[ValuationExclusion, ...]
    warnings: tuple[str, ...]
    errors: tuple[ValuationError, ...]
    unavailable_fields: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors and self.failed_positions == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "valuation_schema_version": PORTFOLIO_VALUATION_SCHEMA_VERSION,
            "metadata": {
                "executed_at": self.executed_at.isoformat(),
                "snapshot_generated_at": self.snapshot_generated_at.isoformat(),
                "providers": list(self.providers),
                "quote_types": list(self.quote_types),
                "currency_conversion": False,
            },
            "coverage": {
                "positions_total": self.positions_total,
                "positions_enabled": self.enabled_positions,
                "positions_valued": self.valued_positions,
                "positions_excluded": self.excluded_positions,
                "positions_failed": self.failed_positions,
                "positions_partially_calculable": self.partially_calculable_positions,
                "positions_without_mapping": self.missing_mapping_positions,
                "mapping_currency_mismatches": self.mapping_currency_mismatches,
            },
            "currency_totals": [item.as_dict() for item in self.currency_totals],
            "positions": [item.as_dict() for item in self.positions],
            "exclusions": [item.as_dict() for item in self.exclusions],
            "warnings": list(self.warnings),
            "errors": [item.as_dict() for item in self.errors],
            "unavailable_fields": list(self.unavailable_fields),
        }


ProviderResolver = Callable[[str], MarketDataProvider]


def value_portfolio(
    snapshot: PortfolioSnapshot,
    mapping: MarketMapping,
    provider_resolver: ProviderResolver,
    *,
    now: Callable[[], datetime] | None = None,
) -> PortfolioValuation:
    """Value an imported snapshot without persistence, analysis, or notifications."""
    executed_at = (now or (lambda: datetime.now(timezone.utc)))()
    if executed_at.tzinfo is None or executed_at.utcoffset() is None:
        raise ValueError("el instante de valoración debe incluir zona horaria")
    executed_at = executed_at.astimezone(timezone.utc)
    validation = validate_market_mapping(snapshot, mapping)
    ordered_positions = tuple(
        sorted(snapshot.positions, key=lambda item: (item.source_ticker, item.source.value))
    )
    exclusions = tuple(
        ValuationExclusion(position.source_ticker)
        for position in ordered_positions
        if (
            (entry := mapping.instruments.get(position.source_ticker)) is not None
            and not entry.enabled
        )
    )

    if not validation.ready_for_market_valuation:
        errors = tuple(
            [
                ValuationError(
                    "missing_mapping",
                    "La posición no tiene una correspondencia de mercado.",
                    identifier,
                )
                for identifier in validation.missing_identifiers
            ]
            + [
                ValuationError(
                    "mapping_currency_mismatch",
                    "La moneda del snapshot no coincide con la esperada por el mapping.",
                    mismatch.instrument_id,
                )
                for mismatch in validation.currency_mismatches
            ]
        )
        return PortfolioValuation(
            executed_at=executed_at,
            snapshot_generated_at=snapshot.generated_at,
            positions_total=validation.total_positions,
            enabled_positions=validation.enabled_positions,
            valued_positions=0,
            excluded_positions=validation.excluded_positions,
            failed_positions=validation.enabled_positions,
            partially_calculable_positions=0,
            missing_mapping_positions=validation.missing_positions,
            mapping_currency_mismatches=len(validation.currency_mismatches),
            providers=validation.providers,
            quote_types=(),
            currency_totals=(),
            positions=(),
            exclusions=exclusions,
            warnings=(NO_CURRENCY_CONVERSION_WARNING,),
            errors=errors,
            unavailable_fields=("currency_totals", "positions"),
        )

    providers: dict[str, MarketDataProvider] = {}
    valued: list[ValuedPosition] = []
    errors: list[ValuationError] = []
    for position in ordered_positions:
        entry = mapping.instruments[position.source_ticker]
        if not entry.enabled:
            continue
        assert entry.provider is not None
        assert entry.market_symbol is not None
        assert entry.expected_currency is not None
        instrument = Instrument(
            id=position.source_ticker,
            name=position.name or position.source_ticker,
            asset_type=position.asset_type,
            currency=entry.expected_currency,
            provider_symbols={entry.provider: entry.market_symbol},
        )
        accumulated_cost = (
            position.quantity * position.average_cost
            if position.cost_basis_status is CostBasisStatus.AVAILABLE
            and position.average_cost is not None
            else None
        )
        try:
            provider = providers.get(entry.provider)
            if provider is None:
                provider = provider_resolver(entry.provider)
                providers[entry.provider] = provider
            if provider.name.lower() != entry.provider:
                raise ValueError("el proveedor resuelto no corresponde al configurado")
            quote = provider.get_quote(instrument)
        except (ImportError, MarketDataError, ValueError):
            errors.append(
                ValuationError(
                    "quote_failed",
                    "No se pudo obtener una cotización válida del proveedor configurado.",
                    position.source_ticker,
                )
            )
            valued.append(
                _failed_position(position, entry.expected_currency, accumulated_cost)
            )
            continue
        if (
            not isinstance(quote, MarketQuote)
            or quote.instrument_id != instrument.id
            or quote.provider.lower() != entry.provider
        ):
            errors.append(
                ValuationError(
                    "invalid_quote",
                    "El proveedor devolvió una cotización no válida para la posición.",
                    position.source_ticker,
                )
            )
            valued.append(
                _failed_position(position, entry.expected_currency, accumulated_cost)
            )
            continue
        if quote.currency != entry.expected_currency:
            errors.append(
                ValuationError(
                    "quote_currency_mismatch",
                    "La moneda de la cotización no coincide con la esperada.",
                    position.source_ticker,
                )
            )
            valued.append(
                _failed_position(position, entry.expected_currency, accumulated_cost)
            )
            continue

        market_value = position.quantity * quote.price
        gain = None if accumulated_cost is None else market_value - accumulated_cost
        return_percent = (
            None
            if gain is None or accumulated_cost == 0
            else (gain / accumulated_cost) * Decimal("100")
        )
        unavailable: list[str] = []
        if position.average_cost is None:
            unavailable.extend(
                ("average_cost", "accumulated_cost", "unrealized_gain_loss", "return_percent")
            )
        elif return_percent is None:
            unavailable.append("return_percent")
        valued.append(
            ValuedPosition(
                instrument_id=position.source_ticker,
                quantity=position.quantity,
                average_cost=position.average_cost,
                accumulated_cost=accumulated_cost,
                market_price=quote.price,
                market_value=market_value,
                unrealized_gain_loss=gain,
                return_percent=return_percent,
                currency=entry.expected_currency,
                quote=quote,
                status="valued" if not unavailable else "partial",
                unavailable_fields=tuple(unavailable),
            )
        )

    position_items = tuple(valued)
    totals = _currency_totals(position_items)
    unavailable_fields = tuple(
        sorted(
            {
                f"positions[].{field}"
                for item in position_items
                for field in item.unavailable_fields
            }
            | {
                f"currency_totals[].{field}"
                for item in totals
                for field in item.unavailable_fields
            }
        )
    )
    successful = tuple(item for item in position_items if item.quote is not None)
    return PortfolioValuation(
        executed_at=executed_at,
        snapshot_generated_at=snapshot.generated_at,
        positions_total=validation.total_positions,
        enabled_positions=validation.enabled_positions,
        valued_positions=len(successful),
        excluded_positions=validation.excluded_positions,
        failed_positions=sum(item.status == "failed" for item in position_items),
        partially_calculable_positions=sum(item.status == "partial" for item in position_items),
        missing_mapping_positions=0,
        mapping_currency_mismatches=0,
        providers=validation.providers,
        quote_types=tuple(sorted({item.quote.kind for item in successful if item.quote})),
        currency_totals=totals,
        positions=position_items,
        exclusions=exclusions,
        warnings=(NO_CURRENCY_CONVERSION_WARNING,),
        errors=tuple(errors),
        unavailable_fields=unavailable_fields,
    )


def _failed_position(
    position: ImportedPosition, currency: str, cost: Decimal | None
) -> ValuedPosition:
    unavailable = ["market_price", "market_value", "unrealized_gain_loss", "return_percent"]
    if position.average_cost is None:
        unavailable[0:0] = ["average_cost", "accumulated_cost"]
    return ValuedPosition(
        instrument_id=position.source_ticker,
        quantity=position.quantity,
        average_cost=position.average_cost,
        accumulated_cost=cost,
        market_price=None,
        market_value=None,
        unrealized_gain_loss=None,
        return_percent=None,
        currency=currency,
        quote=None,
        status="failed",
        unavailable_fields=tuple(unavailable),
    )


def _currency_totals(positions: tuple[ValuedPosition, ...]) -> tuple[CurrencyTotal, ...]:
    currencies = sorted({position.currency for position in positions})
    results: list[CurrencyTotal] = []
    for currency in currencies:
        items = tuple(position for position in positions if position.currency == currency)
        cost = _complete_sum(item.accumulated_cost for item in items)
        value = _complete_sum(item.market_value for item in items)
        gain = _complete_sum(item.unrealized_gain_loss for item in items)
        return_percent = (
            None if cost is None or gain is None or cost == 0 else (gain / cost) * Decimal("100")
        )
        fields = (
            ("accumulated_cost", cost),
            ("market_value", value),
            ("unrealized_gain_loss", gain),
            ("return_percent", return_percent),
        )
        results.append(
            CurrencyTotal(
                currency=currency,
                enabled_positions=len(items),
                valued_positions=sum(item.quote is not None for item in items),
                accumulated_cost=cost,
                market_value=value,
                unrealized_gain_loss=gain,
                return_percent=return_percent,
                unavailable_fields=tuple(name for name, field_value in fields if field_value is None),
            )
        )
    return tuple(results)


def _complete_sum(values: Iterable[Decimal | None]) -> Decimal | None:
    items = tuple(values)
    if any(value is None for value in items):
        return None
    return sum(items, Decimal("0"))


def render_portfolio_valuation_text(valuation: PortfolioValuation) -> str:
    lines = [
        "OpenPortfolio — valoración actual de cartera importada",
        f"Ejecución: {valuation.executed_at.isoformat()}",
        "",
        "Cobertura:",
        f"  Posiciones totales: {valuation.positions_total}",
        f"  Habilitadas: {valuation.enabled_positions}",
        f"  Valoradas: {valuation.valued_positions}",
        f"  Excluidas: {valuation.excluded_positions}",
        f"  Fallidas: {valuation.failed_positions}",
        f"  Parcialmente calculables: {valuation.partially_calculable_positions}",
        "",
        "Subtotales por moneda:",
    ]
    if not valuation.currency_totals:
        lines.append("  ninguno disponible")
    for total in valuation.currency_totals:
        lines.append(
            f"  {total.currency} | habilitadas: {total.enabled_positions} | "
            f"valoradas: {total.valued_positions} | coste: {_available(total.accumulated_cost)} | "
            f"valor: {_available(total.market_value)} | "
            f"ganancia/pérdida: {_available(total.unrealized_gain_loss)} | "
            f"rentabilidad: {_percent(total.return_percent)}"
        )
    lines.extend(("", "Posiciones habilitadas:"))
    if not valuation.positions:
        lines.append("  ninguna disponible")
    for position in valuation.positions:
        quote = position.quote
        quote_text = (
            "cotización no disponible"
            if quote is None
            else (
                f"precio: {_available(position.market_price)} {position.currency} | "
                f"observada: {quote.observed_at.isoformat()} | proveedor: {quote.provider} | "
                f"origen: {quote.source.value}/{quote.kind}"
            )
        )
        lines.append(
            f"  {position.instrument_id} | estado: {position.status} | "
            f"cantidad: {_available(position.quantity)} | coste medio: "
            f"{_available(position.average_cost)} | coste: {_available(position.accumulated_cost)} | "
            f"{quote_text} | valor: {_available(position.market_value)} | "
            f"ganancia/pérdida: {_available(position.unrealized_gain_loss)} | "
            f"rentabilidad: {_percent(position.return_percent)}"
        )
    lines.extend(("", "Exclusiones:"))
    if not valuation.exclusions:
        lines.append("  ninguna")
    else:
        lines.extend(
            f"  {item.instrument_id} | estado: excluida | motivo: mapping deshabilitado"
            for item in valuation.exclusions
        )
    if valuation.errors:
        lines.extend(("", "Errores:"))
        lines.extend(
            f"  {item.instrument_id or 'general'} | {item.code} | {item.message}"
            for item in valuation.errors
        )
    lines.extend(("", "Advertencias:"))
    lines.extend(f"  {warning}" for warning in valuation.warnings)
    if valuation.unavailable_fields:
        lines.append("Campos no disponibles: " + ", ".join(valuation.unavailable_fields))
    return "\n".join(lines) + "\n"


def _available(value: Decimal | None) -> str:
    return "no disponible" if value is None else format(value, "f")


def _percent(value: Decimal | None) -> str:
    return "no disponible" if value is None else f"{format(value, 'f')} %"
