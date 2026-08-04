from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Mapping, Sequence

from openportfolio.domain import (
    CostBasisStatus,
    ImportedPosition,
    ImportSource,
    ImportStatus,
    PortfolioSnapshot,
    SourceImportMetadata,
)
from openportfolio.importers import (
    ImportIssue,
    RevolutCsvReadError,
    RevolutSourceResult,
    UnknownRevolutCsvFormatError,
    detect_revolut_format,
    import_investments,
    import_xau_statement,
)


class RevolutImportError(ValueError):
    """The supplied files cannot be used as the expected Revolut exports."""


class RevolutDiscoveryError(ValueError):
    """A directory cannot yield one unambiguous pair of Revolut exports."""


@dataclass(frozen=True, slots=True)
class RevolutExportSelection:
    investment_history: Path
    account_statement: Path
    csv_examined: int
    csv_ignored: int


def discover_revolut_exports(directory: str | Path) -> RevolutExportSelection:
    """Select the newest supported export of each kind from one directory."""
    source_directory = Path(directory)
    try:
        if not source_directory.is_dir():
            raise RevolutDiscoveryError(
                "el directorio de entrada no existe o no es legible"
            )
        entries = tuple(source_directory.iterdir())
    except OSError as error:
        raise RevolutDiscoveryError(
            "el directorio de entrada no existe o no es legible"
        ) from error

    csv_paths: list[Path] = []
    try:
        for entry in entries:
            if entry.suffix.lower() == ".csv" and entry.is_file():
                csv_paths.append(entry)
    except OSError as error:
        raise RevolutDiscoveryError(
            "no se pueden examinar de forma segura los CSV del directorio"
        ) from error

    if not csv_paths:
        raise RevolutDiscoveryError("el directorio no contiene archivos CSV")

    candidates: dict[ImportSource, list[tuple[int, Path]]] = {
        ImportSource.INVESTMENTS: [],
        ImportSource.XAU_STATEMENT: [],
    }
    ignored = 0
    for path in csv_paths:
        try:
            detected = detect_revolut_format(path)
            modified_at = path.stat().st_mtime_ns
        except RevolutCsvReadError as error:
            raise RevolutDiscoveryError(
                "uno de los archivos CSV no existe o no es legible"
            ) from error
        except UnknownRevolutCsvFormatError:
            ignored += 1
            continue
        except OSError as error:
            raise RevolutDiscoveryError(
                "uno de los archivos CSV no existe o no es legible"
            ) from error
        candidates[detected].append((modified_at, path))

    selected: dict[ImportSource, Path] = {}
    for source in ImportSource:
        matches = candidates[source]
        if not matches:
            raise RevolutDiscoveryError(
                f"falta un CSV compatible con {source.value}"
            )
        newest_mtime = max(modified_at for modified_at, _ in matches)
        newest = [path for modified_at, path in matches if modified_at == newest_mtime]
        if len(newest) != 1:
            raise RevolutDiscoveryError(
                "hay varios candidatos con el mismo mtime; proporciona rutas "
                "explícitas mediante import-revolut"
            )
        selected[source] = newest[0]

    return RevolutExportSelection(
        investment_history=selected[ImportSource.INVESTMENTS],
        account_statement=selected[ImportSource.XAU_STATEMENT],
        csv_examined=len(csv_paths),
        csv_ignored=ignored,
    )


