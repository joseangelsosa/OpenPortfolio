from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import os
from pathlib import Path
import tempfile
from typing import Any

import yaml

from openportfolio.domain import (
    CostBasisStatus,
    ImportedPosition,
    ImportSource,
    ImportStatus,
    PortfolioSnapshot,
    PositionStatus,
    SNAPSHOT_SCHEMA_VERSION,
    SourceImportMetadata,
)


class PortfolioSnapshotError(RuntimeError):
    """A private imported snapshot could not be validated or persisted safely."""


def load_portfolio_snapshot(path: str | Path) -> PortfolioSnapshot:
    source = Path(path)
    try:
        with source.open(encoding="utf-8") as stream:
            raw = yaml.safe_load(stream)
        return _decode(raw)
    except PortfolioSnapshotError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError, TypeError, ValueError) as error:
        raise PortfolioSnapshotError(f"no se puede leer el snapshot existente: {error}") from error


def save_portfolio_snapshot(snapshot: PortfolioSnapshot, path: str | Path) -> None:
    destination = Path(path)
    payload = yaml.safe_dump(
        _encode(snapshot),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    _atomic_write_text(destination, payload)


def atomic_write_text(path: str | Path, content: str) -> None:
    _atomic_write_text(Path(path), content)


def _atomic_write_text(path: Path, content: str) -> None:
    temporary_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            if not content.endswith("\n"):
                temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    except OSError as error:
        raise PortfolioSnapshotError(f"no se puede escribir atómicamente {path.name}: {error}") from error
    finally:
        if temporary_path is not None and temporary_path.exists():
            try:
                temporary_path.unlink()
            except OSError:
                pass


def _encode(snapshot: PortfolioSnapshot) -> dict[str, object]:
    return {
        "schema_version": snapshot.schema_version,
        "generated_at": snapshot.generated_at.astimezone(timezone.utc).isoformat(),
        "source": {"provider": snapshot.provider},
        "sources": {
            source.value: {
                "status": metadata.status.value,
                "updated_at": (
                    metadata.updated_at.astimezone(timezone.utc).isoformat()
                    if metadata.updated_at is not None
                    else None
                ),
                "format": metadata.format,
                "rows_processed": metadata.rows_processed,
            }
            for source, metadata in snapshot.sources.items()
        },
        "positions": [
            {
                "asset_type": position.asset_type,
                "source_ticker": position.source_ticker,
                "name": position.name,
                "market_symbol": position.market_symbol,
                "quantity": _decimal(position.quantity),
                "currency": position.currency,
                "average_cost": (
                    _decimal(position.average_cost) if position.average_cost is not None else None
                ),
                "cost_basis_status": position.cost_basis_status.value,
                "source": position.source.value,
                "position_status": position.position_status.value,
                "tradable": position.tradable,
                "active_monitoring": position.active_monitoring,
                "exclusion_reason": position.exclusion_reason,
            }
            for position in snapshot.positions
        ],
    }


def _decode(raw: object) -> PortfolioSnapshot:
    root = _mapping(raw, "raíz")
    version = root.get("schema_version")
    if version != SNAPSHOT_SCHEMA_VERSION:
        raise PortfolioSnapshotError(f"schema_version no soportada: {version!r}")
    generated_at = _timestamp(root.get("generated_at"), "generated_at")
    source = _mapping(root.get("source"), "source")
    if source.get("provider") != "revolut":
        raise PortfolioSnapshotError("source.provider debe ser revolut")
    raw_sources = _mapping(root.get("sources"), "sources")
    sources: dict[ImportSource, SourceImportMetadata] = {}
    for source_id in ImportSource:
        data = _mapping(raw_sources.get(source_id.value), f"sources.{source_id.value}")
        try:
            status = ImportStatus(data.get("status"))
        except (TypeError, ValueError):
            raise PortfolioSnapshotError(
                f"sources.{source_id.value}.status no es válido"
            ) from None
        rows_processed = data.get("rows_processed")
        if isinstance(rows_processed, bool) or not isinstance(rows_processed, int):
            raise PortfolioSnapshotError(
                f"sources.{source_id.value}.rows_processed debe ser entero"
            )
        updated_at = data.get("updated_at")
        source_format = data.get("format")
        sources[source_id] = SourceImportMetadata(
            source=source_id,
            status=status,
            updated_at=(
                None
                if updated_at is None
                else _timestamp(updated_at, f"sources.{source_id.value}.updated_at")
            ),
            format=None if source_format is None else _text(source_format, "format"),
            rows_processed=rows_processed,
        )
    raw_positions = root.get("positions")
    if not isinstance(raw_positions, list):
        raise PortfolioSnapshotError("positions debe ser una lista")
    positions: list[ImportedPosition] = []
    for index, value in enumerate(raw_positions):
        data = _mapping(value, f"positions[{index}]")
        try:
            positions.append(
                ImportedPosition(
                    asset_type=_text(data.get("asset_type"), "asset_type"),
                    source_ticker=_text(data.get("source_ticker"), "source_ticker"),
                    market_symbol=(
                        None
                        if data.get("market_symbol") is None
                        else _text(data.get("market_symbol"), "market_symbol")
                    ),
                    quantity=_required_decimal(data.get("quantity"), "quantity"),
                    currency=_text(data.get("currency"), "currency"),
                    average_cost=(
                        None
                        if data.get("average_cost") is None
                        else _required_decimal(data.get("average_cost"), "average_cost")
                    ),
                    cost_basis_status=CostBasisStatus(data.get("cost_basis_status")),
                    source=ImportSource(data.get("source")),
                    name=(
                        None if data.get("name") is None else _text(data.get("name"), "name")
                    ),
                    position_status=PositionStatus(data.get("position_status", "active")),
                    tradable=_boolean(data.get("tradable", True), "tradable"),
                    active_monitoring=_boolean(
                        data.get("active_monitoring", True), "active_monitoring"
                    ),
                    exclusion_reason=(
                        None
                        if data.get("exclusion_reason") is None
                        else _text(data.get("exclusion_reason"), "exclusion_reason")
                    ),
                )
            )
        except (TypeError, ValueError) as error:
            raise PortfolioSnapshotError(f"positions[{index}] no es válida: {error}") from error
    try:
        return PortfolioSnapshot(version, generated_at, "revolut", sources, tuple(positions))
    except (TypeError, ValueError) as error:
        raise PortfolioSnapshotError(f"snapshot inválido: {error}") from error


def _mapping(value: object, field_name: str) -> dict[Any, Any]:
    if not isinstance(value, dict):
        raise PortfolioSnapshotError(f"{field_name} debe ser un mapa")
    return value


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PortfolioSnapshotError(f"{field_name} debe ser texto no vacío")
    return value.strip()


def _timestamp(value: object, field_name: str) -> datetime:
    text = _text(value, field_name)
    try:
        timestamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise PortfolioSnapshotError(f"{field_name} no es una fecha ISO-8601") from error
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise PortfolioSnapshotError(f"{field_name} debe incluir zona horaria")
    return timestamp


def _required_decimal(value: object, field_name: str) -> Decimal:
    if not isinstance(value, str):
        raise PortfolioSnapshotError(f"{field_name} debe ser texto para preservar precisión")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise PortfolioSnapshotError(f"{field_name} no es un decimal válido") from error
    if not result.is_finite():
        raise PortfolioSnapshotError(f"{field_name} debe ser finito")
    return result


def _boolean(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise PortfolioSnapshotError(f"{field_name} debe ser booleano")
    return value


def _decimal(value: Decimal) -> str:
    return format(value, "f")
