"""Replaceable adapters for configuration and generated state."""

from openportfolio.persistence.alert_state import (
    DEFAULT_ALERT_STATE_PATH,
    AlertState,
    AlertStateError,
    AlertStateStore,
    DeliveredAlert,
    JsonAlertStateStore,
)
from openportfolio.persistence.yaml_portfolio import PortfolioConfiguration, load_portfolio
from openportfolio.persistence.portfolio_snapshot import (
    PortfolioSnapshotError,
    atomic_write_text,
    load_portfolio_snapshot,
    save_portfolio_snapshot,
)
from openportfolio.persistence.yaml_market_mapping import (
    MarketMappingError,
    load_market_mapping,
)
from openportfolio.persistence.valuation_report import (
    PartialValuationReportError,
    VALUATION_REPORT_CONTRACT_VERSION,
    ValuationReportError,
    build_valuation_report,
    write_valuation_report,
)

__all__ = [
    "DEFAULT_ALERT_STATE_PATH",
    "AlertState",
    "AlertStateError",
    "AlertStateStore",
    "DeliveredAlert",
    "JsonAlertStateStore",
    "PortfolioConfiguration",
    "load_portfolio",
    "PortfolioSnapshotError",
    "atomic_write_text",
    "load_portfolio_snapshot",
    "save_portfolio_snapshot",
    "MarketMappingError",
    "load_market_mapping",
    "PartialValuationReportError",
    "VALUATION_REPORT_CONTRACT_VERSION",
    "ValuationReportError",
    "build_valuation_report",
    "write_valuation_report",
]
