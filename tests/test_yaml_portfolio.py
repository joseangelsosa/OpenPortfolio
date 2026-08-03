from decimal import Decimal
from pathlib import Path

import pytest

from openportfolio.persistence.yaml_portfolio import (
    PortfolioConfigurationError,
    load_portfolio,
)
from openportfolio.domain import QuoteSource


EXAMPLE = Path(__file__).parents[1] / "examples" / "demo_portfolio.yaml"
OPERATIONAL = Path(__file__).parents[1] / "examples" / "operational_review.yaml"


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
    assert configuration.fake_quote_sources == {
        "FAKE-EUR-ETF": QuoteSource.DAILY_CLOSE,
        "FAKE-USD-STOCK": QuoteSource.INTRADAY,
    }


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


def test_yaml_without_analysis_thresholds_remains_valid_for_valuation(
    tmp_path: Path,
) -> None:
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
    quantity: '1.25'
    as_of: '2026-01-02T16:00:00+00:00'
fake_market_data: {FAKE: '10.00'}
""",
        encoding="utf-8",
    )
    configuration = load_portfolio(path)
    assert configuration.price_reference_rules == {}


def test_operational_review_has_no_positions_or_enabled_investment_rules() -> None:
    configuration = load_portfolio(OPERATIONAL)
    portfolio = configuration.portfolio

    assert portfolio.positions == ()
    assert configuration.price_reference_rules == {}
    assert configuration.fake_prices == {}
    assert configuration.fake_quote_sources == {}
    assert {instrument.id for instrument in portfolio.instruments} == {
        "sp500-etf",
        "nvidia",
        "alphabet",
        "microsoft",
        "nestle",
    }
    assert portfolio.instrument("nvidia").symbol_for("yfinance") == "NVDA"
    assert portfolio.instrument("sp500-etf").symbol_for("yfinance") == "H4ZF.DE"
    assert portfolio.instrument("alphabet").symbol_for("yfinance") == "GOOGL"
    assert portfolio.instrument("microsoft").symbol_for("yfinance") == "MSFT"
    assert portfolio.instrument("nestle").symbol_for("yfinance") == "NESR.DE"
    assert all(instrument.active for instrument in portfolio.instruments)
    assert portfolio.instrument("sp500-etf").currency == "EUR"
    assert portfolio.instrument("nestle").currency == "EUR"
