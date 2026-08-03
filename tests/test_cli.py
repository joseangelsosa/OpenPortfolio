from pathlib import Path

import pytest

from openportfolio.cli import main


EXAMPLE = Path(__file__).parents[1] / "examples" / "demo_portfolio.yaml"


def test_fake_cli_reports_separate_currency_totals(capsys: object) -> None:
    result = main(["--portfolio", str(EXAMPLE), "--provider", "fake"])
    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert result == 0
    assert "EUR: 307.40 EUR" in output
    assert "USD: 432.08 USD" in output
    assert "No se muestra un total global" in output
    assert "2026-01-02T16:00:00+00:00" in output
    assert "fake" in output


def test_review_cli_reports_generated_sent_and_suppressed(
    tmp_path: Path, capsys: object
) -> None:
    state_path = tmp_path / "alert_state.json"
    arguments = [
        "review",
        "--portfolio",
        str(EXAMPLE),
        "--provider",
        "fake",
        "--notifier",
        "console",
        "--state-path",
        str(state_path),
    ]

    assert main(arguments) == 0
    first = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "1 alertas generadas, 1 enviadas, 0 suprimidas" in first

    assert main(arguments) == 0
    second = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "1 alertas generadas, 0 enviadas, 1 suprimidas" in second


def test_yfinance_review_requires_dry_run(
    monkeypatch: pytest.MonkeyPatch, capsys: object
) -> None:
    def forbidden_provider(*args: object, **kwargs: object) -> object:
        raise AssertionError("el proveedor no debe construirse sin dry-run")

    monkeypatch.setattr("openportfolio.cli._provider", forbidden_provider)
    result = main(["review", "--provider", "yfinance"])
    error = capsys.readouterr().err  # type: ignore[attr-defined]

    assert result == 2
    assert "yfinance requiere --dry-run" in error
