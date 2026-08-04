from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
from typing import Mapping
from zoneinfo import ZoneInfo

from openportfolio.domain import (
    CostBasisStatus,
    ImportedPosition,
    ImportSource,
    PositionStatus,
)


INVESTMENTS_HEADER = (
    "Date",
    "Ticker",
    "Type",
    "Quantity",
    "Price per share",
    "Total Amount",
    "Currency",
    "FX Rate",
)
ACCOUNT_STATEMENT_HEADER = (
    "Tipo",
    "Producto",
    "Fecha de inicio",
    "Fecha de finalización",
    "Descripción",
    "Importe",
    "Comisión",
    "Divisa",
    "State",
    "Saldo",
)
INVESTMENTS_FORMAT = "revolut_investments_csv_v1"
XAU_FORMAT = "revolut_xau_statement_es_csv_v1"

BUY_TYPES = frozenset(("BUY - LIMIT", "BUY - MARKET"))
SELL_TYPES = frozenset(("SELL - LIMIT", "SELL - STOP"))
IGNORED_TYPES = frozenset(("DIVIDEND", "CASH TOP-UP", "CASH WITHDRAWAL", "REWARD"))
KNOWN_TYPES = BUY_TYPES | SELL_TYPES | IGNORED_TYPES


class RevolutCsvReadError(ValueError):
    """A candidate CSV cannot be read from local storage."""


class UnknownRevolutCsvFormatError(ValueError):
    """A readable CSV does not match a supported Revolut structure."""

@dataclass(frozen=True, slots=True)
class InstrumentPolicy:
    name: str
    market_symbol: str | None
    position_status: PositionStatus = PositionStatus.ACTIVE
    tradable: bool = True
    active_monitoring: bool = True
    exclusion_reason: str | None = None


# User-confirmed catalog. Both market mappings and manual exceptions live here;
# reconstruction contains no ticker-specific branches. Callers may inject a
# different catalog into import_investments when local policy changes.
REVOLUT_INSTRUMENTS: Mapping[str, InstrumentPolicy] = {
    "H4ZF": InstrumentPolicy("HSBC S&P 500 ETF", "H4ZF.DE"),
    "NVDA": InstrumentPolicy("Nvidia", "NVDA"),
    "AAPL": InstrumentPolicy("Apple", "AAPL"),
    "MSFT": InstrumentPolicy("Microsoft", "MSFT"),
    "AVGO": InstrumentPolicy("Broadcom", "AVGO"),
    "GOOGL": InstrumentPolicy("Alphabet", "GOOGL"),
    "NESR": InstrumentPolicy("Nestlé", "NESR.DE"),
    "PG": InstrumentPolicy("Procter & Gamble", "PG"),
    "KO": InstrumentPolicy("Coca-Cola", "KO"),
    "ZAL": InstrumentPolicy("Zalando", "ZAL.DE"),
    "B4F": InstrumentPolicy("Basic-Fit", "B4F.F"),
    "TM": InstrumentPolicy("Toyota", "TM"),
    "UBER": InstrumentPolicy("Uber", "UBER"),
    "IBE1": InstrumentPolicy("Iberdrola", "IBE1.DE"),
    "NKE": InstrumentPolicy("Nike", "NKE"),
    "H4ZC": InstrumentPolicy("HSBC MSCI Japan ETF", "H4ZC.DE"),
    "NVO": InstrumentPolicy("Novo Nordisk", "NVO"),
    "NFLX": InstrumentPolicy("Netflix", "NFLX"),
    "GTLB": InstrumentPolicy("GitLab", "GTLB"),
    "SST": InstrumentPolicy("System1", "SST"),
    "KOS": InstrumentPolicy("Kosmos Energy", "KOS"),
    "IRBTQ": InstrumentPolicy(
        name="iRobot",
        market_symbol=None,
        position_status=PositionStatus.LEGACY,
        tradable=False,
        active_monitoring=False,
        exclusion_reason=(
            "Quiebra confirmada por el usuario; posición bloqueada y sin valor realizable."
        ),
    ),
}

