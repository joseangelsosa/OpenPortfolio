from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
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
    QuoteCheckItem,
    RevolutDiscoveryError,
    RevolutExportSelection,
    RevolutImportError,
    check_portfolio_quotes,
    discover_revolut_exports,
    import_revolut_exports,
    reconciliation_report,
    render_market_mapping_validation_text,
    render_portfolio_valuation_text,
    render_portfolio_summary_text,
    run_portfolio_review,
    summarize_portfolio,
    validate_market_mapping,
    value_portfolio,
)
from openportfolio.domain import Alert, MarketQuote, Portfolio, QuoteSource, Severity
from openportfolio.market_data import MarketDataError, MarketDataProvider
from openportfolio.persistence import (
    DEFAULT_ALERT_STATE_PATH,
    AlertStateError,
    JsonAlertStateStore,
    MarketMappingError,
    PortfolioSnapshotError,
    atomic_write_text,
    load_portfolio,
    load_market_mapping,
    load_portfolio_snapshot,
    save_portfolio_snapshot,
)
from openportfolio.persistence.yaml_portfolio import PortfolioConfigurationError
from openportfolio.providers import FakeMarketDataProvider


DEFAULT_PORTFOLIO = Path(__file__).resolve().parents[2] / "examples" / "demo_portfolio.yaml"


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(argv) if argv is not None else sys.argv[1:]
    if arguments and arguments[0] == "portfolio-summary":
        return _portfolio_summary_main(arguments[1:])
    if arguments and arguments[0] == "validate-market-mapping":
        return _validate_market_mapping_main(arguments[1:])
    if arguments and arguments[0] == "value-portfolio":
        return _value_portfolio_main(arguments[1:])
    if arguments and arguments[0] == "import-revolut-latest":
        return _import_revolut_latest_main(arguments[1:])
    if arguments and arguments[0] == "import-revolut":
        return _import_revolut_main(arguments[1:])
    if arguments and arguments[0] == "review":
        return _review_main(arguments[1:])
    if arguments and arguments[0] == "send-test-notification":
        return _test_notification_main(arguments[1:])
    return _valuation_main(arguments)


def _portfolio_summary_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="openportfolio portfolio-summary",
        description="Resume un snapshot importado sin cotizaciones ni reglas del IOS",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        required=True,
        help="ruta al snapshot de cartera importado",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="formato de salida (por defecto: text)",
    )
    args = parser.parse_args(argv)

    if not _is_readable_file(args.snapshot):
        print(
            "Error de snapshot: el archivo no existe o no es legible.",
            file=sys.stderr,
        )
        return 2
    try:
        summary = summarize_portfolio(load_portfolio_snapshot(args.snapshot))
    except PortfolioSnapshotError:
        print(
            "Error de snapshot: el archivo no tiene un formato compatible.",
            file=sys.stderr,
        )
        return 1

    if args.format == "json":
        print(json.dumps(summary.as_dict(), ensure_ascii=False, sort_keys=True))
    else:
        print(render_portfolio_summary_text(summary), end="")
    return 0


def _validate_market_mapping_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="openportfolio validate-market-mapping",
        description=(
            "Valida offline la correspondencia entre un snapshot y símbolos de mercado"
        ),
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        required=True,
        help="ruta al snapshot de cartera importado",
    )
    parser.add_argument(
        "--mapping",
        type=Path,
        required=True,
        help="ruta al YAML privado de correspondencias",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="formato de salida (por defecto: text)",
    )
    args = parser.parse_args(argv)

    if not _is_readable_file(args.snapshot):
        _print_market_mapping_error(
            args.format,
            "snapshot_not_readable",
            "Error de snapshot: el archivo no existe o no es legible.",
        )
        return 2
    if not _is_readable_file(args.mapping):
        _print_market_mapping_error(
            args.format,
            "mapping_not_readable",
            "Error de mapping: el archivo no existe o no es legible.",
        )
        return 2
    try:
        snapshot = load_portfolio_snapshot(args.snapshot)
    except PortfolioSnapshotError:
        _print_market_mapping_error(
            args.format,
            "invalid_snapshot",
            "Error de snapshot: el archivo no tiene un formato compatible.",
        )
        return 1
    try:
        mapping = load_market_mapping(args.mapping)
    except MarketMappingError:
        _print_market_mapping_error(
            args.format,
            "invalid_mapping",
            "Error de mapping: el archivo no tiene un formato compatible.",
        )
        return 1

    validation = validate_market_mapping(snapshot, mapping)
    if args.format == "json":
        print(json.dumps(validation.as_dict(), ensure_ascii=False, sort_keys=True))
    else:
        print(render_market_mapping_validation_text(validation), end="")
    return 0 if validation.ready_for_market_valuation else 1


