from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from openportfolio.application import combine_revolut_imports, reconciliation_report
from openportfolio.cli import main
from openportfolio.domain import (
    CostBasisStatus,
    ImportSource,
    PortfolioSnapshot,
    PositionStatus,
)
from openportfolio.importers import (
    ACCOUNT_STATEMENT_HEADER,
    INVESTMENTS_HEADER,
    InstrumentPolicy,
    REVOLUT_INSTRUMENTS,
    detect_revolut_format,
    import_investments,
    import_xau_statement,
)
from openportfolio.persistence import (
    PortfolioSnapshotError,
    load_portfolio_snapshot,
    save_portfolio_snapshot,
)


NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)


def _investment_row(
    *,
    date: str = "2026-01-01T10:00:00Z",
    ticker: str = "NVDA",
    kind: str = "BUY - MARKET",
    quantity: str = "2",
    price: str = "USD 10.00",
    total: str = "USD 20.00",
    currency: str = "USD",
    fx: str = "1",
) -> list[str]:
    return [date, ticker, kind, quantity, price, total, currency, fx]


def _write_csv(path: Path, header: tuple[str, ...], rows: list[list[str]]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def _investments(path: Path, rows: list[list[str]]) -> Path:
    return _write_csv(path, INVESTMENTS_HEADER, rows)


def _xau_row(
    *,
    ended: str,
    description: str,
    amount: str,
    fee: str,
    balance: str,
    state: str = "COMPLETADO",
    currency: str = "XAU",
) -> list[str]:
    return [
        "Cambio",
        "Metales",
        ended,
        ended,
        description,
        amount,
        fee,
        currency,
        state,
        balance,
    ]


def _xau(path: Path, rows: list[list[str]]) -> Path:
    return _write_csv(path, ACCOUNT_STATEMENT_HEADER, rows)


def _single_xau(path: Path, amount: str = "0.250500", fee: str = "0.000500") -> Path:
    balance = Decimal(amount) - Decimal(fee)
    return _xau(
        path,
        [_xau_row(ended="2026-01-01 10:00:00", description="Conversión a XAU", amount=amount, fee=fee, balance=str(balance))],
    )


def test_single_fractional_buy_uses_total_amount_as_effective_cost(tmp_path: Path) -> None:
    path = _investments(
        tmp_path / "arbitrary.data",
        [_investment_row(quantity="1.234567890123", price="USD 8", total="USD 9.90")],
    )

    result = import_investments(path)

    assert result.ok
    position = result.positions[0]
    assert position.quantity == Decimal("1.234567890123")
    assert position.average_cost == Decimal("9.90") / Decimal("1.234567890123")
    assert result.counts["buys"] == 1
    assert result.warnings


def test_weighted_average_and_partial_sale_keep_previous_average(tmp_path: Path) -> None:
    path = _investments(
        tmp_path / "trades.csv",
        [
            _investment_row(date="2026-01-03T10:00:00Z", quantity="1", price="USD 30", total="USD 30"),
            _investment_row(date="2026-01-01T10:00:00Z", quantity="2", price="USD 10", total="USD 20"),
            _investment_row(date="2026-01-02T10:00:00Z", quantity="2", price="USD 20", total="USD 40"),
            _investment_row(date="2026-01-04T10:00:00Z", kind="SELL - LIMIT", quantity="2", price="USD 100", total="USD 200"),
        ],
    )

    result = import_investments(path)

    assert result.positions[0].quantity == Decimal("3")
    assert result.positions[0].average_cost == Decimal("18")
    assert result.counts["buys"] == 3
    assert result.counts["sells"] == 1


def test_full_sale_is_closed_and_not_open(tmp_path: Path) -> None:
    path = _investments(
        tmp_path / "closed.csv",
        [
            _investment_row(),
            _investment_row(date="2026-01-02T10:00:00Z", kind="SELL - STOP", quantity="2", price="USD 12", total="USD 24"),
        ],
    )
    result = import_investments(path)
    assert result.positions == ()
    assert result.closed_positions == ("NVDA",)


def test_sale_above_position_is_blocking(tmp_path: Path) -> None:
    path = _investments(
        tmp_path / "oversale.csv",
        [_investment_row(), _investment_row(date="2026-01-02T10:00:00Z", kind="SELL - LIMIT", quantity="3")],
    )
    result = import_investments(path)
    assert not result.ok
    assert "superior" in result.errors[0].message
    assert result.errors[0].row_number == 3


def test_different_tickers_keep_usd_and_eur_separate(tmp_path: Path) -> None:
    path = _investments(
        tmp_path / "currencies.csv",
        [
            _investment_row(),
            _investment_row(ticker="H4ZF", currency="EUR", price="EUR 5", total="EUR 10"),
        ],
    )
    result = import_investments(path)
    assert {(p.source_ticker, p.currency) for p in result.positions} == {("NVDA", "USD"), ("H4ZF", "EUR")}
    assert {p.source_ticker: p.market_symbol for p in result.positions} == {"H4ZF": "H4ZF.DE", "NVDA": "NVDA"}


def test_same_ticker_in_two_currencies_is_blocking(tmp_path: Path) -> None:
    path = _investments(
        tmp_path / "mixed.csv",
        [_investment_row(), _investment_row(date="2026-01-02T10:00:00Z", currency="EUR", price="EUR 10", total="EUR 20")],
    )
    result = import_investments(path)
    assert not result.ok
    assert "más de una moneda" in result.errors[0].message


def test_dividend_cash_and_reward_are_counted_but_do_not_change_position(tmp_path: Path) -> None:
    rows = [_investment_row()]
    rows.extend(
        [
            _investment_row(kind="DIVIDEND", quantity="", price="", total="USD 1"),
            _investment_row(kind="CASH TOP-UP", ticker="", quantity="", price="", total="USD 20"),
            _investment_row(kind="CASH WITHDRAWAL", ticker="", quantity="", price="", total="USD -3"),
            _investment_row(kind="REWARD", ticker="", quantity="", price="", total="USD 1"),
        ]
    )
    result = import_investments(_investments(tmp_path / "ignored.csv", rows))
    assert result.positions[0].quantity == Decimal("2")
    assert result.positions[0].average_cost == Decimal("10")
    assert result.counts == {"buys": 1, "sells": 0, "dividends": 1, "cash": 2, "rewards": 1}


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda row: row.__setitem__(2, "UNKNOWN"), "desconocido"),
        (lambda row: row.__setitem__(5, "not-money"), "importe monetario"),
        (lambda row: row.__setitem__(0, "yesterday"), "ISO-8601"),
        (lambda row: row.__setitem__(5, "EUR 20"), "distinta de Currency"),
    ],
)
def test_invalid_investment_rows_have_sanitized_line_errors(
    tmp_path: Path, mutator: object, message: str
) -> None:
    row = _investment_row()
    mutator(row)  # type: ignore[operator]
    result = import_investments(_investments(tmp_path / "invalid.csv", [row]))
    assert not result.ok
    assert result.errors[0].row_number == 2
    assert message in result.errors[0].message
    assert str(row) not in result.errors[0].render()