_MONEY = re.compile(r"^(?:(?P<currency>[A-Za-z]{3})\s+)?(?P<number>[+-]?(?:\d+(?:\.\d*)?|\.\d+))$")
_MADRID = ZoneInfo("Europe/Madrid")


@dataclass(frozen=True, slots=True)
class ImportIssue:
    source: ImportSource
    message: str
    row_number: int | None = None

    def render(self) -> str:
        location = f", fila {self.row_number}" if self.row_number is not None else ""
        return f"{self.source.value}{location}: {self.message}"


@dataclass(frozen=True, slots=True)
class RevolutSourceResult:
    source: ImportSource
    format: str
    rows_read: int
    positions: tuple[ImportedPosition, ...] = ()
    closed_positions: tuple[str, ...] = ()
    counts: Mapping[str, int] = field(default_factory=dict)
    warnings: tuple[ImportIssue, ...] = ()
    errors: tuple[ImportIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True, slots=True)
class _InvestmentMovement:
    row_number: int
    occurred_at: datetime
    ticker: str
    movement_type: str
    quantity: Decimal | None
    price_per_share: Decimal | None
    total_amount: Decimal
    currency: str
    fx_rate: Decimal


@dataclass(slots=True)
class _PositionLedger:
    currency: str
    quantity: Decimal = Decimal("0")
    carrying_cost: Decimal = Decimal("0")
    seen_trade: bool = False


def detect_revolut_format(path: str | Path) -> ImportSource:
    source = Path(path)
    try:
        with source.open(encoding="utf-8-sig", newline="") as stream:
            header = next(csv.reader(stream), None)
    except OSError as error:
        raise RevolutCsvReadError("no se puede leer el CSV indicado") from error
    except (UnicodeError, csv.Error) as error:
        raise UnknownRevolutCsvFormatError(
            "formato CSV de Revolut desconocido: contenido no válido"
        ) from error
    normalized = tuple(value.strip() for value in (header or ()))
    if normalized == INVESTMENTS_HEADER:
        return ImportSource.INVESTMENTS
    if normalized == ACCOUNT_STATEMENT_HEADER:
        return ImportSource.XAU_STATEMENT
    raise UnknownRevolutCsvFormatError(
        "formato CSV de Revolut desconocido: la cabecera no coincide"
    )


