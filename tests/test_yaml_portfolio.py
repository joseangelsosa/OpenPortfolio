from decimal import Decimal
from pathlib import Path

import pytest

from openportfolio.persistence.yaml_portfolio import (
    PortfolioConfigurationError,
    load_portfolio,
)


EXAMPLE = Path(__file__).parents[1] / "examples" / "demo_portfolio.yaml"


def test_loads_fictitious_yaml_portfolio() -> None:
    configuration = load_portfolio(EXAMPLE)
    portfolio = configuration.portfolio
    assert portfolio.id == "demo-eur"
    assert portfolio.base_currency == "EUR"
    assert {instrument.currency for instrument in portfolio.instruments} == {"EUR", "USD"}
    assert portfolio.instrument("demo-us-stock").symbol_for("yfinance") == "AAPL"
    assert portfolio.positions[0].quantity == Decimal("7.25")
    assert portfolio.positions[0].as_of.utcoffset() is not None
    assert configuration.fake_prices["FAKE-EUR-ETF"] == Decimal("42.40")


def test_yaml_rejects_float_quantity_to_avoid_binary_precision(tmp_path: Path) -> None:
    path = tmp_path / "portfolio.yaml"
    path.write_text(
        """
portfolio: {id: demo, name: Demo, base_currency: EUR}
instruments:
  - id: asset
    name: Asset
    asset_type: ETF
    currency: EUR
    provider_symbols: {fake: FAKE}
positions:
  - id: position
    instrument_id: asset
    quantity: 1.25
    as_of: '2026-01-02T16:00:00+00:00'
fake_market_data: {FAKE: '10.00'}
""",
        encoding="utf-8",
    )
    with pytest.raises(PortfolioConfigurationError, match="decimal escrito como texto"):
        load_portfolio(path)

