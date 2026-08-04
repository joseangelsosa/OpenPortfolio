from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openportfolio.domain import MarketMapping, PortfolioSnapshot


MARKET_MAPPING_VALIDATION_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class CurrencyMismatch:
    instrument_id: str
    snapshot_currency: str
    expected_currency: str

    def as_dict(self) -> dict[str, str]:
        return {
            "instrument_id": self.instrument_id,
            "snapshot_currency": self.snapshot_currency,
            "expected_currency": self.expected_currency,
        }


@dataclass(frozen=True, slots=True)
class MarketMappingValidation:
    total_positions: int
    enabled_positions: int
    excluded_positions: int
    missing_positions: int
    missing_identifiers: tuple[str, ...]
    unused_identifiers: tuple[str, ...]
    currency_mismatches: tuple[CurrencyMismatch, ...]
    providers: tuple[str, ...]

    @property
    def ready_for_market_valuation(self) -> bool:
        return self.missing_positions == 0 and not self.currency_mismatches

    def as_dict(self) -> dict[str, Any]:
        return {
            "validation_schema_version": MARKET_MAPPING_VALIDATION_SCHEMA_VERSION,
            "ready_for_market_valuation": self.ready_for_market_valuation,
            "metrics": {
                "positions_total": self.total_positions,
                "positions_with_enabled_mapping": self.enabled_positions,
                "positions_explicitly_excluded": self.excluded_positions,
                "positions_without_mapping": self.missing_positions,
                "unused_mapping_entries": len(self.unused_identifiers),
                "currency_mismatches": len(self.currency_mismatches),
            },
            "providers": list(self.providers),
            "issues": {
                "positions_without_mapping": list(self.missing_identifiers),
                "unused_mapping_entries": list(self.unused_identifiers),
                "currency_mismatches": [
                    mismatch.as_dict() for mismatch in self.currency_mismatches
                ],
            },
        }


def validate_market_mapping(
    snapshot: PortfolioSnapshot, mapping: MarketMapping
) -> MarketMappingValidation:
    enabled = 0
    excluded = 0
    missing = 0
    missing_ids: set[str] = set()
    used_ids: set[str] = set()
    providers: set[str] = set()
    mismatches: list[CurrencyMismatch] = []

    for position in sorted(
        snapshot.positions,
        key=lambda item: (item.source_ticker, item.source.value),
    ):
        identifier = position.source_ticker
        entry = mapping.instruments.get(identifier)
        if entry is None:
            missing += 1
            missing_ids.add(identifier)
            continue
        used_ids.add(identifier)
        if not entry.enabled:
            excluded += 1
            continue
        enabled += 1
        assert entry.provider is not None
        assert entry.expected_currency is not None
        providers.add(entry.provider)
        if position.currency != entry.expected_currency:
            mismatches.append(
                CurrencyMismatch(
                    instrument_id=identifier,
                    snapshot_currency=position.currency,
                    expected_currency=entry.expected_currency,
                )
            )

    mismatches.sort(
        key=lambda item: (
            item.instrument_id,
            item.snapshot_currency,
            item.expected_currency,
        )
    )
    return MarketMappingValidation(
        total_positions=len(snapshot.positions),
        enabled_positions=enabled,
        excluded_positions=excluded,
        missing_positions=missing,
        missing_identifiers=tuple(sorted(missing_ids)),
        unused_identifiers=tuple(sorted(set(mapping.instruments) - used_ids)),
        currency_mismatches=tuple(mismatches),
        providers=tuple(sorted(providers)),
    )


def render_market_mapping_validation_text(
    validation: MarketMappingValidation,
) -> str:
    lines = [
        "OpenPortfolio — validación de correspondencias de mercado",
        "Sin consultas de cotizaciones ni modificaciones de archivos.",
        "",
        f"Posiciones totales: {validation.total_positions}",
        f"Correspondencias habilitadas: {validation.enabled_positions}",
        f"Exclusiones explícitas: {validation.excluded_positions}",
        f"Posiciones sin correspondencia: {validation.missing_positions}",
        f"Entradas no presentes en el snapshot: {len(validation.unused_identifiers)}",
        f"Incompatibilidades de moneda: {len(validation.currency_mismatches)}",
        f"Proveedores utilizados: {_items(validation.providers)}",
        "",
    ]
    if validation.missing_identifiers:
        lines.append(
            "Identificadores sin correspondencia: "
            + ", ".join(validation.missing_identifiers)
        )
    if validation.unused_identifiers:
        lines.append(
            "Identificadores sobrantes: " + ", ".join(validation.unused_identifiers)
        )
    if validation.currency_mismatches:
        lines.append("Monedas incompatibles:")
        lines.extend(
            f"- {item.instrument_id}: snapshot {item.snapshot_currency}; "
            f"esperada {item.expected_currency}"
            for item in validation.currency_mismatches
        )
    lines.append(
        "Configuración lista para futura valoración: "
        + ("sí" if validation.ready_for_market_valuation else "no")
    )
    return "\n".join(lines) + "\n"


def _items(values: tuple[str, ...]) -> str:
    return ", ".join(values) if values else "ninguno"