@dataclass(frozen=True, slots=True)
class PortfolioImportOutcome:
    snapshot: PortfolioSnapshot | None
    results: tuple[RevolutSourceResult, ...]
    updated_sources: tuple[ImportSource, ...]
    conserved_sources: tuple[ImportSource, ...]
    never_imported_sources: tuple[ImportSource, ...]
    errors: tuple[ImportIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.errors and self.snapshot is not None

    @property
    def warnings(self) -> tuple[ImportIssue, ...]:
        return tuple(issue for result in self.results for issue in result.warnings)


def import_revolut_exports(
    investment_history: str | Path,
    account_statement: str | Path,
    *,
    generated_at: datetime | None = None,
) -> PortfolioImportOutcome:
    """Import a complete pair of Revolut exports using their detected headers."""
    inputs = (
        (ImportSource.INVESTMENTS, Path(investment_history), import_investments),
        (ImportSource.XAU_STATEMENT, Path(account_statement), import_xau_statement),
    )
    results: list[RevolutSourceResult] = []
    for expected_source, path, importer in inputs:
        try:
            detected_source = detect_revolut_format(path)
        except ValueError as error:
            raise RevolutImportError(
                f"el archivo de {expected_source.value} no tiene un formato Revolut válido"
            ) from error
        if detected_source is not expected_source:
            raise RevolutImportError(
                f"el archivo de {expected_source.value} corresponde a otra exportación"
            )
        results.append(importer(path))
    return combine_revolut_imports(results, None, generated_at=generated_at)


def combine_revolut_imports(
    results: Sequence[RevolutSourceResult],
    existing: PortfolioSnapshot | None,
    *,
    generated_at: datetime | None = None,
) -> PortfolioImportOutcome:
    now = (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    imported_sources = tuple(result.source for result in results)
    errors = tuple(issue for result in results for issue in result.errors)
    baseline = existing or PortfolioSnapshot.empty(now)
    conserved = tuple(
        source
        for source in ImportSource
        if source not in imported_sources
        and baseline.sources[source].status is ImportStatus.IMPORTED
    )
    never = tuple(
        source
        for source in ImportSource
        if source not in imported_sources
        and baseline.sources[source].status is ImportStatus.NOT_IMPORTED
    )
    if errors:
        return PortfolioImportOutcome(None, tuple(results), (), conserved, never, errors)

    sources = dict(baseline.sources)
    positions = [position for position in baseline.positions if position.source not in imported_sources]
    for result in results:
        positions.extend(result.positions)
        sources[result.source] = SourceImportMetadata(
            source=result.source,
            status=ImportStatus.IMPORTED,
            updated_at=now,
            format=result.format,
            rows_processed=result.rows_read,
        )
    snapshot = PortfolioSnapshot(
        schema_version=baseline.schema_version,
        generated_at=now,
        provider="revolut",
        sources=sources,
        positions=tuple(sorted(positions, key=lambda item: (item.source.value, item.source_ticker))),
    )
    return PortfolioImportOutcome(
        snapshot,
        tuple(results),
        imported_sources,
        conserved,
        never,
        (),
    )


def reconciliation_report(
    outcome: PortfolioImportOutcome,
    *,
    generated_at: datetime | None = None,
) -> str:
    now = (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    lines = [
        "OpenPortfolio — conciliación de importación Revolut",
        "Informe agregado: no contiene operaciones históricas, efectivo ni saldos de cuenta.",
        "",
        "Fuentes recibidas:",
    ]
    if outcome.results:
        for result in outcome.results:
            lines.append(f"- {result.source.value}: {result.rows_read} filas; formato {result.format}")
    else:
        lines.append("- ninguna")
    lines.extend(("", "Estado de fuentes:"))
    lines.append(f"- actualizadas: {_sources(outcome.updated_sources)}")
    lines.append(f"- conservadas: {_sources(outcome.conserved_sources)}")
    lines.append(f"- nunca importadas: {_sources(outcome.never_imported_sources)}")

    snapshot = outcome.snapshot
    if snapshot is not None:
        lines.append("- actualización y antigüedad por fuente:")
        for source in ImportSource:
            metadata = snapshot.sources[source]
            if metadata.updated_at is None:
                lines.append(f"  - {source.value}: no importada")
            else:
                age = max(0, int((now - metadata.updated_at.astimezone(timezone.utc)).total_seconds()))
                lines.append(
                    f"  - {source.value}: {metadata.updated_at.astimezone(timezone.utc).isoformat()} "
                    f"(antigüedad: {_age(age)}; {metadata.rows_processed} filas)"
                )

    lines.extend(("", "Resumen de movimientos:"))
    totals: dict[str, int] = {}
    for result in outcome.results:
        for key, value in result.counts.items():
            totals[key] = totals.get(key, 0) + value
    labels = (
        ("buys", "compras"),
        ("sells", "ventas"),
        ("dividends", "dividendos ignorados"),
        ("cash", "movimientos de efectivo ignorados"),
        ("rewards", "recompensas ignoradas"),
        ("xau_buys", "conversiones a XAU"),
        ("xau_sells", "conversiones desde XAU"),
        ("ignored", "filas del extracto ignoradas"),
    )
    for key, label in labels:
        if key in totals:
            lines.append(f"- {label}: {totals[key]}")

    closed = tuple(ticker for result in outcome.results for ticker in result.closed_positions)
    positions = snapshot.positions if snapshot is not None else ()
    active_equities = tuple(
        position
        for position in positions
        if position.source is ImportSource.INVESTMENTS and position.active_monitoring
    )
    xau_positions = tuple(
        position for position in positions if position.source is ImportSource.XAU_STATEMENT
    )
    historical = tuple(position for position in positions if not position.active_monitoring)
    lines.extend(
        (
            "",
            f"Posiciones abiertas reconstruidas: {len(positions)}",
            f"Posiciones activas de acciones y ETF: {len(active_equities)}",
            f"Posiciones XAU separadas: {len(xau_positions)}",
            f"Posiciones históricas no operativas: {len(historical)}",
            f"Posiciones cerradas en fuentes actualizadas: {len(closed)}",
        )
    )
    for position in active_equities + xau_positions:
        cost = (
            _decimal(position.average_cost)
            if position.cost_basis_status is CostBasisStatus.AVAILABLE
            else "no disponible"
        )
        market = position.market_symbol or "sin mapping"
        lines.append(
            f"- {position.source_ticker} ({position.name or 'nombre no confirmado'}) | "
            f"símbolo de mercado: {market} | "
            f"cantidad: {_decimal(position.quantity)} | moneda: {position.currency} | "
            f"coste medio: {cost}"
        )
    if historical:
        lines.append("- históricas no operativas:")
        for position in historical:
            lines.append(
                f"  - {position.source_ticker} ({position.name or 'nombre no confirmado'}) | "
                f"cantidad histórica: {_decimal(position.quantity)} | moneda: {position.currency} | "
                f"estado: {position.position_status.value} | negociable: no | "
                f"monitorización activa: no | razón: {position.exclusion_reason}"
            )
    if closed:
        lines.append(f"- cerradas: {', '.join(sorted(closed))}")

    resolved, unresolved = _ticker_resolution(positions)
    lines.extend(
        (
            "",
            f"Tickers resueltos: {', '.join(resolved) if resolved else 'ninguno'}",
            f"Tickers sin resolver: {', '.join(unresolved) if unresolved else 'ninguno'}",
            "",
            f"Advertencias: {len(outcome.warnings)}",
        )
    )
    lines.extend(f"- {issue.render()}" for issue in outcome.warnings)
    lines.append(f"Errores: {len(outcome.errors)}")
    lines.extend(f"- {issue.render()}" for issue in outcome.errors)
    lines.extend(
        (
            "",
            "Política aplicada:",
            "- Compras: Total Amount absoluto es el coste efectivo; el coste unitario es "
            "Total Amount / Quantity.",
            "- Ventas: reducen cantidad y coste contable al coste medio anterior; el precio "
            "de venta no recalcula el coste restante.",
            "- Dividendos, efectivo y recompensas no afectan cantidad ni coste medio.",
            "- XAU: las unidades se calculan como Importe - Comisión y se validan contra "
            "Saldo; el coste no se inventa ni se toma del saldo.",
            "- No se mezclan monedas ni se convierten posiciones a USD.",
            "",
            "Confirmación: examples/operational_review.yaml no fue modificado por la importación.",
            "La importación no activa alertas, no usa cotizaciones y no accede al estado de alertas.",
        )
    )
    return "\n".join(lines) + "\n"


def _ticker_resolution(
    positions: Sequence[ImportedPosition],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    equities = [
        position
        for position in positions
        if position.source is ImportSource.INVESTMENTS and position.active_monitoring
    ]
    resolved = tuple(sorted(position.source_ticker for position in equities if position.market_symbol))
    unresolved = tuple(sorted(position.source_ticker for position in equities if not position.market_symbol))
    return resolved, unresolved


def _sources(sources: Sequence[ImportSource]) -> str:
    return ", ".join(source.value for source in sources) if sources else "ninguna"


def _age(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    if seconds < 86400:
        return f"{seconds // 3600} h"
    return f"{seconds // 86400} días"


def _decimal(value: Decimal | None) -> str:
    assert value is not None
    return format(value, "f")
