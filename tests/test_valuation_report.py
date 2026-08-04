from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path

import pytest

from openportfolio.application import value_portfolio
from openportfolio.cli import main
from openportfolio.domain import (
    CostBasisStatus,
    ImportedPosition,
    ImportSource,
    ImportStatus,
    MarketMapping,
    MarketMappingEntry,
    MarketQuote,
    PortfolioSnapshot,
    QuoteSource,
    SourceImportMetadata,
)
from openportfolio.market_data import MarketDataNotFoundError
from openportfolio.persistence import (
    PartialValuationReportError,
    ValuationReportError,
    build_valuation_report,
    save_portfolio_snapshot,
    write_valuation_report,
)


NOW = datetime(2026, 8, 4, 10, 30, tzinfo=timezone.utc)


class StubProvider:
    name = "yfinance"

    def __init__(
        self,
        prices: dict[str, Decimal],
        *,
        failures: set[str] | None = None,
    ) -> None:
        self.prices = prices
        self.failures = failures or set()
        self.calls: list[str] = []

    def get_quote(self, instrument: object) -> MarketQuote:
        identifier = instrument.id  # type: ignore[attr-defined]
        self.calls.append(identifier)
        if identifier in self.failures:
            raise MarketDataNotFoundError("PRIVATE PROVIDER PAYLOAD 998877")
        return MarketQuote(
            id=f"quote-{identifier}",
            instrument_id=identifier,
            price=self.prices[identifier],
            currency=instrument.currency,  # type: ignore[attr-defined]
            observed_at=NOW,
            retrieved_at=NOW,
            provider=self.name,
            provider_symbol=instrument.symbol_for(self.name),  # type: ignore[attr-defined]
            source=QuoteSource.DAILY_CLOSE,
            kind="close",
        )


def _position(
    identifier: str,
    currency: str,
    *,
    quantity: str = "1.000000000000000001",
    average_cost: str | None = "2.000000000000000003",
) -> ImportedPosition:
    return ImportedPosition(
        asset_type="equity",
        source_ticker=identifier,
        market_symbol=None,
        quantity=Decimal(quantity),
        currency=currency,
        average_cost=None if average_cost is None else Decimal(average_cost),
        cost_basis_status=(
            CostBasisStatus.UNAVAILABLE
            if average_cost is None
            else CostBasisStatus.AVAILABLE
        ),
        source=ImportSource.INVESTMENTS,
        name=f"Synthetic {identifier}",
    )


def _snapshot(*positions: ImportedPosition) -> PortfolioSnapshot:
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
                rows_processed=len(positions),
            ),
            ImportSource.XAU_STATEMENT: SourceImportMetadata.not_imported(
                ImportSource.XAU_STATEMENT
            ),
        },
        positions=positions,
    )


def _mapping(
    positions: tuple[ImportedPosition, ...],
    *,
    excluded: set[str] | None = None,
) -> MarketMapping:
    excluded = excluded or set()
    return MarketMapping(
        version=1,
        instruments={
            position.source_ticker: (
                MarketMappingEntry(enabled=False)
                if position.source_ticker in excluded
                else MarketMappingEntry(
                    enabled=True,
                    market_symbol=f"{position.source_ticker}.TEST",
                    provider="yfinance",
                    expected_currency=position.currency,
                )
            )
            for position in positions
        },
    )


def _valuation(
    snapshot: PortfolioSnapshot,
    mapping: MarketMapping,
    provider: StubProvider,
):
    return value_portfolio(snapshot, mapping, lambda _: provider, now=lambda: NOW)


