from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

import pytest

from openportfolio.application import render_portfolio_summary_text, summarize_portfolio
from openportfolio.cli import main
from openportfolio.domain import (
    CostBasisStatus,
    ImportedPosition,
    ImportSource,
    ImportStatus,
    PortfolioSnapshot,
    PositionStatus,
    SourceImportMetadata,
)
from openportfolio.persistence import save_portfolio_snapshot


GENERATED_AT = datetime(2026, 2, 3, 12, 30, tzinfo=timezone.utc)


def _snapshot() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        schema_version=1,
        generated_at=GENERATED_AT,
        provider="revolut",
        sources={
            ImportSource.INVESTMENTS: SourceImportMetadata(
                source=ImportSource.INVESTMENTS,
                status=ImportStatus.IMPORTED,
                updated_at=GENERATED_AT,
                format="synthetic_investments_v1",
                rows_processed=4,
            ),
            ImportSource.XAU_STATEMENT: SourceImportMetadata(
                source=ImportSource.XAU_STATEMENT,
                status=ImportStatus.IMPORTED,
                updated_at=GENERATED_AT,
                format="synthetic_statement_v1",
                rows_processed=2,
            ),
        },
        positions=(
            ImportedPosition(
                asset_type="equity",
                source_ticker="ACME",
                market_symbol="ACME.TEST",
                quantity=Decimal("3.25"),
                currency="USD",
                average_cost=Decimal("10.50"),
                cost_basis_status=CostBasisStatus.AVAILABLE,
                source=ImportSource.INVESTMENTS,
                name="Acme Synthetic Shares",
            ),
            ImportedPosition(
                asset_type="commodity",
                source_ticker="SYNTH-GOLD",
                market_symbol=None,
                quantity=Decimal("0.125"),
                currency="XAU",
                average_cost=None,
                cost_basis_status=CostBasisStatus.UNAVAILABLE,
                source=ImportSource.XAU_STATEMENT,
                name=None,
            ),
            ImportedPosition(
                asset_type="equity",
                source_ticker="ARCHIVE",
                market_symbol=None,
                quantity=Decimal("1"),
                currency="USD",
                average_cost=Decimal("7"),
                cost_basis_status=CostBasisStatus.AVAILABLE,
                source=ImportSource.INVESTMENTS,
                name="Archived Synthetic Shares",
                position_status=PositionStatus.LEGACY,
                tradable=False,
                active_monitoring=False,
                exclusion_reason="synthetic fixture exclusion",
            ),
        ),
    )


def _saved_snapshot(tmp_path: Path) -> Path:
    path = tmp_path / "synthetic-portfolio.yaml"
    save_portfolio_snapshot(_snapshot(), path)
    return path


def test_summary_loads_fictitious_snapshot_through_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = main(["portfolio-summary", "--snapshot", str(_saved_snapshot(tmp_path))])

    captured = capsys.readouterr()
    assert result == 0
    assert "Posiciones totales: 3" in captured.out
    assert captured.err == ""


def test_text_output_is_deterministic_and_represents_unavailable_fields() -> None:
    summary = summarize_portfolio(_snapshot())

    first = render_portfolio_summary_text(summary)
    second = render_portfolio_summary_text(summary)

    assert first == second
    assert "Operaciones procesadas: 6" in first
    assert "Monedas: USD (2), XAU (1)" in first
    assert "Posiciones abiertas: 3" in first
    assert "Posiciones cerradas: no disponible" in first
    assert "Advertencias de importación: no disponible" in first
    assert "Coste medio: no disponible" in first
    assert "Coste acumulado: no disponible" in first
    assert "Estado operativo: legacy" in first


def test_json_output_is_valid_stable_and_groups_by_currency(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _saved_snapshot(tmp_path)

    assert main(
        ["portfolio-summary", "--snapshot", str(path), "--format", "json"]
    ) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["summary_schema_version"] == 1
    assert payload["snapshot"]["generated_at"] == "2026-02-03T12:30:00+00:00"
    assert payload["metrics"]["by_currency"] == [
        {"currency": "USD", "positions": 2},
        {"currency": "XAU", "positions": 1},
    ]
    assert payload["metrics"]["by_asset_type"] == [
        {"asset_type": "commodity", "positions": 1},
        {"asset_type": "equity", "positions": 2},
    ]
    assert payload["metrics"]["positions"] == {
        "total": 3,
        "open": 3,
        "closed": None,
    }
    assert payload["import_warnings"] == {"available": False, "items": None}
    assert payload["unavailable_fields"] == [
        "import_warnings",
        "closed_positions",
        "positions[].accumulated_cost",
        "market_valuation",
    ]
    missing_cost = next(
        item for item in payload["positions"] if item["operational_identifier"] == "SYNTH-GOLD"
    )
    assert missing_cost["cost"] == {
        "average": None,
        "accumulated": None,
        "basis_status": "unavailable",
    }
    assert {"name", "market_symbol", "average_cost", "accumulated_cost"} == set(
        missing_cost["unavailable_fields"]
    )


def test_missing_snapshot_returns_two_with_sanitized_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secret_name = "private-account-998877.yaml"

    result = main(
        ["portfolio-summary", "--snapshot", str(tmp_path / secret_name)]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "no existe o no es legible" in captured.err
    assert secret_name not in captured.out + captured.err


def test_invalid_snapshot_returns_one_without_leaking_content(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "CONFIDENTIAL-HOLDING-12345"
    path = tmp_path / "invalid.yaml"
    path.write_text(f"schema_version: {secret}\npositions: [{secret}]\n", encoding="utf-8")

    result = main(["portfolio-summary", "--snapshot", str(path)])

    captured = capsys.readouterr()
    assert result == 1
    assert "formato compatible" in captured.err
    assert secret not in captured.out + captured.err
    assert path.name not in captured.out + captured.err


def test_summary_never_constructs_market_or_network_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _saved_snapshot(tmp_path)
    monkeypatch.setattr(
        "openportfolio.cli._provider",
        lambda *args, **kwargs: pytest.fail("no debe construir un proveedor de mercado"),
    )
    monkeypatch.setattr(
        "openportfolio.cli.NtfyNotifier",
        lambda *args, **kwargs: pytest.fail("no debe construir un cliente de red"),
    )

    assert main(["portfolio-summary", "--snapshot", str(path)]) == 0


def test_readme_examples_are_generic_and_do_not_embed_snapshot_output() -> None:
    readme = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")
    section = readme.split("### Resumen del snapshot importado", maxsplit=1)[1]
    section = section.split("## Instalación", maxsplit=1)[0]

    assert "portfolio-summary" in section
    assert "--format json" in section
    assert "proporciona todavía valoración de mercado" in section
    assert "Posiciones totales:" not in section
    assert "Coste medio:" not in section
