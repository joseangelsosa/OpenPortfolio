from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any

from openportfolio.application.portfolio_valuation import PortfolioValuation


VALUATION_REPORT_CONTRACT_VERSION = 1
_REQUIRED_VALUATION_FIELDS = frozenset(
    {
        "metadata",
        "coverage",
        "currency_totals",
        "positions",
        "exclusions",
        "warnings",
        "errors",
        "unavailable_fields",
    }
)


class ValuationReportError(RuntimeError):
    """A valuation report could not be constructed or persisted safely."""


class PartialValuationReportError(ValuationReportError):
    """A partial valuation requires an explicit output policy."""


def build_valuation_report(valuation: PortfolioValuation) -> dict[str, Any]:
    """Build the versioned JSON report without performing any I/O."""
    payload = valuation.as_dict()
    if not _REQUIRED_VALUATION_FIELDS.issubset(payload):
        raise ValuationReportError("el resultado no cumple el contrato de valoración")
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise ValuationReportError("el resultado no contiene metadata válida")

    payload["report_contract_version"] = VALUATION_REPORT_CONTRACT_VERSION
    metadata.update(
        {
            "generated_at": valuation.executed_at.isoformat(),
            "valuation_status": "complete" if valuation.ok else "partial",
            "amounts_separated_by_currency": True,
            "currency_conversion": False,
        }
    )
    return payload


def write_valuation_report(
    valuation: PortfolioValuation,
    path: str | Path,
    *,
    allow_partial: bool = False,
) -> None:
    """Atomically persist a complete report, or an explicitly requested partial one."""
    destination = Path(path)
    if valuation.missing_mapping_positions or valuation.mapping_currency_mismatches:
        raise ValuationReportError(
            "el mapping no está listo para generar un informe persistente"
        )
    if not valuation.ok:
        if not allow_partial:
            raise PartialValuationReportError(
                "la valoración es parcial; el informe anterior se conserva"
            )
        if destination.exists():
            raise PartialValuationReportError(
                "un resultado parcial solo puede escribirse en un destino nuevo"
            )

    try:
        payload = build_valuation_report(valuation)
        serialized = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        json.loads(serialized)
    except ValuationReportError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValuationReportError("no se pudo construir un JSON válido") from error

    temporary_path: Path | None = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(serialized)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination)
    except OSError as error:
        raise ValuationReportError(
            f"no se pudo escribir atómicamente el informe {destination.name}"
        ) from error
    finally:
        if temporary_path is not None and temporary_path.exists():
            try:
                temporary_path.unlink()
            except OSError:
                pass
