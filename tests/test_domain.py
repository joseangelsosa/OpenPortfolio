from datetime import datetime, timezone
from decimal import Decimal

import pytest

from openportfolio.domain import Instrument, MarketQuote, Portfolio, Position


NOW = datetime(2026, 1, 2, 16, 0, tzinfo=timezone.utc)


def instrument(identifier: str = "asset-eur", currency: str = "EUR") -> Instrument:
    return Instrument(
        id=identifier,
        name="Activo ficticio",
        asset_type="ETF",
        currency=currency,
        provider_symbols={"fake": f"FAKE-{identifier}"},
    )


def position(identifier: str = "position-eur", instrument_id: str = "asset-eur") -> Position:
    return Position(
        id=identifier,
        portfolio_id="portfolio-demo",
        instrument_id=instrument_id,
        quantity=Decimal("2.5"),
        as_of=NOW,
    )


def quote(identifier: str = "asset-eur", currency: str = "EUR") -> MarketQuote:
    return MarketQuote(
        id=f"quote-{identifier}",
        instrument_id=identifier,
        price=Decimal("10.20"),
        currency=currency,
        observed_at=NOW,
        retrieved_at=NOW,
        provider="fake",
        provider_symbol=f"FAKE-{identifier}",
    )


@pytest.mark.parametrize("field", ["id", "name", "asset_type"])
def test_instrument_rejects_empty_required_text(field: str) -> None:
    data = {
        "id": "asset-eur",
        "name": "Activo ficticio",
        "asset_type": "ETF",
        "currency": "EUR",
    }
    data[field] = " "
    with pytest.raises(ValueError, match="vacío"):
        Instrument(**data)


@pytest.mark.parametrize("quantity", [Decimal("0"), Decimal("-1"), Decimal("NaN")])
def test_position_rejects_non_positive_or_non_finite_quantity(quantity: Decimal) -> None:
    with pytest.raises(ValueError, match="mayor que cero"):
        Position(
            id="position-eur",
            portfolio_id="portfolio-demo",
            instrument_id="asset-eur",
            quantity=quantity,
            as_of=NOW,
        )


def test_position_requires_decimal_quantity_and_aware_timestamp() -> None:
    with pytest.raises(TypeError, match="Decimal"):
        Position("p", "portfolio-demo", "asset-eur", 2.5, NOW)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="zona horaria"):
        Position(
            "p",
            "portfolio-demo",
            "asset-eur",
            Decimal("2.5"),
            datetime(2026, 1, 2, 16, 0),
        )


def test_portfolio_rejects_unknown_instrument() -> None:
    with pytest.raises(ValueError, match="instrumento desconocido"):
        Portfolio(
            id="portfolio-demo",
            name="Cartera ficticia",
            base_currency="EUR",
            instruments=(instrument(),),
            positions=(position(instrument_id="missing"),),
        )


def test_position_valuation_uses_decimal() -> None:
    result = position().market_value(quote())
    assert result == Decimal("25.500")
    assert isinstance(result, Decimal)


def test_missing_quote_is_explicit_and_not_valued_as_zero() -> None:
    portfolio = Portfolio(
        id="portfolio-demo",
        name="Cartera ficticia",
        base_currency="EUR",
        instruments=(instrument(),),
        positions=(position(),),
    )
    assert portfolio.values_by_position({}) == {"position-eur": None}
    assert portfolio.totals_by_currency({}) == {}


def test_totals_keep_different_currencies_separate() -> None:
    eur_instrument = instrument()
    usd_instrument = instrument("asset-usd", "USD")
    portfolio = Portfolio(
        id="portfolio-demo",
        name="Cartera ficticia",
        base_currency="EUR",
        instruments=(eur_instrument, usd_instrument),
        positions=(position(), position("position-usd", "asset-usd")),
    )
    totals = portfolio.totals_by_currency(
        {
            "asset-eur": quote(),
            "asset-usd": quote("asset-usd", "USD"),
        }
    )
    assert totals == {"EUR": Decimal("25.500"), "USD": Decimal("25.500")}
    assert len(totals) == 2

