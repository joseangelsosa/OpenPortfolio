from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from openportfolio.alerts import ConsoleNotifier, NotificationDeliveryError
from openportfolio.application import run_portfolio_review
from openportfolio.cli import main
from openportfolio.domain import Alert, MarketQuote, OperationalNotification, QuoteSource
from openportfolio.market_data import ProviderResponseError
from openportfolio.persistence import (
    AlertState,
    AlertStateError,
    JsonAlertStateStore,
    load_portfolio,
)
from openportfolio.providers import FakeMarketDataProvider


EXAMPLE = Path(__file__).parents[1] / "examples" / "demo_portfolio.yaml"
OPERATIONAL_EXAMPLE = Path(__file__).parents[1] / "examples" / "operational_review.yaml"


class FailingNotifier:
    def send(self, alert: object) -> None:
        raise NotificationDeliveryError("fallo ficticio")


class CapturingNotifier:
    def __init__(self) -> None:
        self.sent: list[Alert | OperationalNotification] = []

    def send(self, notification: Alert | OperationalNotification) -> None:
        self.sent.append(notification)


class OperationalFakeProvider:
    name = "operational-fake"

    def __init__(
        self,
        prices: dict[str, Decimal],
        *,
        failing_instrument: str | None = None,
    ) -> None:
        self.prices = prices
        self.failing_instrument = failing_instrument

    def get_quote(self, instrument: object) -> MarketQuote:
        instrument_id = instrument.id  # type: ignore[attr-defined]
        if instrument_id == self.failing_instrument:
            raise ProviderResponseError("fallo ficticio de cotización")
        observed_at = datetime(2026, 1, 2, 16, 0, tzinfo=timezone.utc)
        return MarketQuote(
            id=f"quote-{instrument_id}",
            instrument_id=instrument_id,
            price=self.prices[instrument_id],
            currency=instrument.currency,  # type: ignore[attr-defined]
            observed_at=observed_at,
            retrieved_at=observed_at,
            provider=self.name,
            provider_symbol=instrument_id,
            source=QuoteSource.INTRADAY,
        )


def _operational_prices(**overrides: str) -> dict[str, Decimal]:
    prices = {
        "sp500-etf": Decimal("66.42"),
        "nvidia": Decimal("204.38"),
        "alphabet": Decimal("368.28"),
        "microsoft": Decimal("486.36"),
        "nestle": Decimal("87.09"),
    }
    prices.update({key: Decimal(value) for key, value in overrides.items()})
    return prices


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


def test_operational_review_without_alerts_sends_one_outcome_notification() -> None:
    configuration = load_portfolio(OPERATIONAL_EXAMPLE)
    notifier = CapturingNotifier()

    result = run_portfolio_review(
        configuration,
        OperationalFakeProvider(_operational_prices()),
        notifier,
        send_operational_notification=True,
    )

    assert result.operational_notification_sent
    assert not result.alerts
    assert len(notifier.sent) == 1
    notification = notifier.sent[0]
    assert isinstance(notification, OperationalNotification)
    assert "5 instrumentos" in notification.body
    assert "no se han detectado cambios relevantes" in notification.body


@pytest.mark.parametrize("new_alerts", [1, 2])
def test_new_alerts_do_not_add_generic_operational_notification(new_alerts: int) -> None:
    configuration = load_portfolio(OPERATIONAL_EXAMPLE)
    prices = _operational_prices(nvidia="216")
    if new_alerts == 2:
        prices["alphabet"] = Decimal("390")
    notifier = CapturingNotifier()

    result = run_portfolio_review(
        configuration,
        OperationalFakeProvider(prices),
        notifier,
        send_operational_notification=True,
    )

    assert len(result.delivered_alerts) == new_alerts
    assert not result.operational_notification_sent
    assert len(notifier.sent) == new_alerts
    assert all(isinstance(notification, Alert) for notification in notifier.sent)