def _print_market_mapping_error(output_format: str, code: str, message: str) -> None:
    if output_format == "json":
        payload = {
            "validation_schema_version": 1,
            "error": {"code": code, "message": message},
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stderr)
    else:
        print(message, file=sys.stderr)


def _value_portfolio_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="openportfolio value-portfolio",
        description="Valora un snapshot importado en sus monedas originales",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        required=True,
        help="ruta al snapshot de cartera importado",
    )
    parser.add_argument(
        "--mapping",
        type=Path,
        required=True,
        help="ruta al YAML privado de correspondencias",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="formato de salida (por defecto: text)",
    )
    args = parser.parse_args(argv)

    if not _is_readable_file(args.snapshot):
        _print_portfolio_valuation_input_error(
            args.format,
            "snapshot_not_readable",
            "Error de snapshot: el archivo no existe o no es legible.",
        )
        return 2
    if not _is_readable_file(args.mapping):
        _print_portfolio_valuation_input_error(
            args.format,
            "mapping_not_readable",
            "Error de mapping: el archivo no existe o no es legible.",
        )
        return 2
    try:
        snapshot = load_portfolio_snapshot(args.snapshot)
    except PortfolioSnapshotError:
        _print_portfolio_valuation_input_error(
            args.format,
            "invalid_snapshot",
            "Error de snapshot: el archivo no tiene un formato compatible.",
        )
        return 1
    try:
        mapping = load_market_mapping(args.mapping)
    except MarketMappingError:
        _print_portfolio_valuation_input_error(
            args.format,
            "invalid_mapping",
            "Error de mapping: el archivo no tiene un formato compatible.",
        )
        return 1

    valuation = value_portfolio(
        snapshot,
        mapping,
        lambda name: _provider(name, {}, {}),
    )
    if args.format == "json":
        print(json.dumps(valuation.as_dict(), ensure_ascii=False, sort_keys=True))
    else:
        print(render_portfolio_valuation_text(valuation), end="")
    return 0 if valuation.ok else 1


def _print_portfolio_valuation_input_error(
    output_format: str, code: str, message: str
) -> None:
    if output_format == "json":
        payload = {
            "valuation_schema_version": 1,
            "metadata": None,
            "coverage": None,
            "currency_totals": [],
            "positions": [],
            "exclusions": [],
            "warnings": [],
            "errors": [{"code": code, "instrument_id": None, "message": message}],
            "unavailable_fields": ["metadata", "coverage"],
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stderr)
    else:
        print(message, file=sys.stderr)


def _import_revolut_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="openportfolio import-revolut",
        description="Importa exportaciones CSV de Revolut sin red ni activación de alertas",
    )
    parser.add_argument(
        "--investment-history",
        type=Path,
        required=True,
        help="historial CSV de inversiones",
    )
    parser.add_argument(
        "--account-statement",
        type=Path,
        required=True,
        help="extracto CSV que contiene los movimientos XAU",
    )
    parser.add_argument(
        "--snapshot-output",
        type=Path,
        required=True,
        help="ruta de salida del snapshot normalizado",
    )
    parser.add_argument(
        "--report-output",
        type=Path,
        required=True,
        help="ruta de salida del informe de conciliación",
    )
    args = parser.parse_args(argv)

    inputs = (
        ("--investment-history", args.investment_history),
        ("--account-statement", args.account_statement),
    )
    for option, path in inputs:
        if not _is_readable_file(path):
            print(
                f"Error de importación: el archivo indicado por {option} "
                "no existe o no es legible.",
                file=sys.stderr,
            )
            return 2
    return _run_revolut_import(
        args.investment_history,
        args.account_statement,
        args.snapshot_output,
        args.report_output,
    )


