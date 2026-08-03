from __future__ import annotations

from collections.abc import Callable
import os
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from openportfolio.domain import Alert, OperationalNotification, Severity


DEFAULT_NTFY_SERVER = "https://ntfy.sh"


class NotificationError(RuntimeError):
    """Base error for sanitized notification failures."""


class NotificationConfigurationError(NotificationError):
    """The selected notification channel is not configured safely."""


class NotificationDeliveryError(NotificationError):
    """The notification channel could not deliver an alert."""


class Notifier(Protocol):
    def send(self, notification: Alert | OperationalNotification) -> None:
        """Deliver one notification or raise a sanitized notification error."""
        ...


class ConsoleNotifier:
    def __init__(self, output: Callable[[str], None] = print) -> None:
        self._output = output

    def send(self, notification: Alert | OperationalNotification) -> None:
        self._output(f"{notification.title}\n{notification.body}")


class NtfyNotifier:
    def __init__(
        self,
        *,
        server: str | None = None,
        topic: str | None = None,
        timeout: float = 10.0,
        http_open: Callable[..., Any] = urlopen,
    ) -> None:
        self._server = (
            server or os.getenv("OPENPORTFOLIO_NTFY_SERVER") or DEFAULT_NTFY_SERVER
        ).rstrip("/")
        self._topic = (
            topic if topic is not None else os.getenv("OPENPORTFOLIO_NTFY_TOPIC") or ""
        ).strip()
        self._timeout = timeout
        self._http_open = http_open
        parsed = urlparse(self._server)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.query
            or parsed.fragment
        ):
            raise NotificationConfigurationError("el servidor ntfy debe ser una URL HTTP(S) válida")
        if timeout <= 0:
            raise NotificationConfigurationError("el timeout de ntfy debe ser mayor que cero")
        if not self._topic:
            raise NotificationConfigurationError(
                "falta OPENPORTFOLIO_NTFY_TOPIC para realizar un envío ntfy"
            )

    def send(self, notification: Alert | OperationalNotification) -> None:
        request = Request(
            f"{self._server}/{quote(self._topic, safe='')}",
            data=notification.body.encode("utf-8"),
            method="POST",
            headers={
                "Title": notification.title,
                "Priority": self._priority(notification),
                "Content-Type": "text/plain; charset=utf-8",
            },
        )
        try:
            with self._http_open(request, timeout=self._timeout) as response:
                status = getattr(response, "status", 200)
                if status < 200 or status >= 300:
                    raise NotificationDeliveryError(
                        f"ntfy rechazó la notificación con estado HTTP {status}"
                    )
        except HTTPError as error:
            raise NotificationDeliveryError(
                f"ntfy rechazó la notificación con estado HTTP {error.code}"
            ) from None
        except URLError:
            raise NotificationDeliveryError("no se pudo conectar con el servidor ntfy") from None
        except NotificationDeliveryError:
            raise
        except OSError:
            raise NotificationDeliveryError("falló la conexión con el servidor ntfy") from None
        except Exception:
            raise NotificationDeliveryError("falló el envío al servidor ntfy") from None

    @staticmethod
    def _priority(notification: Alert | OperationalNotification) -> str:
        return (
            "5"
            if isinstance(notification, Alert) and notification.severity is Severity.HIGH
            else "3"
        )
