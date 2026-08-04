from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys
from typing import Mapping, Sequence

from openportfolio.alerts import (
    ConsoleNotifier,
    NotificationConfigurationError,
    NotificationDeliveryError,
    NtfyNotifier,
)
from openportfolio.application import (
    combine_revolut_imports,
    QuoteCheckItem,
    check_portfolio_quotes,
    reconciliation_report,
    run_portfolio_review,
)
from openportfolio.domain import Alert, MarketQuote, Portfolio, QuoteSource, Severity
from openportfolio.market_data import MarketDataError, MarketDataProvider
from openportfolio.persistence import (
    DEFAULT_ALERT_STATE_PATH,
    AlertStateError,
    JsonAlertStateStore,
    PortfolioSnapshotError,
    atomic_write_text,
    load_portfolio,
    load_portfolio_snapshot,
    save_portfolio_snapshot,
)
from openportfolio.persistence.yaml_portfolio import PortfolioConfigurationError
from openportfolio.providers import FakeMarketDataProvider


DEFAULT_PORTFOLIO = Path(__file__).resolve().parents[2] / "examples" / "demo_portfolio.yaml"


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(argv) if argv is not None else sys.argv[1:]
    if arguments and arguments[0] == "import-revolut":
        return _import_revolut_main(arguments[1:])
    if arguments and arguments[0] == "review":
        return _review_main(arguments[1:])
    if arguments and arguments[0] == "send-test-notification":
        return _test_notification_main(arguments[1:])
    return _valuation_main(arguments)


def _import_revolut_main(argv: Sequence[str]) -> int:
    from openportfolio.domain import ImportSource
    from openportfolio.importers import (
        detect_revolut_format,
        import_investments,
        import_xau_statement,
    )

    parser = argparse.ArgumentParser(
        prog="openportfolio import-revolut",
        description="Importa exportaciones CSV de Revolut sin red ni activación de alertas",
    )
    parser.add_argument(
        "--input",
        type=Path,
        action="append",
        default=[],
        help="CSV cuyo formato se detectará por la cabecera; se puede repetir",
    )
    parser.add_argument("--investments", type=Path, help="historial CSV de inversiones")
    parser.add_argument(
        "--account-statement", type=Path, help="extracto CSV para la posición XAU"
    )
    parser.add_argument(
        "--existing-snapshot",
        type=Path,
        help="snapshot anterior; si se omite y --output existe, se usa --output",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".openportfolio/private/portfolio.yaml"),
        help="snapshot privado de salida",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path(".openportfolio/private/reconciliation.txt"),
        help="informe privado de conciliación",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="valida y resume sin escribir snapshot ni informe",
    )
    args = parser.parse_args(argv)

    candidates: list[tuple[ImportSource, Path]] = []
    if args.investments is not None:
        candidates.append((ImportSource.INVESTMENTS, args.investments))
    if args.account_statement is not None:
        candidates.append((ImportSource.XAU_STATEMENT, args.account_statement))
    for input_path in args.input:
        try:
            candidates.append((detect_revolut_format(input_path), input_path))
        except ValueError as error:
            print(f"Error de importación: {error}", file=sys.stderr)
            return 2
    if not candidates:
        print("Error de importación: debe indicarse al menos un CSV de entrada.", file=sys.stderr)
        return 2
    sources = [source for source, _ in candidates]
    if len(sources) != len(set(sources)):
        print(
            "Error de importación: se recibió más de un archivo para la misma fuente.",
            file=sys.stderr,
        )
        return 2

    existing_path = args.existing_snapshot
    if existing_path is None and args.output.exists():
        existing_path = args.output
    try:
        existing = (
            load_portfolio_snapshot(existing_path) if existing_path is not None else None
        )
    except PortfolioSnapshotError as error:
        print(f"Error de snapshot: {error}", file=sys.stderr)
        return 2

    importers = {
        ImportSource.INVESTMENTS: import_investments,
        ImportSource.XAU_STATEMENT: import_xau_statement,
    }
    results = tuple(importers[source](path) for source, path in candidates)
    now = datetime.now(timezone.utc)
    outcome = combine_revolut_imports(results, existing, generated_at=now)
    report = reconciliation_report(outcome, generated_at=now)

    if not args.dry_run:
        try:
            if outcome.ok:
                assert outcome.snapshot is not None
                save_portfolio_snapshot(outcome.snapshot, args.output)
            atomic_write_text(args.report, report)
        except PortfolioSnapshotError as error:
            print(f"Error de persistencia: {error}", file=sys.stderr)
            return 2

    active_equities = (
        sum(
            1
            for position in outcome.snapshot.positions
            if position.source is ImportSource.INVESTMENTS and position.active_monitoring
        )
        if outcome.snapshot is not None
        else 0
    )
    xau_count = (
        sum(
            1
            for position in outcome.snapshot.positions
            if position.source is ImportSource.XAU_STATEMENT
        )
        if outcome.snapshot is not None
        else 0
    )
    historical_count = (
        sum(1 for position in outcome.snapshot.positions if not position.active_monitoring)
        if outcome.snapshot is not None
        else 0
    )
    unresolved = (
        sum(
            1
            for position in outcome.snapshot.positions
            if position.source is ImportSource.INVESTMENTS
            and position.active_monitoring
            and position.market_symbol is None
        )
        if outcome.snapshot is not None
        else 0
    )
    mode = "DRY RUN; sin escritura. " if args.dry_run else ""
    print(
        f"{mode}Importación Revolut: {sum(result.rows_read for result in results)} filas, "
        f"{active_equities} acciones/ETF activas, {xau_count} XAU, "
        f"{historical_count} históricas no operativas, {unresolved} tickers activos sin resolver, "
        f"{len(outcome.warnings)} advertencias, {len(outcome.errors)} errores."
    )
    for issue in outcome.errors:
        print(f"Error: {issue.render()}", file=sys.stderr)
    if outcome.errors:
        if not args.dry_run:
            print("Snapshot no modificado; se escribió únicamente el informe saneado.")
        return 1
    if not args.dry_run:
        print("Snapshot e informe privados escritos de forma atómica.")
    return 0


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
    parser.add_argument(
        "--check-quotes",
        action="store_true",
        help="comprueba cotizaciones sin reglas, alertas, estado ni notificaciones",
    )
    args = parser.parse_args(argv)

    try:
        configuration = load_portfolio(args.portfolio)
        provider = _provider(
            args.provider,
            configuration.fake_prices,
            configuration.fake_quote_sources,
        )
    except (PortfolioConfigurationError, ImportError) as error:
        parser.error(str(error))

    if args.check_quotes:
        result = check_portfolio_quotes(configuration.portfolio, provider)
        _print_quote_check(result.items)
        return 0 if result.ok else 1

    quotes: dict[str, MarketQuote] = {}
    errors: dict[str, str] = {}
    for instrument in configuration.portfolio.instruments:
        if not instrument.active:
            continue
        try:
            quote = provider.get_quote(instrument)
        except MarketDataError as error:
            errors[instrument.id] = str(error)
            continue
        if quote.currency != instrument.currency:
            errors[instrument.id] = (
                f"moneda recibida {quote.currency}; se esperaba {instrument.currency}"
            )
            continue
        quotes[instrument.id] = quote

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
    parser.add_argument(
        "--operational-notification",
        action="store_true",
        help="envía confirmación ntfy del resultado cuando no hay alertas nuevas",
    )
    args = parser.parse_args(argv)

    if args.operational_notification and (
        args.provider != "yfinance" or args.notifier != "ntfy" or args.dry_run
    ):
        print(
            "Error de configuración: la notificación operativa solo corresponde a "
            "revisiones reales con yfinance y ntfy.",
            file=sys.stderr,
        )
        return 2

    try:
        configuration = load_portfolio(args.portfolio)
        provider = _provider(
            args.provider,
            configuration.fake_prices,
            configuration.fake_quote_sources,
        )
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
            send_operational_notification=args.operational_notification,
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
        f"{len(result.suppressed_alerts)} suprimidas por duplicadas, "
        f"{int(result.operational_notification_sent)} notificación operativa enviada."
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


