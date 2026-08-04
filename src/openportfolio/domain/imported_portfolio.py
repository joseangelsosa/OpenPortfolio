from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping


SNAPSHOT_SCHEMA_VERSION = 1


class ImportSource(StrEnum):
    INVESTMENTS = "revolut_investments"
    XAU_STATEMENT = "revolut_xau_statement"


class ImportStatus(StrEnum):
    IMPORTED = "imported"
    NOT_IMPORTED = "not_imported"


class CostBasisStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class PositionStatus(StrEnum):
    ACTIVE = "active"
    LEGACY = "legacy"


@dataclass(frozen=True, slots=True)
class ImportedPosition:
    asset_type: str
    source_ticker: str
    market_symbol: str | None
    quantity: Decimal
    currency: str
    average_cost: Decimal | None
    cost_basis_status: CostBasisStatus
    source: ImportSource
    name: str | None = None
    position_status: PositionStatus = PositionStatus.ACTIVE
    tradable: bool = True
    active_monitoring: bool = True
    exclusion_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.asset_type.strip():
            raise ValueError("asset_type no puede estar vacío")
        if not self.source_ticker.strip():
            raise ValueError("source_ticker no puede estar vacío")
        if self.market_symbol is not None and not self.market_symbol.strip():
            raise ValueError("market_symbol no puede estar vacío")
        if not isinstance(self.quantity, Decimal) or not self.quantity.is_finite():
            raise TypeError("quantity debe ser Decimal finito")
        if self.quantity <= 0:
            raise ValueError("quantity debe ser mayor que cero")
        currency = self.currency.strip().upper()
        if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
            raise ValueError("currency debe ser un código de tres letras")
        object.__setattr__(self, "currency", currency)
        if not isinstance(self.cost_basis_status, CostBasisStatus):
            raise TypeError("cost_basis_status no es válido")
        if not isinstance(self.source, ImportSource):
            raise TypeError("source no es válido")
        if self.name is not None and not self.name.strip():
            raise ValueError("name no puede estar vacío")
        if not isinstance(self.position_status, PositionStatus):
            raise TypeError("position_status no es válido")
        if not isinstance(self.tradable, bool) or not isinstance(self.active_monitoring, bool):
            raise TypeError("tradable y active_monitoring deben ser booleanos")
        if self.position_status is PositionStatus.LEGACY:
            if self.market_symbol is not None:
                raise ValueError("una posición legacy no puede tener market_symbol")
            if self.tradable or self.active_monitoring:
                raise ValueError("una posición legacy debe quedar fuera de negociación y monitorización")
            if self.exclusion_reason is None or not self.exclusion_reason.strip():
                raise ValueError("una posición legacy debe explicar su exclusión")
        elif self.exclusion_reason is not None:
            raise ValueError("una posición activa no debe tener motivo de exclusión")
        if self.cost_basis_status is CostBasisStatus.AVAILABLE:
            if (
                not isinstance(self.average_cost, Decimal)
                or not self.average_cost.is_finite()
                or self.average_cost < 0
            ):
                raise ValueError("average_cost debe ser Decimal no negativo cuando está disponible")
        elif self.average_cost is not None:
            raise ValueError("average_cost debe ser null cuando el coste no está disponible")


@dataclass(frozen=True, slots=True)
class SourceImportMetadata:
    source: ImportSource
    status: ImportStatus
    updated_at: datetime | None
    format: str | None
    rows_processed: int

    def __post_init__(self) -> None:
        if not isinstance(self.source, ImportSource):
            raise TypeError("source no es válido")
        if not isinstance(self.status, ImportStatus):
            raise TypeError("status no es válido")
        if self.rows_processed < 0:
            raise ValueError("rows_processed no puede ser negativo")
        if self.status is ImportStatus.NOT_IMPORTED:
            if self.updated_at is not None or self.format is not None or self.rows_processed:
                raise ValueError("una fuente no importada no puede contener metadatos de ejecución")
            return
        if self.updated_at is None or self.updated_at.tzinfo is None:
            raise ValueError("updated_at debe incluir zona horaria")
        if not self.format or not self.format.strip():
            raise ValueError("format no puede estar vacío")

    @classmethod
    def not_imported(cls, source: ImportSource) -> SourceImportMetadata:
        return cls(source, ImportStatus.NOT_IMPORTED, None, None, 0)


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    schema_version: int
    generated_at: datetime
    provider: str
    sources: Mapping[ImportSource, SourceImportMetadata]
    positions: tuple[ImportedPosition, ...]

    def __post_init__(self) -> None:
        if self.schema_version != SNAPSHOT_SCHEMA_VERSION:
            raise ValueError(f"schema_version no soportada: {self.schema_version!r}")
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset() is None:
            raise ValueError("generated_at debe incluir zona horaria")
        if self.provider != "revolut":
            raise ValueError("provider debe ser revolut")
        source_map = dict(self.sources)
        if set(source_map) != set(ImportSource):
            raise ValueError("sources debe declarar todas las fuentes de Revolut")
        for source, metadata in source_map.items():
            if metadata.source is not source:
                raise ValueError("los metadatos no corresponden a su fuente")
        seen: set[tuple[ImportSource, str]] = set()
        for position in self.positions:
            key = (position.source, position.source_ticker)
            if key in seen:
                raise ValueError("solo puede haber una posición por fuente y ticker")
            seen.add(key)
            if source_map[position.source].status is not ImportStatus.IMPORTED:
                raise ValueError("una posición no puede pertenecer a una fuente no importada")
        object.__setattr__(self, "sources", MappingProxyType(source_map))

    @classmethod
    def empty(cls, generated_at: datetime) -> PortfolioSnapshot:
        return cls(
            schema_version=SNAPSHOT_SCHEMA_VERSION,
            generated_at=generated_at,
            provider="revolut",
            sources={source: SourceImportMetadata.not_imported(source) for source in ImportSource},
            positions=(),
        )
