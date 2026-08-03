from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from urllib.error import HTTPError, URLError

import pytest

from openportfolio.alerts import (
    ConsoleNotifier,
    NotificationConfigurationError,
    NotificationDeliveryError,
    NtfyNotifier,
    alert_from_event,
)
from openportfolio.domain import AnalysisEvent, Alert, OperationalNotification, Severity


SECRET_TOPIC = "test-topic-placeholder-7f8e9d"
NOW = datetime(2026, 1, 2, 16, 0, tzinfo=timezone.utc)


class Response:
    def __init__(self, status: int = 200) -> None:
        self.status = status

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def alert(severity: Severity = Severity.REVIEW) -> Alert:
    event = AnalysisEvent.create(
        portfolio_id="portfolio",
        rule_code="PRICE_REFERENCE_CHANGE",
        title="Demo Equity crossed a configured threshold",
        explanation="Factual threshold crossing.",
        severity=severity,
        instrument_id="asset",
        instrument_name="Demo Equity",
        currency="USD",
        current_price=Decimal("115"),
        reference_price=Decimal("100"),
        change_percent=Decimal("15"),
        threshold_percent=Decimal("10"),
        occurred_at=NOW,
    )
    result = alert_from_event(event)
    assert result is not None
    return result


def test_console_notifier_prints_same_essential_content() -> None:
    output: list[str] = []
    ConsoleNotifier(output.append).send(alert())
    assert output == [f"{alert().title}\n{alert().body}"]


def test_ntfy_builds_request_with_configurable_server_topic_and_timeout() -> None:
    calls: list[tuple[object, float]] = []

    def http_open(request: object, *, timeout: float) -> Response:
        calls.append((request, timeout))
        return Response()

    NtfyNotifier(
        server="https://push.example.test/base",
        topic=SECRET_TOPIC,
        timeout=4.5,
        http_open=http_open,
    ).send(alert())

    request, timeout = calls[0]
    assert request.full_url == f"https://push.example.test/base/{SECRET_TOPIC}"  # type: ignore[attr-defined]
    assert request.data == alert().body.encode("utf-8")  # type: ignore[attr-defined]
    assert request.headers["Title"] == "OpenPortfolio · REVIEW"  # type: ignore[attr-defined]
    assert request.headers["Priority"] == "3"  # type: ignore[attr-defined]
    assert timeout == 4.5


def test_high_maps_to_max_priority() -> None:
    requests: list[object] = []

    def http_open(request: object, *, timeout: float) -> Response:
        requests.append(request)
        return Response()

    NtfyNotifier(topic=SECRET_TOPIC, http_open=http_open).send(alert(Severity.HIGH))
    assert requests[0].headers["Priority"] == "5"  # type: ignore[attr-defined]


def test_operational_notification_uses_normal_priority() -> None:
    requests: list[object] = []

    def http_open(request: object, *, timeout: float) -> Response:
        requests.append(request)
        return Response()

    notification = OperationalNotification(
        title="OpenPortfolio · RESULTADO OPERATIVO",
        body="Revisión completada.",
    )
    NtfyNotifier(topic=SECRET_TOPIC, http_open=http_open).send(notification)

    assert requests[0].headers["Priority"] == "3"  # type: ignore[attr-defined]


def test_ntfy_rejects_missing_topic_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENPORTFOLIO_NTFY_TOPIC", raising=False)
    called = False

    def http_open(*args: object, **kwargs: object) -> Response:
        nonlocal called
        called = True
        return Response()

    with pytest.raises(NotificationConfigurationError, match="OPENPORTFOLIO_NTFY_TOPIC"):
        NtfyNotifier(http_open=http_open)
    assert not called


@pytest.mark.parametrize("status", [400, 503])
def test_ntfy_treats_non_success_status_as_error(status: int) -> None:
    def http_open(*args: object, **kwargs: object) -> Response:
        return Response(status)

    with pytest.raises(NotificationDeliveryError, match=f"HTTP {status}"):
        NtfyNotifier(topic=SECRET_TOPIC, http_open=http_open).send(alert())


def test_ntfy_sanitizes_http_error() -> None:
    def http_open(*args: object, **kwargs: object) -> Response:
        raise HTTPError(f"https://ntfy.sh/{SECRET_TOPIC}", 401, "unauthorized", {}, None)

    with pytest.raises(NotificationDeliveryError) as captured:
        NtfyNotifier(topic=SECRET_TOPIC, http_open=http_open).send(alert())
    assert "HTTP 401" in str(captured.value)
    assert SECRET_TOPIC not in str(captured.value)


def test_ntfy_sanitizes_connection_error() -> None:
    def http_open(*args: object, **kwargs: object) -> Response:
        raise URLError(f"connection failed for {SECRET_TOPIC}")

    with pytest.raises(NotificationDeliveryError) as captured:
        NtfyNotifier(topic=SECRET_TOPIC, http_open=http_open).send(alert())
    assert "conectar" in str(captured.value)
    assert SECRET_TOPIC not in str(captured.value)
