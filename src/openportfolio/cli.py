from __future__ import annotations

import argparse
from decimal import Decimal
from pathlib import Path
import sys
from typing import Mapping, Sequence

from openportfolio.alerts import ConsoleNotifier, NotificationConfigurationError, NtfyNotifier
from openportfolio.application import run_portfolio_review
from openportfolio.domain import MarketQuote, Portfolio
from openportfolio.market_data import MarketDataError, MarketDataProvider
from openportfolio.persistence import (
    DEFAULT_ALERT_STATE_PATH,
    AlertStateError,
    JsonAlertStateStore,
    load_portfolio,
)
from openportfolio.persistence.yaml_portfolio import PortfolioConfigurationError
from openportfolio.providers import FakeMarketDataProvider


DEFAULT_PORTFOLIO = Path(__file__).resolve().parents[2] / "examples" / "demo_portfolio.yaml"


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(argv) if argv is not None else sys.argv[1:]
    if arguments and arguments[0] == "review":
        return _review_main(arguments[1:])
    return _valuation_main(arguments)


def _valuation_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Valora una cartera ficticia por moneda original",
        epilog="Para analizar umbrales y notificar: openportfolio review --help",
    )
    parser.add_argument(
        "--portfolio",
        type=Path,
        default=DEFAULT_PORTFOLIO,
        help="ruta al YAML de cartera (por defecto: examples/demo_portfolio.yaml)",
    )
    parser.add_argument(
        "--provider",
        choices=("fake", "yfinance"),
        default="fake",
        help="proveedor de cotizaciones; fake no usa red",
    )
    args = parser.parse_args(argv)

    try:
        configuration = load_portfolio(args.portfolio)
        provider = _provider(args.provider, configuration.fake_prices)
    except (PortfolioConfigurationError, ImportError) as error:
        parser.error(str(error))

    quotes: dict[str, MarketQuote] = {}
    errors: dict[str, str] = {}
    for instrument in configuration.portfolio.instruments:
        if not instrument.active:
            continue
        try:
            quotes[instrument.id] = provider.get_quote(instrument)
        except MarketDataError as error:
            errors[instrument.id] = str(error)

    _print_report(configuration.portfolio, quotes, errors)
    return 1 if errors else 0


def _review_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="openportfolio review",
        description="Ejecuta reglas deterministas y entrega alertas de revisión",
    )
    parser.add_argument(
        "--portfolio",
        type=Path,
        default=DEFAULT_PORTFOLIO,
        help="ruta al YAML de cartera (por defecto: examples/demo_portfolio.yaml)",
    )
    parser.add_argument(
        "--provider",
        choices=("fake", "yfinance"),
        default="fake",
        help="proveedor de cotizaciones; fake no usa red",
    )
    parser.add_argument(
        "--notifier",
        choices=("console", "ntfy"),
        default="console",
        help="canal de entrega; console es completamente offline",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="construye y muestra alertas sin contactar el canal seleccionado",
    )
    parser.add_argument(
        "--state-path",
        type=Path,
        default=DEFAULT_ALERT_STATE_PATH,
        help="estado JSON de entregas (por defecto: state/alert_state.json)",
    )
    args = parser.parse_args(argv)

    if args.provider == "yfinance" and not args.dry_run:
        print(
            "Error de configuración: yfinance requiere --dry-run en este incremento",
            file=sys.stderr,
        )
        return 2

    try:
        configuration = load_portfolio(args.portfolio)
        provider = _provider(args.provider, configuration.fake_prices)
        if args.dry_run or args.notifier == "console":
            notifier = ConsoleNotifier()
        else:
            notifier = NtfyNotifier()
    except (PortfolioConfigurationError, ImportError, NotificationConfigurationError) as error:
        print(f"Error de configuración: {error}", file=sys.stderr)
        return 2

    try:
        result = run_portfolio_review(
            configuration,
            provider,
            notifier,
            state_store=JsonAlertStateStore(args.state_path),
            dry_run=args.dry_run,
        )
    except AlertStateError as error:
        print(f"Error de estado de alertas: {error}", file=sys.stderr)
        return 2
    if args.dry_run:
        print("DRY RUN — no se envió ninguna notificación.")
    print(
        f"Revisión completada: {len(result.quotes)} cotizaciones, "
        f"{len(result.events)} eventos, {len(result.alerts)} alertas generadas, "
        f"{result.notifications_sent} enviadas, "
        f"{len(result.suppressed_alerts)} suprimidas por duplicadas."
    )
    for instrument_id, error in result.quote_errors.items():
        print(f"Cotización ausente para {instrument_id}: {error}", file=sys.stderr)
    for error in result.notification_errors:
        print(f"Error de notificación: {error}", file=sys.stderr)

    if result.notification_errors:
        return 3
    if result.is_partial:
        return 1
    return 0


def _provider(name: str, fake_prices: Mapping[str, Decimal]) -> MarketDataProvider:
    if name == "fake":
        return FakeMarketDataProvider(fake_prices)
    from openportfolio.providers.yfinance import YFinanceMarketDataProvider

    return YFinanceMarketDataProvider()


def _print_report(
    portfolio: Portfolio,
    quotes: dict[str, MarketQuote],
    errors: dict[str, str],
) -> None:
    values = portfolio.values_by_position(quotes)
    print(f"OpenPortfolio — {portfolio.name}")
    print(f"Moneda base declarada: {portfolio.base_currency}")
    print()
    header = (
        f"{'Instrumento':<24} {'Cantidad':>10} {'Precio':>14} "
        f"{'Valor':>16} {'Observada':<25} {'Proveedor':<12}"
    )
    print(header)
    print("-" * len(header))
    for position in portfolio.positions:
        instrument = portfolio.instrument(position.instrument_id)
        quote = quotes.get(instrument.id)
        if quote is None:
            detail = errors.get(instrument.id, "cotización ausente")
            print(f"{instrument.name:<24} {str(position.quantity):>10} {'—':>14} {'NO DISPONIBLE':>16}")
            print(f"  Error: {detail}")
            continue
        value = values[position.id]
        assert value is not None
        price_text = f"{_number(quote.price)} {quote.currency}"
        value_text = f"{_number(value)} {quote.currency}"
        print(
            f"{instrument.name:<24} {str(position.quantity):>10} {price_text:>14} "
            f"{value_text:>16} {quote.observed_at.isoformat():<25} {quote.provider:<12}"
        )
        print(f"  Símbolo proveedor: {quote.provider_symbol}; obtenida: {quote.retrieved_at.isoformat()}")

    totals = portfolio.totals_by_currency(quotes)
    print("\nTotales parciales por moneda:")
    for currency, total in sorted(totals.items()):
        print(f"  {currency}: {_number(total)} {currency}")
    if len(totals) > 1:
        print(
            "No se muestra un total global: la conversión de divisas no está implementada "
            "y no es correcto sumar monedas diferentes."
        )
    if errors:
        print("La valoración es parcial porque faltan una o más cotizaciones.")


def _number(value: Decimal) -> str:
    return f"{value:,.2f}"


if __name__ == "__main__":
    raise SystemExit(main())
