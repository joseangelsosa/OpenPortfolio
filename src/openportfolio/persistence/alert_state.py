from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
from types import MappingProxyType
from typing import Mapping, Protocol

from openportfolio.domain import Alert, Severity


STATE_VERSION = 1
DEFAULT_ALERT_STATE_PATH = Path("state/alert_state.json")


class AlertStateError(RuntimeError):
    """The delivery state could not be read or written safely."""


@dataclass(frozen=True, slots=True)
class DeliveredAlert:
    event_id: str
    severity: Severity


@dataclass(frozen=True, slots=True)
class AlertState:
    delivered_alerts: Mapping[str, DeliveredAlert]

    def __post_init__(self) -> None:
        object.__setattr__(self, "delivered_alerts", MappingProxyType(dict(self.delivered_alerts)))

    @classmethod
    def empty(cls) -> AlertState:
        return cls(delivered_alerts={})

    def was_delivered(self, alert: Alert) -> bool:
        delivered = self.delivered_alerts.get(alert.id)
        if delivered is None:
            return False
        rank = {Severity.INFO: 0, Severity.REVIEW: 1, Severity.HIGH: 2}
        return rank[delivered.severity] >= rank[alert.severity]

    def with_delivered(self, alert: Alert) -> AlertState:
        delivered = dict(self.delivered_alerts)
        delivered[alert.id] = DeliveredAlert(
            event_id=alert.event_id,
            severity=alert.severity,
        )
        return AlertState(delivered_alerts=delivered)

    def without(self, alert_ids: set[str]) -> AlertState:
        return AlertState(
            delivered_alerts={
                alert_id: record
                for alert_id, record in self.delivered_alerts.items()
                if alert_id not in alert_ids
            }
        )


class AlertStateStore(Protocol):
    def load(self) -> AlertState:
        """Load delivery state, returning an empty state on first use."""
        ...

    def save(self, state: AlertState) -> None:
        """Persist the complete delivery state or raise AlertStateError."""
        ...


class JsonAlertStateStore:
    def __init__(self, path: Path = DEFAULT_ALERT_STATE_PATH) -> None:
        self.path = path

    def load(self) -> AlertState:
        if not self.path.exists():
            return AlertState.empty()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return self._decode(raw)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise AlertStateError(
                f"no se pudo leer el estado de alertas de {self.path}: {error}"
            ) from error

    def save(self, state: AlertState) -> None:
        parent = self.path.parent
        temporary_path: Path | None = None
        try:
            parent.mkdir(parents=True, exist_ok=True)
            payload = self._encode(state)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                json.dump(payload, temporary, ensure_ascii=True, indent=2, sort_keys=True)
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, self.path)
        except (OSError, TypeError, ValueError) as error:
            raise AlertStateError(
                f"no se pudo guardar el estado de alertas en {self.path}: {error}"
            ) from error
        finally:
            if temporary_path is not None and temporary_path.exists():
                try:
                    temporary_path.unlink()
                except OSError:
                    pass

    @staticmethod
    def _encode(state: AlertState) -> dict[str, object]:
        return {
            "version": STATE_VERSION,
            "delivered_alerts": {
                alert_id: {
                    "event_id": record.event_id,
                    "severity": record.severity.value,
                }
                for alert_id, record in sorted(state.delivered_alerts.items())
            },
        }

    @staticmethod
    def _decode(raw: object) -> AlertState:
        if not isinstance(raw, dict):
            raise ValueError("la raíz debe ser un objeto JSON")
        if raw.get("version") != STATE_VERSION:
            raise ValueError(f"versión de estado no soportada: {raw.get('version')!r}")
        raw_alerts = raw.get("delivered_alerts")
        if not isinstance(raw_alerts, dict):
            raise ValueError("delivered_alerts debe ser un objeto JSON")

        delivered: dict[str, DeliveredAlert] = {}
        for alert_id, value in raw_alerts.items():
            if not isinstance(alert_id, str) or not alert_id.strip():
                raise ValueError("cada ID de alerta debe ser texto no vacío")
            if not isinstance(value, dict):
                raise ValueError(f"el registro {alert_id!r} debe ser un objeto JSON")
            event_id = value.get("event_id")
            severity = value.get("severity")
            if not isinstance(event_id, str) or not event_id.strip():
                raise ValueError(f"event_id inválido en {alert_id!r}")
            try:
                parsed_severity = Severity(severity)
            except (TypeError, ValueError):
                raise ValueError(f"severity inválida en {alert_id!r}") from None
            delivered[alert_id] = DeliveredAlert(event_id=event_id, severity=parsed_severity)
        return AlertState(delivered_alerts=delivered)