def test_invalid_header_is_blocking(tmp_path: Path) -> None:
    result = import_investments(_write_csv(tmp_path / "bad.csv", ("wrong",), [["value"]]))
    assert not result.ok
    assert result.errors[0].row_number == 1


def test_unknown_ticker_is_preserved_but_unresolved(tmp_path: Path) -> None:
    result = import_investments(_investments(tmp_path / "ticker.csv", [_investment_row(ticker="SYNTH")]))
    assert result.positions[0].source_ticker == "SYNTH"
    assert result.positions[0].market_symbol is None


def test_confirmed_catalog_contains_names_and_all_active_market_mappings() -> None:
    expected = {
        "H4ZF": "H4ZF.DE",
        "H4ZC": "H4ZC.DE",
        "NESR": "NESR.DE",
        "IBE1": "IBE1.DE",
        "B4F": "B4F.F",
        "ZAL": "ZAL.DE",
        "AAPL": "AAPL",
        "AVGO": "AVGO",
        "GOOGL": "GOOGL",
        "GTLB": "GTLB",
        "KO": "KO",
        "KOS": "KOS",
        "MSFT": "MSFT",
        "NFLX": "NFLX",
        "NKE": "NKE",
        "NVDA": "NVDA",
        "NVO": "NVO",
        "PG": "PG",
        "SST": "SST",
        "TM": "TM",
        "UBER": "UBER",
    }
    assert {ticker: REVOLUT_INSTRUMENTS[ticker].market_symbol for ticker in expected} == expected
    assert all(REVOLUT_INSTRUMENTS[ticker].name for ticker in expected)