def _test_notification_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="openportfolio send-test-notification",
        description="Envía una única notificación de prueba sin datos ni estado reales",
    )
    parser.parse_args(argv)
    try:
        notifier = NtfyNotifier()
        notifier.send(
            Alert(
                id="test-notification",
                event_id="test-notification-event",
                severity=Severity.INFO,
                title="PRUEBA OpenPortfolio",
                body=(
                    "PRUEBA OpenPortfolio — canal de notificaciones configurado "
                    "correctamente. No corresponde a una revisión real de mercado."
                ),
                created_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
            )
        )
    except NotificationConfigurationError as error:
        print(f"Error de configuración: {error}", file=sys.stderr)
        return 2
    except NotificationDeliveryError as error:
        print(f"Error de notificación: {error}", file=sys.stderr)
        return 3
    print("Notificación de PRUEBA enviada.")
    return 0


def _provider(
    name: str,
    fake_prices: Mapping[str, Decimal],
    fake_sources: Mapping[str, QuoteSource],
) -> MarketDataProvider:
    if name == "fake":
        return FakeMarketDataProvider(fake_prices, fake_sources)
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


def _print_quote_check(items: Sequence[QuoteCheckItem]) -> None:
    print("OpenPortfolio — comprobación de cotizaciones")
    print("Sin reglas, alertas, estado ni notificaciones.")
    print()
    failed_symbols: list[str] = []
    for item in items:
        instrument = item.instrument
        symbol = item.requested_symbol or "—"
        quote = item.quote
        error = item.error
        if quote is None:
            price = currency = timestamp = source = "—"
        else:
            price = _number(quote.price)
            currency = quote.currency
            timestamp = quote.observed_at.isoformat()
            source = quote.source.value
        result = "OK" if error is None else f"ERROR — {error}"
        print(
            f"{instrument.name} | símbolo: {symbol} | precio: {price} | "
            f"moneda: {currency} | timestamp: {timestamp} | source: {source} | "
            f"resultado: {result}"
        )
        if error is not None:
            failed_symbols.append(symbol if symbol != "—" else instrument.id)
    print()
    if failed_symbols:
        print(f"Comprobación fallida. Símbolos fallidos: {', '.join(failed_symbols)}")
    else:
        print(f"Comprobación correcta: {len(items)} cotizaciones validadas.")


if __name__ == "__main__":
    raise SystemExit(main())
