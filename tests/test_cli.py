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