def import_investments(
    path: str | Path,
    *,
    instrument_catalog: Mapping[str, InstrumentPolicy] = REVOLUT_INSTRUMENTS,
) -> RevolutSourceResult:
    source_id = ImportSource.INVESTMENTS
    rows, header_error = _read_csv(path, INVESTMENTS_HEADER, source_id)
    if header_error is not None:
        return RevolutSourceResult(source_id, INVESTMENTS_FORMAT, len(rows), errors=(header_error,))

    movements: list[_InvestmentMovement] = []
    errors: list[ImportIssue] = []
    counts = {"buys": 0, "sells": 0, "dividends": 0, "cash": 0, "rewards": 0}
    for row_number, row in enumerate(rows, start=2):
        movement, issue = _investment_row(row, row_number)
        if issue is not None:
            errors.append(issue)
            continue
        assert movement is not None
        movements.append(movement)
        if movement.movement_type in BUY_TYPES:
            counts["buys"] += 1
        elif movement.movement_type in SELL_TYPES:
            counts["sells"] += 1
        elif movement.movement_type == "DIVIDEND":
            counts["dividends"] += 1
        elif movement.movement_type in {"CASH TOP-UP", "CASH WITHDRAWAL"}:
            counts["cash"] += 1
        elif movement.movement_type == "REWARD":
            counts["rewards"] += 1
    if errors:
        return RevolutSourceResult(
            source_id, INVESTMENTS_FORMAT, len(rows), counts=counts, errors=tuple(errors)
        )

    ledgers: dict[str, _PositionLedger] = {}
    reconstruction_errors: list[ImportIssue] = []
    difference_counts: dict[str, int] = {}
    for movement in sorted(movements, key=lambda item: (item.occurred_at, item.row_number)):
        if movement.movement_type not in BUY_TYPES | SELL_TYPES:
            continue
        assert movement.quantity is not None and movement.price_per_share is not None
        ledger = ledgers.get(movement.ticker)
        if ledger is None:
            ledger = _PositionLedger(currency=movement.currency)
            ledgers[movement.ticker] = ledger
        elif ledger.currency != movement.currency:
            reconstruction_errors.append(
                ImportIssue(
                    source_id,
                    f"el ticker {movement.ticker} aparece en más de una moneda "
                    f"({ledger.currency} y {movement.currency})",
                    movement.row_number,
                )
            )
            continue
        ledger.seen_trade = True
        if movement.movement_type in BUY_TYPES:
            effective_cost = abs(movement.total_amount)
            if effective_cost <= 0:
                reconstruction_errors.append(
                    ImportIssue(source_id, "una compra tiene coste efectivo no positivo", movement.row_number)
                )
                continue
            gross = movement.price_per_share * movement.quantity
            difference = abs(effective_cost - gross)
            material_threshold = max(Decimal("0.01"), abs(gross) * Decimal("0.001"))
            if difference >= material_threshold:
                difference_counts[movement.ticker] = difference_counts.get(movement.ticker, 0) + 1
            ledger.quantity += movement.quantity
            ledger.carrying_cost += effective_cost
            continue
        if movement.quantity > ledger.quantity:
            reconstruction_errors.append(
                ImportIssue(
                    source_id,
                    f"venta de {movement.ticker} superior a la posición disponible",
                    movement.row_number,
                )
            )
            continue
        average_cost = ledger.carrying_cost / ledger.quantity
        ledger.quantity -= movement.quantity
        ledger.carrying_cost -= average_cost * movement.quantity
        if ledger.quantity == 0:
            ledger.carrying_cost = Decimal("0")

    if reconstruction_errors:
        return RevolutSourceResult(
            source_id,
            INVESTMENTS_FORMAT,
            len(rows),
            counts=counts,
            errors=tuple(reconstruction_errors),
        )

    positions: list[ImportedPosition] = []
    closed: list[str] = []
    for ticker, ledger in sorted(ledgers.items()):
        if ledger.quantity == 0:
            closed.append(ticker)
            continue
        policy = instrument_catalog.get(ticker)
        positions.append(
            ImportedPosition(
                asset_type="equity",
                source_ticker=ticker,
                market_symbol=policy.market_symbol if policy is not None else None,
                quantity=ledger.quantity,
                currency=ledger.currency,
                average_cost=ledger.carrying_cost / ledger.quantity,
                cost_basis_status=CostBasisStatus.AVAILABLE,
                source=source_id,
                name=policy.name if policy is not None else None,
                position_status=(
                    policy.position_status if policy is not None else PositionStatus.ACTIVE
                ),
                tradable=policy.tradable if policy is not None else True,
                active_monitoring=policy.active_monitoring if policy is not None else True,
                exclusion_reason=policy.exclusion_reason if policy is not None else None,
            )
        )
    warnings = [
        ImportIssue(
            source_id,
            f"{count} compra(s) de {ticker} tienen una diferencia material entre "
            "Total Amount y Price per share × Quantity; se usó Total Amount",
        )
        for ticker, count in sorted(difference_counts.items())
    ]
    unresolved = sorted(
        position.source_ticker
        for position in positions
        if position.active_monitoring and position.market_symbol is None
    )
    if unresolved:
        warnings.append(
            ImportIssue(
                source_id,
                "tickers activos sin mapping confirmado: " + ", ".join(unresolved),
            )
        )
    return RevolutSourceResult(
        source_id,
        INVESTMENTS_FORMAT,
        len(rows),
        tuple(positions),
        tuple(closed),
        counts,
        tuple(warnings),
    )