def test_declarative_legacy_policy_preserves_quantity_but_excludes_monitoring(
    tmp_path: Path,
) -> None:
    reason = "Razón sintética configurable"
    catalog = {
        "OLD": InstrumentPolicy(
            name="Legacy Synthetic",
            market_symbol=None,
            position_status=PositionStatus.LEGACY,
            tradable=False,
            active_monitoring=False,
            exclusion_reason=reason,
        )
    }
    result = import_investments(
        _investments(tmp_path / "legacy.csv", [_investment_row(ticker="OLD", quantity="10")]),
        instrument_catalog=catalog,
    )
    position = result.positions[0]
    assert result.ok
    assert position.quantity == Decimal("10")
    assert position.position_status is PositionStatus.LEGACY
    assert position.market_symbol is None
    assert not position.tradable and not position.active_monitoring
    assert position.exclusion_reason == reason
    assert result.closed_positions == ()
    assert "tickers activos sin mapping" not in "\n".join(
        warning.message for warning in result.warnings
    )

    outcome = combine_revolut_imports((result,), None, generated_at=NOW)
    report = reconciliation_report(outcome, generated_at=NOW)
    assert "Posiciones activas de acciones y ETF: 0" in report
    assert "Posiciones históricas no operativas: 1" in report
    assert "cantidad histórica: 10" in report
    assert reason in report


def test_irbtq_default_policy_is_legacy_and_never_receives_market_symbol(
    tmp_path: Path,
) -> None:
    result = import_investments(
        _investments(tmp_path / "irbtq.csv", [_investment_row(ticker="IRBTQ", quantity="10")])
    )
    position = result.positions[0]
    assert position.quantity == Decimal("10")
    assert position.position_status is PositionStatus.LEGACY
    assert position.market_symbol is None
    assert not position.active_monitoring
    assert "quiebra" in (position.exclusion_reason or "").lower()


def test_xau_purchase_fee_and_partial_sale_reconcile_to_balance(tmp_path: Path) -> None:
    path = _xau(
        tmp_path / "metal.csv",
        [
            _xau_row(ended="2026-01-01 10:00:00", description="Conversión a XAU", amount="0.301", fee="0.001", balance="0.300"),
            _xau_row(ended="2026-01-02 10:00:00", description="Conversión a EUR", amount="-0.100", fee="0.0005", balance="0.1995"),
        ],
    )
    result = import_xau_statement(path)
    position = result.positions[0]
    assert result.ok
    assert position.quantity == Decimal("0.1995")
    assert position.average_cost is None
    assert position.cost_basis_status is CostBasisStatus.UNAVAILABLE
    assert result.counts["xau_buys"] == 1 and result.counts["xau_sells"] == 1


