from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

import pytest

from openportfolio.application import validate_market_mapping
from openportfolio.cli import main
from openportfolio.domain import (
    CostBasisStatus,
    ImportedPosition,
    ImportSource,
    ImportStatus,
    MarketMapping,
    MarketMappingEntry,
    PortfolioSnapshot,
    PositionStatus,
    SourceImportMetadata,
)
from openportfolio.persistence import (
    MarketMappingError,
    load_market_mapping,
    save_portfolio_snapshot,
)


NOW = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)


def _snapshot() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        schema_version=1,
        generated_at=NOW,
        provider="revolut",
        sources={
            ImportSource.INVESTMENTS: SourceImportMetadata(
                source=ImportSource.INVESTMENTS,
                status=ImportStatus.IMPORTED,
                updated_at=NOW,
                format="synthetic_v1",
                rows_processed=2,
            ),
            ImportSource.XAU_STATEMENT: SourceImportMetadata.not_imported(
                ImportSource.XAU_STATEMENT
            ),
        },
        positions=(
            ImportedPosition(
                asset_type="equity",
                source_ticker="ACME",
                market_symbol="IGNORED.TEST",
                quantity=Decimal("2"),
                currency="USD",
                average_cost=Decimal("10"),
                cost_basis_status=CostBasisStatus.AVAILABLE,
                source=ImportSource.INVESTMENTS,
                name="Acme Synthetic Shares",
            ),
            ImportedPosition(
                asset_type="equity",
                source_ticker="ARCHIVE",
                market_symbol=None,
                quantity=Decimal("1"),
                currency="EUR",
                average_cost=Decimal("5"),
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


def _mapping(*, currency: str = "USD", extra: bool = False) -> MarketMapping:
    entries = {
        "ACME": MarketMappingEntry(
            enabled=True,
            market_symbol="ACME.TEST",
            provider="yfinance",
            expected_currency=currency,
        ),
        "ARCHIVE": MarketMappingEntry(enabled=False),
    }
    if extra:
        entries["SPARE"] = MarketMappingEntry(enabled=False)
    return MarketMapping(version=1, instruments=entries)


def _write_mapping(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def _valid_yaml() -> str:
    return """\
version: 1
instruments:
  ACME:
    market_symbol: ACME.TEST
    provider: yfinance
    expected_currency: USD
    enabled: true
  ARCHIVE:
    enabled: false
"""


def _saved_inputs(tmp_path: Path, mapping_body: str | None = None) -> tuple[Path, Path]:
    snapshot_path = tmp_path / "synthetic-snapshot.yaml"
    mapping_path = tmp_path / "synthetic-mapping.yaml"
    save_portfolio_snapshot(_snapshot(), snapshot_path)
    _write_mapping(mapping_path, mapping_body or _valid_yaml())
    return snapshot_path, mapping_path


def test_loads_valid_yaml_with_enabled_and_explicitly_excluded_entries(
    tmp_path: Path,
) -> None:
    mapping = load_market_mapping(_write_mapping(tmp_path / "mapping.yaml", _valid_yaml()))

    assert mapping.version == 1
    assert mapping.instruments["ACME"].market_symbol == "ACME.TEST"
    assert mapping.instruments["ACME"].expected_currency == "USD"
    assert mapping.instruments["ARCHIVE"] == MarketMappingEntry(enabled=False)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (_valid_yaml().replace("version: 1", "version: 2"), "version"),
        (_valid_yaml().replace("ACME.TEST", '" "'), "market_symbol"),
        (_valid_yaml().replace("expected_currency: USD", "expected_currency: US"), "tres letras"),
        (_valid_yaml().replace("provider: yfinance", "provider: unknown"), "no está admitido"),
        (
            _valid_yaml().replace(
                "  ARCHIVE:\n    enabled: false",
                "  ARCHIVE:\n    enabled: false\n    market_symbol: ARCHIVE.TEST",
            ),
            "excluida",
        ),
        (
            _valid_yaml().replace("    enabled: true", "    enabled: true\n    quantity: 99", 1),
            "campos desconocidos",
        ),
    ],
)
def test_rejects_invalid_mapping_contract(
    tmp_path: Path, body: str, message: str
) -> None:
    path = _write_mapping(tmp_path / "mapping.yaml", body)

    with pytest.raises(MarketMappingError, match=message):
        load_market_mapping(path)


def test_rejects_duplicate_instrument_identifiers(tmp_path: Path) -> None:
    body = _valid_yaml() + "  ACME:\n    enabled: false\n"

    with pytest.raises(MarketMappingError, match="duplicada"):
        load_market_mapping(_write_mapping(tmp_path / "mapping.yaml", body))


def test_rejects_identifiers_duplicated_after_normalization(tmp_path: Path) -> None:
    body = _valid_yaml() + '  " ACME ":\n    enabled: false\n'

    with pytest.raises(MarketMappingError, match="únicos"):
        load_market_mapping(_write_mapping(tmp_path / "mapping.yaml", body))


def test_fully_covered_snapshot_is_ready() -> None:
    result = validate_market_mapping(_snapshot(), _mapping())

    assert result.ready_for_market_valuation
    assert result.as_dict()["metrics"] == {
        "positions_total": 2,
        "positions_with_enabled_mapping": 1,
        "positions_explicitly_excluded": 1,
        "positions_without_mapping": 0,
        "unused_mapping_entries": 0,
        "currency_mismatches": 0,
    }
    assert result.providers == ("yfinance",)


def test_missing_position_is_reported_and_not_ready() -> None:
    mapping = MarketMapping(version=1, instruments={"ARCHIVE": MarketMappingEntry(False)})

    result = validate_market_mapping(_snapshot(), mapping)

    assert not result.ready_for_market_valuation
    assert result.missing_positions == 1
    assert result.missing_identifiers == ("ACME",)


def test_unused_entry_is_reported_but_does_not_block_readiness() -> None:
    result = validate_market_mapping(_snapshot(), _mapping(extra=True))

    assert result.ready_for_market_valuation
    assert result.unused_identifiers == ("SPARE",)


def test_currency_mismatch_is_reported_and_not_ready() -> None:
    result = validate_market_mapping(_snapshot(), _mapping(currency="EUR"))

    assert not result.ready_for_market_valuation
    assert [item.as_dict() for item in result.currency_mismatches] == [
        {
            "instrument_id": "ACME",
            "snapshot_currency": "USD",
            "expected_currency": "EUR",
        }
    ]


def test_cli_text_and_json_outputs_are_deterministic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot_path, mapping_path = _saved_inputs(tmp_path)
    arguments = [
        "validate-market-mapping",
        "--snapshot",
        str(snapshot_path),
        "--mapping",
        str(mapping_path),
    ]

    assert main(arguments) == 0
    text_output = capsys.readouterr().out
    assert "Posiciones totales: 2" in text_output
    assert "Correspondencias habilitadas: 1" in text_output
    assert "Exclusiones explícitas: 1" in text_output
    assert "Configuración lista para futura valoración: sí" in text_output

    assert main([*arguments, "--format", "json"]) == 0
    first = capsys.readouterr().out
    assert main([*arguments, "--format", "json"]) == 0
    second = capsys.readouterr().out
    assert first == second
    payload = json.loads(first)
    assert payload["validation_schema_version"] == 1
    assert payload["ready_for_market_valuation"] is True
    assert payload["providers"] == ["yfinance"]


@pytest.mark.parametrize(
    "mapping_body",
    [
        "version: 1\ninstruments:\n  ARCHIVE:\n    enabled: false\n",
        _valid_yaml().replace("expected_currency: USD", "expected_currency: EUR"),
    ],
)
def test_cli_returns_nonzero_for_validation_findings(
    tmp_path: Path, mapping_body: str
) -> None:
    snapshot_path, mapping_path = _saved_inputs(tmp_path, mapping_body)

    assert main(
        [
            "validate-market-mapping",
            "--snapshot",
            str(snapshot_path),
            "--mapping",
            str(mapping_path),
            "--format",
            "json",
        ]
    ) == 1


def test_invalid_mapping_error_is_json_and_does_not_leak_content(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "CONFIDENTIAL-INSTRUMENT-998877"
    snapshot_path, mapping_path = _saved_inputs(
        tmp_path,
        f"version: 1\ninstruments: [{{secret: {secret}}}\n",
    )

    result = main(
        [
            "validate-market-mapping",
            "--snapshot",
            str(snapshot_path),
            "--mapping",
            str(mapping_path),
            "--format",
            "json",
        ]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == ""
    assert json.loads(captured.err)["error"]["code"] == "invalid_mapping"
    assert secret not in captured.err
    assert mapping_path.name not in captured.err


def test_invalid_snapshot_error_does_not_leak_content(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "CONFIDENTIAL-SNAPSHOT-HOLDING-112233"
    snapshot_path = _write_mapping(
        tmp_path / "invalid-snapshot.yaml",
        f"schema_version: {secret}\npositions: [{secret}]\n",
    )
    mapping_path = _write_mapping(tmp_path / "mapping.yaml", _valid_yaml())

    result = main(
        [
            "validate-market-mapping",
            "--snapshot",
            str(snapshot_path),
            "--mapping",
            str(mapping_path),
        ]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert "formato compatible" in captured.err
    assert secret not in captured.out + captured.err
    assert snapshot_path.name not in captured.out + captured.err


def test_missing_mapping_file_returns_two_with_sanitized_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot_path, _ = _saved_inputs(tmp_path)
    private_name = "private-mapping-445566.yaml"

    result = main(
        [
            "validate-market-mapping",
            "--snapshot",
            str(snapshot_path),
            "--mapping",
            str(tmp_path / private_name),
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "no existe o no es legible" in captured.err
    assert private_name not in captured.out + captured.err


def test_cli_never_constructs_network_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_path, mapping_path = _saved_inputs(tmp_path)
    monkeypatch.setattr(
        "openportfolio.cli._provider",
        lambda *args, **kwargs: pytest.fail("no debe construir un proveedor de mercado"),
    )
    monkeypatch.setattr(
        "openportfolio.cli.NtfyNotifier",
        lambda *args, **kwargs: pytest.fail("no debe construir un cliente de red"),
    )

    assert main(
        [
            "validate-market-mapping",
            "--snapshot",
            str(snapshot_path),
            "--mapping",
            str(mapping_path),
        ]
    ) == 0