def _write_mapping(mapping: MarketMapping, path: Path) -> None:
    lines = ["version: 1", "instruments:"]
    for identifier, entry in mapping.instruments.items():
        lines.extend((f"  {identifier}:", f"    enabled: {str(entry.enabled).lower()}"))
        if entry.enabled:
            lines.extend(
                (
                    f"    market_symbol: {entry.market_symbol}",
                    f"    provider: {entry.provider}",
                    f"    expected_currency: {entry.expected_currency}",
                )
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _cli_files(tmp_path: Path, snapshot: PortfolioSnapshot, mapping: MarketMapping):
    snapshot_path = tmp_path / "synthetic_snapshot.yaml"
    mapping_path = tmp_path / "synthetic_mapping.yaml"
    output = tmp_path / "private" / "valuation.json"
    save_portfolio_snapshot(snapshot, snapshot_path)
    _write_mapping(mapping, mapping_path)
    return snapshot_path, mapping_path, output


def _cli_args(snapshot: Path, mapping: Path, output: Path) -> list[str]:
    return [
        "generate-valuation-report",
        "--snapshot",
        str(snapshot),
        "--mapping",
        str(mapping),
        "--output",
        str(output),
        "--format",
        "json",
    ]


def test_complete_report_has_stable_contract_exact_decimals_and_separate_currencies(
    tmp_path: Path,
) -> None:
    eur = _position("SYNTH-EUR", "EUR")
    usd = _position("SYNTH-USD", "USD", quantity="3.25", average_cost="4.75")
    excluded = _position("SYNTH-XAU", "XAU", average_cost=None)
    snapshot = _snapshot(eur, usd, excluded)
    result = _valuation(
        snapshot,
        _mapping(snapshot.positions, excluded={"SYNTH-XAU"}),
        StubProvider(
            {
                "SYNTH-EUR": Decimal("3.123456789123456789"),
                "SYNTH-USD": Decimal("5.25"),
            }
        ),
    )
    output = tmp_path / "new" / "private_report.json"

    write_valuation_report(result, output)

    raw = output.read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert raw.endswith("\n")
    assert payload["report_contract_version"] == 1
    assert payload["valuation_schema_version"] == 1
    assert {
        "metadata",
        "coverage",
        "currency_totals",
        "positions",
        "exclusions",
        "warnings",
        "errors",
        "unavailable_fields",
    }.issubset(payload)
    assert payload["metadata"] == {
        "amounts_separated_by_currency": True,
        "currency_conversion": False,
        "executed_at": NOW.isoformat(),
        "generated_at": NOW.isoformat(),
        "providers": ["yfinance"],
        "quote_types": ["close"],
        "snapshot_generated_at": NOW.isoformat(),
        "valuation_status": "complete",
    }
    assert {item["currency"] for item in payload["currency_totals"]} == {"EUR", "USD"}
    assert payload["positions"][0]["quantity"] == "1.000000000000000001"
    assert payload["positions"][0]["market_price"] == "3.123456789123456789"
    assert payload["exclusions"] == [
        {
            "instrument_id": "SYNTH-XAU",
            "reason": "market_mapping_disabled",
            "status": "excluded",
        }
    ]
    assert "No existe conversión" in payload["warnings"][0]


def test_atomic_replace_uses_same_directory_and_replaces_complete_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    position = _position("SYNTH", "EUR")
    snapshot = _snapshot(position)
    result = _valuation(
        snapshot,
        _mapping(snapshot.positions),
        StubProvider({"SYNTH": Decimal("4")}),
    )
    output = tmp_path / "report.json"
    output.write_text("old complete report\n", encoding="utf-8")
    actual_replace = os.replace
    replacements: list[tuple[Path, Path]] = []

    def record_replace(source: str | Path, destination: str | Path) -> None:
        replacements.append((Path(source), Path(destination)))
        actual_replace(source, destination)

    monkeypatch.setattr("openportfolio.persistence.valuation_report.os.replace", record_replace)

    write_valuation_report(result, output)

    assert replacements[0][0].parent == output.parent
    assert replacements[0][1] == output
    assert json.loads(output.read_text(encoding="utf-8"))["metadata"]["valuation_status"] == "complete"
    assert list(tmp_path.glob(".*.tmp")) == []


def test_failed_atomic_replace_preserves_existing_report_and_sanitizes_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    position = _position("SYNTH", "EUR")
    snapshot = _snapshot(position)
    result = _valuation(
        snapshot,
        _mapping(snapshot.positions),
        StubProvider({"SYNTH": Decimal("4")}),
    )
    output = tmp_path / "report.json"
    previous = b'{"previous":"complete"}\n'
    output.write_bytes(previous)

    def fail_replace(*_: object) -> None:
        raise OSError("PRIVATE FILESYSTEM DETAIL")

    monkeypatch.setattr("openportfolio.persistence.valuation_report.os.replace", fail_replace)

    with pytest.raises(ValuationReportError) as caught:
        write_valuation_report(result, output)

    assert "PRIVATE FILESYSTEM DETAIL" not in str(caught.value)
    assert output.read_bytes() == previous
    assert list(tmp_path.glob(".*.tmp")) == []


def test_invalid_json_construction_never_replaces_existing_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    position = _position("SYNTH", "EUR")
    snapshot = _snapshot(position)
    result = _valuation(
        snapshot,
        _mapping(snapshot.positions),
        StubProvider({"SYNTH": Decimal("4")}),
    )
    output = tmp_path / "report.json"
    previous = b'{"previous":"complete"}\n'
    output.write_bytes(previous)
    monkeypatch.setattr(
        "openportfolio.persistence.valuation_report.build_valuation_report",
        lambda _: {"not_json": Decimal("1.1")},
    )

    with pytest.raises(ValuationReportError, match="JSON válido"):
        write_valuation_report(result, output)

    assert output.read_bytes() == previous


def test_partial_result_preserves_default_output_and_requires_explicit_opt_in(
    tmp_path: Path,
) -> None:
    positions = (_position("SYNTH-OK", "USD"), _position("SYNTH-FAIL", "USD"))
    snapshot = _snapshot(*positions)
    result = _valuation(
        snapshot,
        _mapping(snapshot.positions),
        StubProvider(
            {"SYNTH-OK": Decimal("4")},
            failures={"SYNTH-FAIL"},
        ),
    )
    output = tmp_path / "latest.json"
    previous = b'{"metadata":{"valuation_status":"complete"}}\n'
    output.write_bytes(previous)

    with pytest.raises(PartialValuationReportError):
        write_valuation_report(result, output)
    assert output.read_bytes() == previous

    with pytest.raises(PartialValuationReportError, match="destino nuevo"):
        write_valuation_report(result, output, allow_partial=True)
    assert output.read_bytes() == previous

    partial_output = tmp_path / "requested_partial.json"
    write_valuation_report(result, partial_output, allow_partial=True)
    payload = json.loads(partial_output.read_text(encoding="utf-8"))
    assert payload["metadata"]["valuation_status"] == "partial"
    assert payload["coverage"]["positions_failed"] == 1
    assert "PRIVATE PROVIDER PAYLOAD" not in partial_output.read_text(encoding="utf-8")


def test_mapping_not_ready_never_queries_or_writes_even_when_partial_is_allowed(
    tmp_path: Path,
) -> None:
    position = _position("SYNTH-MISSING", "EUR")
    snapshot = _snapshot(position)
    provider = StubProvider({})
    result = _valuation(snapshot, MarketMapping(version=1, instruments={}), provider)

    with pytest.raises(ValuationReportError, match="mapping no está listo"):
        write_valuation_report(result, tmp_path / "report.json", allow_partial=True)

    assert provider.calls == []
    assert not (tmp_path / "report.json").exists()


def test_cli_complete_report_returns_zero_and_prints_only_operational_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    position = _position("SYNTH-PRIVATE-ID", "EUR", quantity="98765.4321")
    snapshot = _snapshot(position)
    mapping = _mapping(snapshot.positions)
    snapshot_path, mapping_path, output = _cli_files(tmp_path, snapshot, mapping)
    snapshot_before = snapshot_path.read_bytes()
    mapping_before = mapping_path.read_bytes()
    provider = StubProvider({"SYNTH-PRIVATE-ID": Decimal("12345.6789")})
    monkeypatch.setattr("openportfolio.cli._provider", lambda *_: provider)

    exit_code = main(_cli_args(snapshot_path, mapping_path, output))

    captured = capsys.readouterr()
    assert exit_code == 0
    assert output.is_file()
    assert "Informe: generado" in captured.out
    assert "Estado: completo" in captured.out
    assert "Código de salida: 0" in captured.out
    assert "SYNTH-PRIVATE-ID" not in captured.out + captured.err
    assert "98765.4321" not in captured.out + captured.err
    assert "12345.6789" not in captured.out + captured.err
    assert captured.err == ""
    assert snapshot_path.read_bytes() == snapshot_before
    assert mapping_path.read_bytes() == mapping_before
    report_text = output.read_text(encoding="utf-8")
    assert str(tmp_path) not in report_text
    assert "sources:" not in report_text


def test_cli_partial_policy_returns_nonzero_and_preserves_latest_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    positions = (_position("SYNTH-OK", "USD"), _position("SYNTH-FAIL", "USD"))
    snapshot = _snapshot(*positions)
    mapping = _mapping(snapshot.positions)
    snapshot_path, mapping_path, output = _cli_files(tmp_path, snapshot, mapping)
    output.parent.mkdir(parents=True)
    previous = b'{"metadata":{"valuation_status":"complete"}}\n'
    output.write_bytes(previous)
    provider = StubProvider({"SYNTH-OK": Decimal("4")}, failures={"SYNTH-FAIL"})
    monkeypatch.setattr("openportfolio.cli._provider", lambda *_: provider)

    exit_code = main(_cli_args(snapshot_path, mapping_path, output))

    captured = capsys.readouterr()
    assert exit_code == 1
    assert output.read_bytes() == previous
    assert "Informe: no generado" in captured.out
    assert "Estado: parcial" in captured.out
    assert "Código de salida: 1" in captured.out
    assert "SYNTH-FAIL" not in captured.out + captured.err
    assert "PRIVATE PROVIDER PAYLOAD" not in captured.out + captured.err


def test_cli_explicit_partial_output_is_written_but_returns_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    positions = (_position("SYNTH-OK", "USD"), _position("SYNTH-FAIL", "USD"))
    snapshot = _snapshot(*positions)
    mapping = _mapping(snapshot.positions)
    snapshot_path, mapping_path, output = _cli_files(tmp_path, snapshot, mapping)
    provider = StubProvider({"SYNTH-OK": Decimal("4")}, failures={"SYNTH-FAIL"})
    monkeypatch.setattr("openportfolio.cli._provider", lambda *_: provider)

    exit_code = main(
        _cli_args(snapshot_path, mapping_path, output) + ["--allow-partial-output"]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert json.loads(output.read_text(encoding="utf-8"))["metadata"]["valuation_status"] == "partial"
    assert "Informe: generado" in captured.out
    assert "Código de salida: 1" in captured.out


def test_cli_write_failure_returns_nonzero_preserves_previous_and_sanitizes_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    position = _position("SYNTH", "EUR")
    snapshot = _snapshot(position)
    mapping = _mapping(snapshot.positions)
    snapshot_path, mapping_path, output = _cli_files(tmp_path, snapshot, mapping)
    output.parent.mkdir(parents=True)
    previous = b'{"metadata":{"valuation_status":"complete"}}\n'
    output.write_bytes(previous)
    monkeypatch.setattr(
        "openportfolio.cli._provider",
        lambda *_: StubProvider({"SYNTH": Decimal("4")}),
    )

    def fail_replace(*_: object) -> None:
        raise OSError("PRIVATE FILESYSTEM DETAIL")

    monkeypatch.setattr("openportfolio.persistence.valuation_report.os.replace", fail_replace)

    exit_code = main(_cli_args(snapshot_path, mapping_path, output))

    captured = capsys.readouterr()
    assert exit_code == 3
    assert output.read_bytes() == previous
    assert "Informe: no generado" in captured.out
    assert "Código de salida: 3" in captured.out
    assert "PRIVATE FILESYSTEM DETAIL" not in captured.out + captured.err


@pytest.mark.parametrize(
    ("invalid_kind", "contents"),
    (
        ("snapshot", "not: [valid"),
        ("mapping", "version: wrong\ninstruments: {}\n"),
    ),
)
def test_cli_invalid_private_input_writes_nothing_and_never_builds_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    invalid_kind: str,
    contents: str,
) -> None:
    position = _position("SYNTH", "EUR")
    snapshot = _snapshot(position)
    mapping = _mapping(snapshot.positions)
    snapshot_path, mapping_path, output = _cli_files(tmp_path, snapshot, mapping)
    invalid_path = snapshot_path if invalid_kind == "snapshot" else mapping_path
    invalid_path.write_text(contents, encoding="utf-8")
    monkeypatch.setattr(
        "openportfolio.cli._provider",
        lambda *_: pytest.fail("no provider expected"),
    )

    assert main(_cli_args(snapshot_path, mapping_path, output)) != 0

    captured = capsys.readouterr()
    assert not output.exists()
    assert "Informe: no generado" in captured.out
    assert "SYNTH" not in captured.out + captured.err


def test_cli_mapping_incomplete_performs_no_queries_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    position = _position("SYNTH", "EUR")
    snapshot = _snapshot(position)
    snapshot_path, mapping_path, output = _cli_files(
        tmp_path,
        snapshot,
        MarketMapping(version=1, instruments={}),
    )
    monkeypatch.setattr(
        "openportfolio.cli._provider",
        lambda *_: pytest.fail("no provider expected"),
    )

    assert main(_cli_args(snapshot_path, mapping_path, output)) == 1
    assert not output.exists()


def test_build_report_is_deterministic_for_same_valuation() -> None:
    position = _position("SYNTH", "EUR")
    snapshot = _snapshot(position)
    result = _valuation(
        snapshot,
        _mapping(snapshot.positions),
        StubProvider({"SYNTH": Decimal("4")}),
    )

    assert build_valuation_report(result) == build_valuation_report(result)
