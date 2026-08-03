from pathlib import Path

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
