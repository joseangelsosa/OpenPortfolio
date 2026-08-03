from pathlib import Path

import pytest

from openportfolio.cli import main
from openportfolio.domain import Alert


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


def test_real_ntfy_review_fails_safely_without_topic(
    monkeypatch: pytest.MonkeyPatch, capsys: object
) -> None:
    monkeypatch.delenv("OPENPORTFOLIO_NTFY_TOPIC", raising=False)
    result = main(["review", "--provider", "fake", "--notifier", "ntfy"])
    error = capsys.readouterr().err  # type: ignore[attr-defined]

    assert result == 2
    assert "OPENPORTFOLIO_NTFY_TOPIC" in error


def test_send_test_notification_sends_exactly_one_fake_without_real_dependencies(
    monkeypatch: pytest.MonkeyPatch, capsys: object
) -> None:
    sent: list[Alert] = []

    class CapturingNotifier:
        def send(self, alert: Alert) -> None:
            sent.append(alert)

    monkeypatch.setattr("openportfolio.cli.NtfyNotifier", CapturingNotifier)
    monkeypatch.setattr(
        "openportfolio.cli.load_portfolio",
        lambda *args, **kwargs: pytest.fail("no debe cargar cartera"),
    )
    monkeypatch.setattr(
        "openportfolio.cli._provider",
        lambda *args, **kwargs: pytest.fail("no debe construir yfinance ni fake"),
    )
    monkeypatch.setattr(
        "openportfolio.cli.JsonAlertStateStore",
        lambda *args, **kwargs: pytest.fail("no debe usar estado"),
    )

    assert main(["send-test-notification"]) == 0
    assert len(sent) == 1
    assert sent[0].title == "PRUEBA OpenPortfolio"
    assert "No corresponde a una revisión real de mercado" in sent[0].body
    assert "PRUEBA" in capsys.readouterr().out  # type: ignore[attr-defined]