def import_xau_statement(path: str | Path) -> RevolutSourceResult:
    source_id = ImportSource.XAU_STATEMENT
    rows, header_error = _read_csv(path, ACCOUNT_STATEMENT_HEADER, source_id)
    if header_error is not None:
        return RevolutSourceResult(source_id, XAU_FORMAT, len(rows), errors=(header_error,))

    parsed: list[tuple[datetime, int, Decimal, Decimal, Decimal]] = []
    errors: list[ImportIssue] = []
    counts = {"xau_buys": 0, "xau_sells": 0, "ignored": 0}
    for row_number, row in enumerate(rows, start=2):
        state = row["State"].strip().upper()
        currency = row["Divisa"].strip().upper()
        description = row["Descripción"].strip()
        if state != "COMPLETADO" or currency != "XAU":
            counts["ignored"] += 1
            continue
        if description not in {"Conversión a XAU", "Conversión a EUR"}:
            errors.append(
                ImportIssue(source_id, "operación XAU completada con descripción desconocida", row_number)
            )
            continue
        try:
            occurred_at = _parse_statement_date(row["Fecha de finalización"])
            amount = _plain_decimal(row["Importe"], "Importe")
            commission = _plain_decimal(row["Comisión"], "Comisión")
            balance = _plain_decimal(row["Saldo"], "Saldo")
            if commission < 0:
                raise ValueError("Comisión no puede ser negativa")
            if description == "Conversión a XAU" and amount <= 0:
                raise ValueError("Conversión a XAU debe aumentar unidades")
            if description == "Conversión a EUR" and amount >= 0:
                raise ValueError("Conversión a EUR debe reducir unidades")
        except ValueError as error:
            errors.append(ImportIssue(source_id, str(error), row_number))
            continue
        counts["xau_buys" if description == "Conversión a XAU" else "xau_sells"] += 1
        parsed.append((occurred_at, row_number, amount, commission, balance))
    if errors:
        return RevolutSourceResult(source_id, XAU_FORMAT, len(rows), counts=counts, errors=tuple(errors))
    if not parsed:
        return RevolutSourceResult(
            source_id,
            XAU_FORMAT,
            len(rows),
            counts=counts,
            errors=(ImportIssue(source_id, "no hay conversiones XAU completadas"),),
        )

    parsed.sort(key=lambda item: (item[0], item[1]))
    first = parsed[0]
    opening_balance = first[4] - (first[2] - first[3])
    previous_balance = opening_balance
    for _, row_number, amount, commission, balance in parsed:
        expected = previous_balance + amount - commission
        if balance != expected:
            errors.append(
                ImportIssue(source_id, "el saldo XAU no concilia con Importe - Comisión", row_number)
            )
        if balance < 0:
            errors.append(ImportIssue(source_id, "el saldo XAU resultante es negativo", row_number))
        previous_balance = balance
    if errors:
        return RevolutSourceResult(source_id, XAU_FORMAT, len(rows), counts=counts, errors=tuple(errors))

    warnings = [
        ImportIssue(
            source_id,
            "las fechas sin zona horaria se interpretaron como Europe/Madrid y se normalizaron a UTC",
        ),
        ImportIssue(
            source_id,
            "el coste medio de XAU no está disponible: hace falta el contravalor pagado "
            "y sus comisiones en la moneda de efectivo",
        ),
    ]
    if opening_balance != 0:
        warnings.append(
            ImportIssue(source_id, "el periodo comienza con saldo XAU previo; se conservó al conciliar")
        )
    positions = ()
    if previous_balance > 0:
        positions = (
            ImportedPosition(
                asset_type="commodity",
                source_ticker="XAU",
                market_symbol=None,
                quantity=previous_balance,
                currency="XAU",
                average_cost=None,
                cost_basis_status=CostBasisStatus.UNAVAILABLE,
                source=source_id,
                name="Gold",
            ),
        )
    closed = ("XAU",) if previous_balance == 0 else ()
    return RevolutSourceResult(
        source_id,
        XAU_FORMAT,
        len(rows),
        positions,
        closed,
        counts,
        tuple(warnings),
    )