def test_xau_balance_discrepancy_is_blocking(tmp_path: Path) -> None:
    path = _xau(
        tmp_path / "bad-metal.csv",
        [_xau_row(ended="2026-01-01 10:00:00", description="Conversión a XAU", amount="0.2", fee="0.001", balance="0.3")],
    )
    # A single-row export can legitimately have an opening balance, so add a bad transition.
    with path.open("a", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerow(_xau_row(ended="2026-01-02 10:00:00", description="Conversión a XAU", amount="0.1", fee="0", balance="0.5"))
    result = import_xau_statement(path)
    assert not result.ok
    assert "no concilia" in result.errors[0].message


def test_format_detection_uses_header_not_filename(tmp_path: Path) -> None:
    investments = _investments(tmp_path / "anything.bin", [_investment_row()])
    statement = _single_xau(tmp_path / "another.data")
    assert detect_revolut_format(investments) is ImportSource.INVESTMENTS
    assert detect_revolut_format(statement) is ImportSource.XAU_STATEMENT
    unknown = _write_csv(tmp_path / "unknown.csv", ("a", "b"), [["1", "2"]])
    with pytest.raises(ValueError, match="desconocido"):
        detect_revolut_format(unknown)


def _combined(
    investment_path: Path | None,
    xau_path: Path | None,
    existing: PortfolioSnapshot | None = None,
    now: datetime = NOW,
):
    results = []
    if investment_path is not None:
        results.append(import_investments(investment_path))
    if xau_path is not None:
        results.append(import_xau_statement(xau_path))
    return combine_revolut_imports(results, existing, generated_at=now)


def test_investments_only_without_previous_snapshot_marks_xau_not_imported(tmp_path: Path) -> None:
    outcome = _combined(_investments(tmp_path / "i.csv", [_investment_row()]), None)
    assert outcome.ok and outcome.snapshot is not None
    assert outcome.snapshot.sources[ImportSource.XAU_STATEMENT].status.value == "not_imported"
    assert {p.source for p in outcome.snapshot.positions} == {ImportSource.INVESTMENTS}


def test_xau_only_without_previous_snapshot_marks_investments_not_imported(tmp_path: Path) -> None:
    outcome = _combined(None, _single_xau(tmp_path / "x.csv"))
    assert outcome.ok and outcome.snapshot is not None
    assert outcome.snapshot.sources[ImportSource.INVESTMENTS].status.value == "not_imported"
    assert outcome.snapshot.positions[0].source is ImportSource.XAU_STATEMENT


def test_incremental_updates_preserve_other_partition_and_dates(tmp_path: Path) -> None:
    investments = _investments(tmp_path / "i.csv", [_investment_row()])
    xau = _single_xau(tmp_path / "x.csv")
    first = _combined(investments, xau)
    assert first.snapshot is not None
    old_investment_update = first.snapshot.sources[ImportSource.INVESTMENTS].updated_at
    new_time = NOW + timedelta(days=2)
    second = _combined(None, _single_xau(tmp_path / "x2.csv", amount="0.4", fee="0"), first.snapshot, new_time)
    assert second.snapshot is not None
    assert {p.source_ticker for p in second.snapshot.positions} == {"NVDA", "XAU"}
    assert second.snapshot.sources[ImportSource.INVESTMENTS].updated_at == old_investment_update
    assert second.snapshot.sources[ImportSource.XAU_STATEMENT].updated_at == new_time
    third = _combined(_investments(tmp_path / "i2.csv", [_investment_row(ticker="MSFT")]), None, second.snapshot, new_time + timedelta(days=1))
    assert third.snapshot is not None
    assert {p.source_ticker for p in third.snapshot.positions} == {"MSFT", "XAU"}


def test_importing_both_updates_both_partitions(tmp_path: Path) -> None:
    outcome = _combined(
        _investments(tmp_path / "i.csv", [_investment_row()]),
        _single_xau(tmp_path / "x.csv"),
    )
    assert outcome.ok and outcome.snapshot is not None
    assert set(outcome.updated_sources) == set(ImportSource)
    assert {p.source_ticker for p in outcome.snapshot.positions} == {"NVDA", "XAU"}


def test_error_in_new_source_produces_no_partially_updated_snapshot(tmp_path: Path) -> None:
    initial = _combined(None, _single_xau(tmp_path / "x.csv"))
    assert initial.snapshot is not None
    bad = _investments(tmp_path / "bad.csv", [_investment_row(kind="UNKNOWN")])
    outcome = _combined(bad, None, initial.snapshot)
    assert not outcome.ok
    assert outcome.snapshot is None


def test_closing_equity_does_not_remove_xau_and_closing_xau_does_not_remove_equity(tmp_path: Path) -> None:
    initial = _combined(
        _investments(tmp_path / "i.csv", [_investment_row()]),
        _single_xau(tmp_path / "x.csv"),
    )
    assert initial.snapshot is not None
    closed_equity = _investments(
        tmp_path / "closed-i.csv",
        [_investment_row(), _investment_row(date="2026-01-02T10:00:00Z", kind="SELL - LIMIT", quantity="2")],
    )
    after_equity = _combined(closed_equity, None, initial.snapshot)
    assert after_equity.snapshot is not None
    assert [p.source_ticker for p in after_equity.snapshot.positions] == ["XAU"]
    closed_xau = _xau(
        tmp_path / "closed-x.csv",
        [
            _xau_row(ended="2026-01-01 10:00:00", description="Conversión a XAU", amount="0.2", fee="0", balance="0.2"),
            _xau_row(ended="2026-01-02 10:00:00", description="Conversión a EUR", amount="-0.2", fee="0", balance="0"),
        ],
    )
    after_xau = _combined(None, closed_xau, initial.snapshot)
    assert after_xau.snapshot is not None
    assert [p.source_ticker for p in after_xau.snapshot.positions] == ["NVDA"]


def test_snapshot_decimal_round_trip_is_lossless(tmp_path: Path) -> None:
    outcome = _combined(
        _investments(tmp_path / "i.csv", [_investment_row(quantity="1.123456789012345678", price="USD 2", total="USD 2.246913578024691356")]),
        None,
    )
    assert outcome.snapshot is not None
    path = tmp_path / "portfolio.yaml"
    save_portfolio_snapshot(outcome.snapshot, path)
    loaded = load_portfolio_snapshot(path)
    assert loaded == outcome.snapshot
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert raw["positions"][0]["quantity"] == "1.123456789012345678"


def test_incompatible_snapshot_version_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "old.yaml"
    path.write_text("schema_version: 99\n", encoding="utf-8")
    with pytest.raises(PortfolioSnapshotError, match="schema_version"):
        load_portfolio_snapshot(path)


def _cli_arguments(
    investments: Path,
    statement: Path,
    snapshot: Path,
    report: Path,
) -> list[str]:
    return [
        "import-revolut",
        "--investment-history",
        str(investments),
        "--account-statement",
        str(statement),
        "--snapshot-output",
        str(snapshot),
        "--report-output",
        str(report),
    ]


def test_import_revolut_cli_succeeds_creates_outputs_and_prints_safe_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    private_ticker = "PRIVATE-TICKER"
    investments = _investments(
        tmp_path / "investments.csv",
        [
            _investment_row(
                ticker=private_ticker,
                quantity="12.345678",
                price="EUR 98.76",
                total="EUR 1219.25913528",
                currency="EUR",
            )
        ],
    )
    statement = _single_xau(tmp_path / "statement.csv", amount="0.3456", fee="0.0001")
    snapshot = tmp_path / "generated" / "snapshots" / "portfolio.yaml"
    report = tmp_path / "generated" / "reports" / "reconciliation.txt"

    exit_code = main(_cli_arguments(investments, statement, snapshot, report))

    captured = capsys.readouterr()
    assert exit_code == 0
    assert snapshot.is_file() and report.is_file()
    assert load_portfolio_snapshot(snapshot).positions
    assert "Operaciones procesadas: 2" in captured.out
    assert "Posiciones resultantes: 2" in captured.out
    assert "Monedas encontradas: EUR, XAU" in captured.out
    assert "Conciliación: correcta" in captured.out
    assert str(snapshot) in captured.out and str(report) in captured.out
    assert captured.err == ""
    for private_detail in (
        private_ticker,
        "12.345678",
        "98.76",
        "1219.25913528",
        "0.3456",
    ):
        assert private_detail not in captured.out
        assert private_detail not in captured.err


def test_import_revolut_cli_missing_required_argument_exits_two(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["import-revolut"])
    assert raised.value.code == 2
    assert "--investment-history" in capsys.readouterr().err


def test_import_revolut_cli_rejects_missing_input_with_sanitized_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "customer-account-123456.csv"
    statement = _single_xau(tmp_path / "statement.csv")
    snapshot = tmp_path / "portfolio.yaml"
    report = tmp_path / "report.txt"

    exit_code = main(_cli_arguments(missing, statement, snapshot, report))

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "--investment-history" in captured.err
    assert missing.name not in captured.err
    assert not snapshot.exists() and not report.exists()


def test_import_revolut_cli_sanitizes_import_errors_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "CONFIDENTIAL-TICKER"
    investments = _investments(
        tmp_path / "investments.csv",
        [_investment_row(ticker=secret, kind="PRIVATE-OPERATION", total="USD 98765.43")],
    )
    statement = _single_xau(tmp_path / "statement.csv")
    snapshot = tmp_path / "portfolio.yaml"
    report = tmp_path / "report.txt"

    exit_code = main(_cli_arguments(investments, statement, snapshot, report))

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "los datos no son válidos" in captured.err
    assert secret not in captured.out + captured.err
    assert "98765.43" not in captured.out + captured.err
    assert "PRIVATE-OPERATION" not in captured.out + captured.err
    assert not snapshot.exists() and not report.exists()


def test_failed_atomic_replace_preserves_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    initial = _combined(_investments(tmp_path / "i.csv", [_investment_row()]), None)
    assert initial.snapshot is not None
    path = tmp_path / "portfolio.yaml"
    save_portfolio_snapshot(initial.snapshot, path)
    previous = path.read_bytes()
    monkeypatch.setattr("openportfolio.persistence.portfolio_snapshot.os.replace", lambda *_: (_ for _ in ()).throw(OSError("failure")))
    with pytest.raises(PortfolioSnapshotError, match="atómicamente"):
        save_portfolio_snapshot(initial.snapshot, path)
    assert path.read_bytes() == previous


def test_import_cli_never_constructs_network_notifier_or_alert_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    investments = _investments(tmp_path / "i.csv", [_investment_row()])
    statement = _single_xau(tmp_path / "x.csv")
    monkeypatch.setattr("openportfolio.cli.NtfyNotifier", lambda: pytest.fail("no notifier"))
    monkeypatch.setattr("openportfolio.cli.JsonAlertStateStore", lambda *_: pytest.fail("no state"))
    monkeypatch.setattr("openportfolio.cli._provider", lambda *_: pytest.fail("no market provider"))
    assert main(
        _cli_arguments(
            investments,
            statement,
            tmp_path / "p.yaml",
            tmp_path / "r.txt",
        )
    ) == 0