def _import_revolut_latest_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="openportfolio import-revolut-latest",
        description="Descubre e importa las exportaciones CSV de Revolut más recientes",
    )
    parser.add_argument(
        "--input-directory",
        type=Path,
        required=True,
        help="directorio que contiene las exportaciones CSV",
    )
    parser.add_argument(
        "--snapshot-output",
        type=Path,
        required=True,
        help="ruta de salida del snapshot normalizado",
    )
    parser.add_argument(
        "--report-output",
        type=Path,
        required=True,
        help="ruta de salida del informe de conciliación",
    )
    args = parser.parse_args(argv)

    try:
        selection = discover_revolut_exports(args.input_directory)
    except RevolutDiscoveryError as error:
        print(f"Error de descubrimiento: {error}", file=sys.stderr)
        return 2

    return _run_revolut_import(
        selection.investment_history,
        selection.account_statement,
        args.snapshot_output,
        args.report_output,
        selection=selection,
    )


def _run_revolut_import(
    investment_history: Path,
    account_statement: Path,
    snapshot_output: Path,
    report_output: Path,
    *,
    selection: RevolutExportSelection | None = None,
) -> int:
    inputs = (investment_history, account_statement)
    output_paths = (snapshot_output, report_output)
    resolved_inputs = {path.resolve() for path in inputs}
    resolved_outputs = {path.resolve() for path in output_paths}
    if len(resolved_outputs) != len(output_paths) or resolved_inputs & resolved_outputs:
        print(
            "Error de importación: las rutas de salida deben ser distintas y no pueden "
            "sobrescribir entradas.",
            file=sys.stderr,
        )
        return 2

    now = datetime.now(timezone.utc)
    try:
        outcome = import_revolut_exports(
            investment_history,
            account_statement,
            generated_at=now,
        )
    except RevolutImportError as error:
        print(f"Error de importación: {error}", file=sys.stderr)
        return 1
    report = reconciliation_report(outcome, generated_at=now)

    if not outcome.ok:
        print(
            f"Error de importación: los datos no son válidos ({len(outcome.errors)} error(es)).",
            file=sys.stderr,
        )
        return 1
    assert outcome.snapshot is not None
    try:
        save_portfolio_snapshot(outcome.snapshot, snapshot_output)
        atomic_write_text(report_output, report)
    except PortfolioSnapshotError as error:
        print(f"Error de persistencia: {error}", file=sys.stderr)
        return 2

    operations = sum(result.rows_read for result in outcome.results)
    currencies = sorted({position.currency for position in outcome.snapshot.positions})
    if selection is not None:
        print(f"CSV examinados: {selection.csv_examined}")
        print(f"Archivos ignorados: {selection.csv_ignored}")
        print("Selección: un candidato de cada tipo")
    print(f"Operaciones procesadas: {operations}")
    print(f"Posiciones resultantes: {len(outcome.snapshot.positions)}")
    print(f"Monedas encontradas: {', '.join(currencies) if currencies else 'ninguna'}")
    print(f"Advertencias: {len(outcome.warnings)}")
    print("Conciliación: correcta")
    print(f"Snapshot generado: {snapshot_output}")
    print(f"Informe generado: {report_output}")
    return 0


def _is_readable_file(path: Path) -> bool:
    try:
        if not path.is_file():
            return False
        with path.open("rb") as stream:
            stream.read(1)
    except OSError:
        return False
    return True


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