def _read_csv(
    path: str | Path,
    expected_header: tuple[str, ...],
    source: ImportSource,
) -> tuple[list[dict[str, str]], ImportIssue | None]:
    try:
        with Path(path).open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            header = tuple(value.strip() for value in (reader.fieldnames or ()))
            if header != expected_header:
                return [], ImportIssue(source, "cabecera CSV inválida", 1)
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error):
        return [], ImportIssue(source, "no se puede leer el CSV indicado")
    return rows, None


def _investment_row(
    row: dict[str, str], row_number: int
) -> tuple[_InvestmentMovement | None, ImportIssue | None]:
    source = ImportSource.INVESTMENTS
    try:
        movement_type = row["Type"].strip().upper()
        if movement_type not in KNOWN_TYPES:
            raise ValueError(f"tipo de movimiento desconocido: {movement_type or 'vacío'}")
        occurred_at = _parse_iso_utc(row["Date"])
        ticker = row["Ticker"].strip().upper()
        currency = row["Currency"].strip().upper()
        if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
            raise ValueError("Currency no es un código válido")
        total_amount = _money_decimal(row["Total Amount"], currency, "Total Amount")
        fx_rate = _plain_decimal(row["FX Rate"], "FX Rate")
        if fx_rate <= 0:
            raise ValueError("FX Rate debe ser mayor que cero")
        quantity: Decimal | None = None
        price: Decimal | None = None
        if movement_type in BUY_TYPES | SELL_TYPES:
            if not ticker:
                raise ValueError("Ticker es obligatorio para compras y ventas")
            quantity = _plain_decimal(row["Quantity"], "Quantity")
            price = _money_decimal(row["Price per share"], currency, "Price per share")
            if quantity <= 0 or price <= 0:
                raise ValueError("Quantity y Price per share deben ser mayores que cero")
        elif movement_type == "DIVIDEND" and not ticker:
            raise ValueError("Ticker es obligatorio para dividendos")
        return (
            _InvestmentMovement(
                row_number,
                occurred_at,
                ticker,
                movement_type,
                quantity,
                price,
                total_amount,
                currency,
                fx_rate,
            ),
            None,
        )
    except (KeyError, ValueError) as error:
        message = "falta una columna obligatoria" if isinstance(error, KeyError) else str(error)
        return None, ImportIssue(source, message, row_number)


def _parse_iso_utc(value: str) -> datetime:
    text = value.strip()
    try:
        timestamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Date no es una fecha ISO-8601 válida") from error
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Date debe incluir zona horaria")
    return timestamp.astimezone(timezone.utc)


def _parse_statement_date(value: str) -> datetime:
    text = value.strip()
    try:
        timestamp = datetime.fromisoformat(text)
    except ValueError as error:
        raise ValueError("Fecha de finalización no es válida") from error
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=_MADRID)
    return timestamp.astimezone(timezone.utc)


def _money_decimal(value: str, expected_currency: str, field_name: str) -> Decimal:
    match = _MONEY.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"{field_name} no es un importe monetario válido")
    prefix = match.group("currency")
    if prefix is not None and prefix.upper() != expected_currency:
        raise ValueError(f"{field_name} usa una moneda distinta de Currency")
    return _decimal_text(match.group("number"), field_name)


def _plain_decimal(value: str, field_name: str) -> Decimal:
    text = value.strip().replace("\u00a0", "").replace(" ", "")
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    return _decimal_text(text, field_name)


def _decimal_text(value: str, field_name: str) -> Decimal:
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{field_name} no es un decimal válido") from error
    if not result.is_finite():
        raise ValueError(f"{field_name} debe ser finito")
    return result
