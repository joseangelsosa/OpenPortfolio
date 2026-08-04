from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from collections.abc import Iterable
from typing import Any

from openportfolio.domain import (
    CostBasisStatus,
    ImportedPosition,
    ImportStatus,
    PortfolioSnapshot,
)


SUMMARY_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class PortfolioSummary:
    """Stable, presentation-neutral view of one imported portfolio snapshot."""

    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return self.payload


def summarize_portfolio(snapshot: PortfolioSnapshot) -> PortfolioSummary:
    """Build a deterministic summary without market data or analysis rules."""
    positions = tuple(
        sorted(
            snapshot.positions,
            key=lambda item: (item.source.value, item.source_ticker),
        )
    )
    currencies = _group_counts(position.currency for position in positions)
    asset_types = _group_counts(position.asset_type for position in positions)
    imported_sources = sum(
        metadata.status is ImportStatus.IMPORTED for metadata in snapshot.sources.values()
    )

    return PortfolioSummary(
        {
            "summary_schema_version": SUMMARY_SCHEMA_VERSION,
            "snapshot": {
                "schema_version": snapshot.schema_version,
                "generated_at": snapshot.generated_at.isoformat(),
                "provider": snapshot.provider,
                "sources": [
                    {
                        "source": source.value,
                        "status": metadata.status.value,
                        "updated_at": (
                            metadata.updated_at.isoformat()
                            if metadata.updated_at is not None
                            else None
                        ),
                        "format": metadata.format,
                        "rows_processed": metadata.rows_processed,
                    }
                    for source, metadata in sorted(
                        snapshot.sources.items(), key=lambda item: item[0].value
                    )
                ],
            },
            "metrics": {
                "operations_processed": sum(
                    metadata.rows_processed for metadata in snapshot.sources.values()
                ),
                "positions": {
                    "total": len(positions),
                    "open": len(positions),
                    "closed": None,
                },
                "by_currency": [
                    {"currency": value, "positions": count}
                    for value, count in currencies
                ],
                "by_asset_type": [
                    {"asset_type": value, "positions": count}
                    for value, count in asset_types
                ],
                "reconciliation": {
                    "status": (
                        "complete"
                        if imported_sources == len(snapshot.sources)
                        else "partial"
                    ),
                    "imported_sources": imported_sources,
                    "declared_sources": len(snapshot.sources),
                },
            },
            "import_warnings": {"available": False, "items": None},
            "positions": [_position_payload(position) for position in positions],
            "unavailable_fields": [
                "import_warnings",
                "closed_positions",
                "positions[].accumulated_cost",
                "market_valuation",
            ],
        }
    )


def render_portfolio_summary_text(summary: PortfolioSummary) -> str:
    payload = summary.as_dict()
    snapshot = payload["snapshot"]
    metrics = payload["metrics"]
    position_metrics = metrics["positions"]
    currencies = metrics["by_currency"]
    asset_types = metrics["by_asset_type"]
    reconciliation = metrics["reconciliation"]

    lines = [
        "OpenPortfolio — resumen de snapshot importado",
        "Sin valoración de mercado, reglas del IOS ni consultas externas.",
        "",
        f"Generado: {snapshot['generated_at']}",
        f"Proveedor: {snapshot['provider']}",
        f"Operaciones procesadas: {metrics['operations_processed']}",
        f"Posiciones totales: {position_metrics['total']}",
        f"Posiciones abiertas: {position_metrics['open']}",
        "Posiciones cerradas: no disponible",
        "Monedas: " + _text_groups(currencies, "currency"),
        "Tipos de activo: " + _text_groups(asset_types, "asset_type"),
        (
            "Conciliación: "
            f"{reconciliation['status']} "
            f"({reconciliation['imported_sources']}/{reconciliation['declared_sources']} fuentes)"
        ),
        "Advertencias de importación: no disponible",
        "",
        "Posiciones:",
    ]
    if not payload["positions"]:
        lines.append("- ninguna")
    for position in payload["positions"]:
        unavailable = position["unavailable_fields"]
        lines.extend(
            (
                f"- {position['operational_identifier']}",
                f"  Nombre: {_available(position['name'])}",
                f"  Símbolo de mercado: {_available(position['market_symbol'])}",
                f"  Moneda: {position['currency']}",
                f"  Cantidad: {position['quantity']}",
                f"  Coste medio: {_available(position['cost']['average'])}",
                "  Coste acumulado: no disponible",
                f"  Estado de tenencia: {position['holding_status']}",
                f"  Estado operativo: {position['operational_status']}",
                (
                    "  Campos no disponibles: "
                    + (", ".join(unavailable) if unavailable else "ninguno")
                ),
            )
        )
    return "\n".join(lines) + "\n"


def _position_payload(position: ImportedPosition) -> dict[str, Any]:
    unavailable = ["accumulated_cost"]
    if position.name is None:
        unavailable.append("name")
    if position.market_symbol is None:
        unavailable.append("market_symbol")
    if position.cost_basis_status is CostBasisStatus.UNAVAILABLE:
        unavailable.append("average_cost")
    return {
        "operational_identifier": position.source_ticker,
        "name": position.name,
        "market_symbol": position.market_symbol,
        "source": position.source.value,
        "asset_type": position.asset_type,
        "currency": position.currency,
        "quantity": _decimal(position.quantity),
        "cost": {
            "average": (
                _decimal(position.average_cost)
                if position.average_cost is not None
                else None
            ),
            "accumulated": None,
            "basis_status": position.cost_basis_status.value,
        },
        "holding_status": "open",
        "operational_status": position.position_status.value,
        "tradable": position.tradable,
        "active_monitoring": position.active_monitoring,
        "exclusion_reason": position.exclusion_reason,
        "unavailable_fields": unavailable,
    }


def _group_counts(values: Iterable[str]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return sorted(counts.items())


def _text_groups(groups: list[dict[str, Any]], key: str) -> str:
    if not groups:
        return "ninguna"
    return ", ".join(f"{group[key]} ({group['positions']})" for group in groups)


def _available(value: str | None) -> str:
    return value if value is not None else "no disponible"


def _decimal(value: Decimal) -> str:
    return format(value, "f")
