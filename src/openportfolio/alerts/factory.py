from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from openportfolio.domain import Alert, AnalysisEvent, Severity


NOTIFICATION_SEVERITIES = frozenset((Severity.REVIEW, Severity.HIGH))


def _money(value: Decimal) -> str:
    return f"{value:.2f}"


def _percent(value: Decimal, *, signed: bool = False) -> str:
    format_spec = "+.2f" if signed else ".2f"
    return f"{value:{format_spec}}%"


def format_alert_body(event: AnalysisEvent) -> str:
    currency = event.currency or ""
    instrument = event.instrument_name or event.instrument_id or "Instrument"
    symbol = event.provider_symbol or "unknown"
    source = event.source.value if event.source is not None else "unknown"
    return "\n".join(
        (
            f"Instrument: {instrument} ({symbol})",
            f"Current: {_money(event.current_price)} {currency}".rstrip(),
            f"Reference: {_money(event.reference_price)} {currency}".rstrip(),
            f"Change: {_percent(event.change_percent, signed=True)}",
            f"Source: {source}",
            f"Market timestamp: {event.occurred_at.isoformat()}",
            "",
            "Requires review according to the IOS; this is not financial advice.",
        )
    )


def alert_from_event(event: AnalysisEvent) -> Alert | None:
    if event.severity not in NOTIFICATION_SEVERITIES:
        return None
    return Alert.from_event(event, body=format_alert_body(event))


def alerts_from_events(events: Iterable[AnalysisEvent]) -> tuple[Alert, ...]:
    alerts = (alert_from_event(event) for event in events)
    return tuple(alert for alert in alerts if alert is not None)
