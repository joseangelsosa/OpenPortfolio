from datetime import datetime, timezone
from decimal import Decimal

import pytest

from openportfolio.alerts import alert_from_event, alerts_from_events, format_alert_body
from openportfolio.analysis import PriceReferenceChangeRule, PriceReferenceThresholds
from openportfolio.domain import AnalysisEvent, Instrument, MarketQuote, Severity


NOW = datetime(2026, 1, 2, 16, 0, tzinfo=timezone.utc)


def evaluate(
    current: str,
    *,
    reference: str = "100",
    review: str = "10",
    high: str = "20",
) -> AnalysisEvent | None:
    instrument = Instrument("asset", "Demo Equity", "STOCK", "USD", {"fake": "FAKE"})
    quote = MarketQuote(
        "quote", "asset", Decimal(current), "USD", NOW, NOW, "fake", "FAKE"
    )
    return PriceReferenceChangeRule().evaluate(
        portfolio_id="portfolio",
        instrument=instrument,
        quote=quote,
        thresholds=PriceReferenceThresholds(
            Decimal(reference), Decimal(review), Decimal(high)
        ),
    )


@pytest.mark.parametrize(
    ("current", "expected_change"),
    [("115", Decimal("15.00")), ("85", Decimal("-15.00"))],
)
def test_calculates_positive_and_negative_decimal_change(
    current: str, expected_change: Decimal
) -> None:
    event = evaluate(current)
    assert event is not None
    assert event.change_percent == expected_change
    assert ("up" if expected_change > 0 else "down") in event.explanation


def test_review_threshold_produces_review() -> None:
    event = evaluate("110")
    assert event is not None
    assert event.severity is Severity.REVIEW
    assert event.threshold_percent == Decimal("10")


def test_high_threshold_produces_high() -> None:
    event = evaluate("75")
    assert event is not None
    assert event.severity is Severity.HIGH
    assert event.threshold_percent == Decimal("20")


def test_below_threshold_produces_no_event() -> None:
    assert evaluate("109.99") is None


@pytest.mark.parametrize(
    "values",
    [
        ("0", "10", "20"),
        ("100", "0", "20"),
        ("100", "20", "20"),
        ("100", "21", "20"),
    ],
)
def test_threshold_configuration_rejects_incoherent_values(
    values: tuple[str, str, str]
) -> None:
    with pytest.raises(ValueError):
        PriceReferenceThresholds(*(Decimal(value) for value in values))


def test_event_identifier_is_deterministic() -> None:
    first = evaluate("115")
    second = evaluate("115")
    assert first is not None and second is not None
    assert first.id == second.id


def test_review_event_becomes_alert_with_stable_identifier() -> None:
    event = evaluate("115")
    assert event is not None
    first = alert_from_event(event)
    second = alert_from_event(event)
    assert first is not None and second is not None
    assert first.id == second.id
    assert first.event_id == event.id


def test_info_is_filtered_while_review_and_high_are_not() -> None:
    review = evaluate("115")
    high = evaluate("125")
    assert review is not None and high is not None
    info = AnalysisEvent.create(
        portfolio_id=review.portfolio_id,
        rule_code=review.rule_code,
        title=review.title,
        explanation=review.explanation,
        severity=Severity.INFO,
        instrument_id=review.instrument_id or "asset",
        instrument_name=review.instrument_name or "Demo Equity",
        currency=review.currency or "USD",
        current_price=review.current_price,
        reference_price=review.reference_price,
        change_percent=review.change_percent,
        threshold_percent=review.threshold_percent,
        occurred_at=review.occurred_at,
    )
    alerts = alerts_from_events((info, review, high))
    assert [alert.severity for alert in alerts] == [Severity.REVIEW, Severity.HIGH]


def test_mobile_message_contains_factual_evidence_and_disclaimer() -> None:
    event = evaluate("115")
    assert event is not None
    body = format_alert_body(event)
    assert "Demo Equity crossed a configured threshold" in body
    assert "Current: 115.00 USD" in body
    assert "Reference: 100.00 USD" in body
    assert "Change: +15.00%" in body
    assert "Threshold: 10.00%" in body
    assert "Rule: PRICE_REFERENCE_CHANGE" in body
    assert "not a trading instruction" in body
    assert NOW.isoformat() in body
