from decimal import Decimal
from pathlib import Path

import pytest

from openportfolio.alerts import ConsoleNotifier
from openportfolio.application import run_portfolio_review
from openportfolio.cli import main
from openportfolio.persistence import load_portfolio
from openportfolio.providers import FakeMarketDataProvider


EXAMPLE = Path(__file__).parents[1] / "examples" / "demo_portfolio.yaml"


def test_review_demo_produces_one_deterministic_review_alert() -> None:
    configuration = load_portfolio(EXAMPLE)
    output: list[str] = []
    result = run_portfolio_review(
        configuration,
        FakeMarketDataProvider(configuration.fake_prices),
        ConsoleNotifier(output.append),
    )
    assert not result.is_partial
    assert len(result.events) == 1
    assert len(result.alerts) == 1
    assert "OpenPortfolio · REVIEW" in output[0]
    assert "Change: +17.57%" in output[0]


def test_review_is_partial_when_a_quote_is_missing_without_using_zero() -> None:
    configuration = load_portfolio(EXAMPLE)
    provider = FakeMarketDataProvider({"FAKE-USD-STOCK": Decimal("123.45")})
    result = run_portfolio_review(configuration, provider, ConsoleNotifier(lambda _: None))
    assert result.is_partial
    assert "demo-europe-etf" in result.quote_errors
    assert "demo-europe-etf" not in result.quotes
    assert len(result.events) == 1
    assert len(result.alerts) == 1


def test_ntfy_dry_run_does_not_construct_network_notifier(
    monkeypatch: pytest.MonkeyPatch, capsys: object
) -> None:
    def forbidden_notifier(*args: object, **kwargs: object) -> object:
        raise AssertionError("NtfyNotifier no debe construirse en dry-run")

    monkeypatch.setattr("openportfolio.cli.NtfyNotifier", forbidden_notifier)
    result = main(
        [
            "review",
            "--portfolio",
            str(EXAMPLE),
            "--provider",
            "fake",
            "--notifier",
            "ntfy",
            "--dry-run",
        ]
    )
    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert result == 0
    assert "OpenPortfolio · REVIEW" in output
    assert "DRY RUN — no se envió ninguna notificación" in output
