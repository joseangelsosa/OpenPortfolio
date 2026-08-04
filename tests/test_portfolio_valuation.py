from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import pytest

from openportfolio.application import render_portfolio_valuation_text, value_portfolio
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
from openportfolio.persistence import save_portfolio_snapshot


NOW = datetime(2026, 8, 4, 10, 30, tzinfo=timezone.utc)


class StubProvider:
    name = "yfinance"

    def __init__(
        self,
        prices: dict[str, Decimal],
        *,
        currencies: dict[str, str] | None = None,
        failures: set[str] | None = None,
    ) -> None:
        self.prices = prices
        self.currencies = currencies or {}
        self.failures = failures or set()
        self.calls: list[str] = []

    def get_quote(self, instrument: object) -> MarketQuote:
        instrument_id = instrument.id  # type: ignore[attr-defined]
        self.calls.append(instrument_id)
        if instrument_id in self.failures:
            raise MarketDataNotFoundError("respuesta privada que no debe propagarse")
        return MarketQuote(
            id=f"quote-{instrument_id}",
            instrument_id=instrument_id,
            price=self.prices[instrument_id],
            currency=self.currencies.get(instrument_id, instrument.currency),  # type: ignore[attr-defined]
            observed_at=NOW,
            retrieved_at=NOW,
            provider=self.name,
            provider_symbol=instrument.symbol_for(self.name),  # type: ignore[attr-defined]
            source=QuoteSource.DAILY_CLOSE,
            kind="close",
        )