def test_only_deduplicated_alert_sends_one_accurate_summary(tmp_path: Path) -> None:
    configuration = load_portfolio(OPERATIONAL_EXAMPLE)
    prices = _operational_prices(nvidia="216")
    store = JsonAlertStateStore(tmp_path / "alert_state.json")
    run_portfolio_review(
        configuration,
        OperationalFakeProvider(prices),
        CapturingNotifier(),
        state_store=store,
    )
    notifier = CapturingNotifier()

    result = run_portfolio_review(
        configuration,
        OperationalFakeProvider(prices),
        notifier,
        state_store=store,
        send_operational_notification=True,
    )

    assert len(result.suppressed_alerts) == 1
    assert result.operational_notification_sent
    assert len(notifier.sent) == 1
    notification = notifier.sent[0]
    assert isinstance(notification, OperationalNotification)
    assert "Sin alertas nuevas" in notification.body
    assert "1 movimiento relevante ya había sido notificado" in notification.body
    assert "no se han detectado cambios relevantes" not in notification.body


def test_new_and_deduplicated_alert_sends_only_new_alert(tmp_path: Path) -> None:
    configuration = load_portfolio(OPERATIONAL_EXAMPLE)
    store = JsonAlertStateStore(tmp_path / "alert_state.json")
    run_portfolio_review(
        configuration,
        OperationalFakeProvider(_operational_prices(nvidia="216")),
        CapturingNotifier(),
        state_store=store,
    )
    notifier = CapturingNotifier()

    result = run_portfolio_review(
        configuration,
        OperationalFakeProvider(_operational_prices(nvidia="216", alphabet="390")),
        notifier,
        state_store=store,
        send_operational_notification=True,
    )

    assert len(result.delivered_alerts) == 1
    assert len(result.suppressed_alerts) == 1
    assert not result.operational_notification_sent
    assert len(notifier.sent) == 1
    assert isinstance(notifier.sent[0], Alert)


def test_operational_notification_does_not_change_alert_state(tmp_path: Path) -> None:
    configuration = load_portfolio(OPERATIONAL_EXAMPLE)
    store = JsonAlertStateStore(tmp_path / "alert_state.json")
    notifier = CapturingNotifier()

    first = run_portfolio_review(
        configuration,
        OperationalFakeProvider(_operational_prices()),
        notifier,
        state_store=store,
        send_operational_notification=True,
    )
    state_after_first = store.load()
    second = run_portfolio_review(
        configuration,
        OperationalFakeProvider(_operational_prices()),
        notifier,
        state_store=store,
        send_operational_notification=True,
    )

    assert first.operational_notification_sent
    assert second.operational_notification_sent
    assert store.load() == state_after_first
    assert not state_after_first.delivered_alerts


def test_partial_review_does_not_send_success_notification() -> None:
    configuration = load_portfolio(OPERATIONAL_EXAMPLE)
    notifier = CapturingNotifier()

    result = run_portfolio_review(
        configuration,
        OperationalFakeProvider(
            _operational_prices(), failing_instrument="nvidia"
        ),
        notifier,
        send_operational_notification=True,
    )

    assert result.is_partial
    assert not result.operational_notification_sent
    assert not notifier.sent


def test_operational_notification_failure_is_visible() -> None:
    configuration = load_portfolio(OPERATIONAL_EXAMPLE)

    result = run_portfolio_review(
        configuration,
        OperationalFakeProvider(_operational_prices()),
        FailingNotifier(),
        send_operational_notification=True,
    )

    assert not result.operational_notification_sent
    assert result.notification_errors == ("fallo ficticio",)


def test_state_persistence_failure_prevents_success_notification() -> None:
    configuration = load_portfolio(OPERATIONAL_EXAMPLE)
    notifier = CapturingNotifier()

    class FailingStateStore:
        def load(self) -> AlertState:
            return AlertState.empty()

        def save(self, state: AlertState) -> None:
            raise AlertStateError("fallo ficticio de persistencia")

    with pytest.raises(AlertStateError, match="persistencia"):
        run_portfolio_review(
            configuration,
            OperationalFakeProvider(_operational_prices()),
            notifier,
            state_store=FailingStateStore(),
            send_operational_notification=True,
        )

    assert not notifier.sent
