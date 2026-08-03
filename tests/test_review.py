from decimal import Decimal
from pathlib import Path

import pytest

from openportfolio.alerts import ConsoleNotifier, NotificationDeliveryError
from openportfolio.application import run_portfolio_review
from openportfolio.cli import main
from openportfolio.persistence import JsonAlertStateStore, load_portfolio
from openportfolio.providers import FakeMarketDataProvider


EXAMPLE = Path(__file__).parents[1] / "examples" / "demo_portfolio.yaml"


class FailingNotifier:
    def send(self, alert: object) -> None:
        raise NotificationDeliveryError("fallo ficticio")


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


def test_second_identical_alert_is_suppressed(tmp_path: Path) -> None:
    configuration = load_portfolio(EXAMPLE)
    store = JsonAlertStateStore(tmp_path / "alert_state.json")
    output: list[str] = []

    first = run_portfolio_review(
        configuration,
        FakeMarketDataProvider(configuration.fake_prices),
        ConsoleNotifier(output.append),
        state_store=store,
    )
    second = run_portfolio_review(
        configuration,
        FakeMarketDataProvider(configuration.fake_prices),
        ConsoleNotifier(output.append),
        state_store=store,
    )

    assert first.notifications_sent == 1
    assert not first.suppressed_alerts
    assert second.notifications_sent == 0
    assert len(second.suppressed_alerts) == 1
    assert len(output) == 1


def test_higher_severity_is_delivered_again(tmp_path: Path) -> None:
    configuration = load_portfolio(EXAMPLE)
    store = JsonAlertStateStore(tmp_path / "alert_state.json")
    output: list[str] = []

    review = run_portfolio_review(
        configuration,
        FakeMarketDataProvider(configuration.fake_prices),
        ConsoleNotifier(output.append),
        state_store=store,
    )
    high_prices = dict(configuration.fake_prices)
    high_prices["FAKE-USD-STOCK"] = Decimal("140")
    high = run_portfolio_review(
        configuration,
        FakeMarketDataProvider(high_prices),
        ConsoleNotifier(output.append),
        state_store=store,
    )

    assert review.delivered_alerts[0].severity.value == "REVIEW"
    assert high.delivered_alerts[0].severity.value == "HIGH"
    assert not high.suppressed_alerts
    assert len(output) == 2


def test_return_below_threshold_rearms_a_future_review(tmp_path: Path) -> None:
    configuration = load_portfolio(EXAMPLE)
    store = JsonAlertStateStore(tmp_path / "alert_state.json")
    output: list[str] = []
    notifier = ConsoleNotifier(output.append)

    run_portfolio_review(
        configuration,
        FakeMarketDataProvider(configuration.fake_prices),
        notifier,
        state_store=store,
    )
    below = dict(configuration.fake_prices)
    below["FAKE-USD-STOCK"] = Decimal("110")
    reset = run_portfolio_review(
        configuration,
        FakeMarketDataProvider(below),
        notifier,
        state_store=store,
    )
    crossed_again = run_portfolio_review(
        configuration,
        FakeMarketDataProvider(configuration.fake_prices),
        notifier,
        state_store=store,
    )

    assert not reset.alerts
    assert crossed_again.notifications_sent == 1
    assert len(output) == 2


def test_notification_failure_is_not_recorded(tmp_path: Path) -> None:
    configuration = load_portfolio(EXAMPLE)
    store = JsonAlertStateStore(tmp_path / "alert_state.json")

    failed = run_portfolio_review(
        configuration,
        FakeMarketDataProvider(configuration.fake_prices),
        FailingNotifier(),
        state_store=store,
    )
    successful = run_portfolio_review(
        configuration,
        FakeMarketDataProvider(configuration.fake_prices),
        ConsoleNotifier(lambda _: None),
        state_store=store,
    )

    assert failed.notification_errors == ("fallo ficticio",)
    assert not failed.delivered_alerts
    assert successful.notifications_sent == 1


def test_dry_run_does_not_record_delivery(tmp_path: Path) -> None:
    configuration = load_portfolio(EXAMPLE)
    store = JsonAlertStateStore(tmp_path / "alert_state.json")
    output: list[str] = []

    preview = run_portfolio_review(
        configuration,
        FakeMarketDataProvider(configuration.fake_prices),
        ConsoleNotifier(output.append),
        state_store=store,
        dry_run=True,
    )
    assert not store.path.exists()
    actual = run_portfolio_review(
        configuration,
        FakeMarketDataProvider(configuration.fake_prices),
        ConsoleNotifier(output.append),
        state_store=store,
    )

    assert preview.notifications_sent == 0
    assert actual.notifications_sent == 1
    assert len(output) == 2


def test_review_rule_can_run_without_a_position(tmp_path: Path) -> None:
    path = tmp_path / "watchlist.yaml"
    path.write_text(
        """
portfolio: {id: watchlist, name: Watchlist, base_currency: USD}
instruments:
  - id: asset
    name: Asset
    asset_type: STOCK
    currency: USD
    provider_symbols: {fake: FAKE-ASSET}
positions: []
review_rules:
  - instrument_id: asset
    reference_price: '100'
    review_change_percent: '10'
    high_change_percent: '20'
fake_market_data: {FAKE-ASSET: '115'}
""",
        encoding="utf-8",
    )
    configuration = load_portfolio(path)

    result = run_portfolio_review(
        configuration,
        FakeMarketDataProvider(configuration.fake_prices),
        ConsoleNotifier(lambda _: None),
        dry_run=True,
    )

    assert len(result.quotes) == 1
    assert len(result.alerts) == 1
    assert result.alerts[0].severity.value == "REVIEW"