def _position(
    identifier: str,
    *,
    quantity: str,
    currency: str,
    average_cost: str | None,
) -> ImportedPosition:
    return ImportedPosition(
        asset_type="equity",
        source_ticker=identifier,
        market_symbol=f"IGNORED-{identifier}",
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
    positions: tuple[ImportedPosition, ...], *, excluded: set[str] | None = None
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


def _value(
    snapshot: PortfolioSnapshot,
    mapping: MarketMapping,
    provider: StubProvider,
):
    return value_portfolio(snapshot, mapping, lambda _: provider, now=lambda: NOW)


def test_complete_single_currency_valuation_uses_decimal_calculations() -> None:
    position = _position(
        "PRECISE", quantity="0.123456789123456789", currency="USD", average_cost="3.1"
    )
    snapshot = _snapshot(position)
    result = _value(snapshot, _mapping(snapshot.positions), StubProvider({"PRECISE": Decimal("4.2")}))

    item = result.positions[0]
    assert result.ok
    assert item.accumulated_cost == Decimal("0.3827160462827160459")
    assert item.market_value == Decimal("0.5185185143185185138")
    assert item.unrealized_gain_loss == Decimal("0.1358024680358024679")
    assert item.return_percent == (
        item.unrealized_gain_loss / item.accumulated_cost * Decimal("100")
    )
    assert result.currency_totals[0].return_percent == item.return_percent
    assert result.as_dict()["positions"][0]["quantity"] == "0.123456789123456789"


def test_currency_subtotals_remain_separate_and_aggregate_from_totals() -> None:
    positions = (
        _position("EUR-A", quantity="2", currency="EUR", average_cost="10"),
        _position("EUR-B", quantity="1", currency="EUR", average_cost="20"),
        _position("USD-A", quantity="3", currency="USD", average_cost="4"),
    )
    snapshot = _snapshot(*positions)
    provider = StubProvider(
        {"EUR-A": Decimal("20"), "EUR-B": Decimal("10"), "USD-A": Decimal("5")}
    )

    result = _value(snapshot, _mapping(snapshot.positions), provider)
    totals = {item.currency: item for item in result.currency_totals}

    assert set(totals) == {"EUR", "USD"}
    assert totals["EUR"].accumulated_cost == Decimal("40")
    assert totals["EUR"].market_value == Decimal("50")
    assert totals["EUR"].unrealized_gain_loss == Decimal("10")
    assert totals["EUR"].return_percent == Decimal("25")
    assert totals["USD"].market_value == Decimal("15")


def test_missing_average_cost_keeps_market_value_and_marks_partial() -> None:
    position = _position("NO-COST", quantity="2.5", currency="EUR", average_cost=None)
    snapshot = _snapshot(position)

    result = _value(
        snapshot,
        _mapping(snapshot.positions),
        StubProvider({"NO-COST": Decimal("7.25")}),
    )

    item = result.positions[0]
    assert item.status == "partial"
    assert item.market_value == Decimal("18.125")
    assert item.accumulated_cost is None
    assert item.unrealized_gain_loss is None
    assert item.return_percent is None
    assert result.partially_calculable_positions == 1
    assert result.currency_totals[0].market_value == Decimal("18.125")
    assert result.currency_totals[0].accumulated_cost is None


def test_explicit_exclusion_never_calls_provider() -> None:
    included = _position("ACTIVE", quantity="1", currency="USD", average_cost="2")
    excluded = _position("EXCLUDED", quantity="1", currency="XAU", average_cost=None)
    snapshot = _snapshot(included, excluded)
    provider = StubProvider({"ACTIVE": Decimal("3")})

    result = _value(snapshot, _mapping(snapshot.positions, excluded={"EXCLUDED"}), provider)

    assert provider.calls == ["ACTIVE"]
    assert [item.instrument_id for item in result.exclusions] == ["EXCLUDED"]
    assert all(item.instrument_id != "EXCLUDED" for item in result.positions)
    assert {item.currency for item in result.currency_totals} == {"USD"}


def test_quote_failure_is_sanitized_and_other_positions_continue() -> None:
    positions = (
        _position("FAIL", quantity="1", currency="USD", average_cost="2"),
        _position("OK", quantity="1", currency="USD", average_cost="3"),
    )
    snapshot = _snapshot(*positions)
    provider = StubProvider({"OK": Decimal("4")}, failures={"FAIL"})

    result = _value(snapshot, _mapping(snapshot.positions), provider)

    assert provider.calls == ["FAIL", "OK"]
    assert not result.ok
    assert result.failed_positions == 1
    assert result.valued_positions == 1
    assert result.errors[0].instrument_id == "FAIL"
    assert "respuesta privada" not in result.errors[0].message
    assert result.currency_totals[0].market_value is None


def test_quote_currency_mismatch_is_a_failure() -> None:
    position = _position("MISMATCH", quantity="1", currency="EUR", average_cost="2")
    snapshot = _snapshot(position)
    provider = StubProvider(
        {"MISMATCH": Decimal("3")}, currencies={"MISMATCH": "USD"}
    )

    result = _value(snapshot, _mapping(snapshot.positions), provider)

    assert result.failed_positions == 1
    assert result.positions[0].market_price is None
    assert result.errors[0].code == "quote_currency_mismatch"


def test_incomplete_mapping_is_rejected_before_resolving_provider() -> None:
    position = _position("MISSING", quantity="1", currency="USD", average_cost="2")
    snapshot = _snapshot(position)
    resolver_calls: list[str] = []

    result = value_portfolio(
        snapshot,
        MarketMapping(version=1, instruments={}),
        lambda name: resolver_calls.append(name),  # type: ignore[arg-type,return-value]
        now=lambda: NOW,
    )

    assert resolver_calls == []
    assert not result.ok
    assert result.errors[0].code == "missing_mapping"
    assert result.currency_totals == ()


def test_text_and_json_have_stable_documented_sections() -> None:
    position = _position("ACME", quantity="2", currency="USD", average_cost="10")
    snapshot = _snapshot(position)
    result = _value(snapshot, _mapping(snapshot.positions), StubProvider({"ACME": Decimal("12")}))

    text = render_portfolio_valuation_text(result)
    payload = result.as_dict()

    assert "Ejecución: 2026-08-04T10:30:00+00:00" in text
    assert "Subtotales por moneda:" in text
    assert "No existe conversión entre monedas" in text
    assert list(payload) == [
        "valuation_schema_version",
        "metadata",
        "coverage",
        "currency_totals",
        "positions",
        "exclusions",
        "warnings",
        "errors",
        "unavailable_fields",
    ]
    assert payload["metadata"]["providers"] == ["yfinance"]
    assert payload["metadata"]["quote_types"] == ["close"]
    assert json.loads(json.dumps(payload, sort_keys=True)) == payload


def _save_inputs(tmp_path: Path, snapshot: PortfolioSnapshot) -> tuple[Path, Path]:
    snapshot_path = tmp_path / "synthetic-snapshot.yaml"
    mapping_path = tmp_path / "synthetic-mapping.yaml"
    save_portfolio_snapshot(snapshot, snapshot_path)
    mapping_path.write_text(
        """\
version: 1
instruments:
  ACME:
    market_symbol: ACME.TEST
    provider: yfinance
    expected_currency: USD
    enabled: true
""",
        encoding="utf-8",
    )
    return snapshot_path, mapping_path


@pytest.mark.parametrize("output_format", ["text", "json"])
def test_cli_formats_exit_zero_and_do_not_modify_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    output_format: str,
) -> None:
    snapshot = _snapshot(
        _position("ACME", quantity="2", currency="USD", average_cost="10")
    )
    snapshot_path, mapping_path = _save_inputs(tmp_path, snapshot)
    before = {
        path: hashlib.sha256(path.read_bytes()).digest()
        for path in (snapshot_path, mapping_path)
    }
    monkeypatch.setattr(
        "openportfolio.cli._provider",
        lambda *_: StubProvider({"ACME": Decimal("12")}),
    )

    code = main(
        [
            "value-portfolio",
            "--snapshot",
            str(snapshot_path),
            "--mapping",
            str(mapping_path),
            "--format",
            output_format,
        ]
    )
    captured = capsys.readouterr()

    assert code == 0
    assert captured.err == ""
    if output_format == "json":
        assert json.loads(captured.out)["coverage"]["positions_valued"] == 1
    else:
        assert "Valoradas: 1" in captured.out
    assert before == {
        path: hashlib.sha256(path.read_bytes()).digest()
        for path in (snapshot_path, mapping_path)
    }


def test_cli_returns_one_for_quote_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    snapshot = _snapshot(
        _position("ACME", quantity="2", currency="USD", average_cost="10")
    )
    snapshot_path, mapping_path = _save_inputs(tmp_path, snapshot)
    monkeypatch.setattr(
        "openportfolio.cli._provider",
        lambda *_: StubProvider({}, failures={"ACME"}),
    )

    assert main(
        [
            "value-portfolio",
            "--snapshot",
            str(snapshot_path),
            "--mapping",
            str(mapping_path),
            "--format",
            "json",
        ]
    ) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["coverage"]["positions_failed"] == 1


def test_cli_input_error_is_sanitized_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secret_name = "private-account-112233.yaml"

    assert main(
        [
            "value-portfolio",
            "--snapshot",
            str(tmp_path / secret_name),
            "--mapping",
            str(tmp_path / "mapping.yaml"),
            "--format",
            "json",
        ]
    ) == 2
    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert payload["errors"][0]["code"] == "snapshot_not_readable"
    assert secret_name not in captured.out + captured.err
