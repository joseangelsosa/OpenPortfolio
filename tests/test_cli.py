from pathlib import Path
from urllib.error import HTTPError

import pytest

from openportfolio.alerts import NtfyNotifier
from openportfolio.cli import main
from openportfolio.domain import Alert
from openportfolio.providers import FakeMarketDataProvider


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
    assert "0 notificación operativa enviada" in first

    assert main(arguments) == 0
    second = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "1 alertas generadas, 0 enviadas, 1 suprimidas" in second
    assert "0 notificación operativa enviada" in second


def test_fake_review_cannot_enable_operational_heartbeat(
    monkeypatch: pytest.MonkeyPatch, capsys: object
) -> None:
    monkeypatch.setattr(
        "openportfolio.cli.NtfyNotifier",
        lambda: pytest.fail("no debe construir ntfy"),
    )

    result = main(
        [
            "review",
            "--portfolio",
            str(EXAMPLE),
            "--provider",
            "fake",
            "--notifier",
            "ntfy",
            "--operational-notification",
        ]
    )

    assert result == 2
    assert "solo corresponde a revisiones reales" in capsys.readouterr().err  # type: ignore[attr-defined]


def test_real_ntfy_review_fails_safely_without_topic(
    monkeypatch: pytest.MonkeyPatch, capsys: object
) -> None:
    monkeypatch.delenv("OPENPORTFOLIO_NTFY_TOPIC", raising=False)
    result = main(["review", "--provider", "fake", "--notifier", "ntfy"])
    error = capsys.readouterr().err  # type: ignore[attr-defined]

    assert result == 2
    assert "OPENPORTFOLIO_NTFY_TOPIC" in error


def test_operational_notifier_failure_is_visible_and_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    secret = "secret-topic-never-log"
    private_server = "https://private-notifications.example.test"

    def fail_http(*args: object, **kwargs: object) -> object:
        raise HTTPError(f"{private_server}/{secret}", 403, "forbidden", {}, None)

    monkeypatch.setattr(
        "openportfolio.cli._provider",
        lambda name, prices, sources: FakeMarketDataProvider(prices, sources),
    )
    monkeypatch.setattr(
        "openportfolio.cli.NtfyNotifier",
        lambda: NtfyNotifier(
            server=private_server,
            topic=secret,
            http_open=fail_http,
        ),
    )

    result = main(
        [
            "review",
            "--portfolio",
            str(EXAMPLE),
            "--provider",
            "yfinance",
            "--notifier",
            "ntfy",
            "--operational-notification",
            "--state-path",
            str(tmp_path / "alert_state.json"),
        ]
    )
    captured = capsys.readouterr()  # type: ignore[attr-defined]

    assert result == 3
    assert "HTTP 403" in captured.err
    assert secret not in captured.out + captured.err
    assert private_server not in captured.out + captured.err


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
