# OpenPortfolio

OpenPortfolio es una prueba de concepto *open source* para observar una cartera de inversión a largo plazo. Su finalidad es informativa: no recomienda compras o ventas, no ejecuta operaciones y no se conecta a brokers.

El proyecto contiene una primera vertical de la Fase 1: carga una cartera completamente ficticia desde YAML, obtiene cotizaciones mediante un proveedor reemplazable y muestra la valoración de cada posición en su moneda original. También adelanta una vertical mínima de revisión: aplica una regla determinista de variación frente a un precio de referencia, genera eventos y alertas y puede mostrarlas en consola o enviarlas mediante ntfy. La demo predeterminada es determinista y no usa red. El adaptador opcional de yfinance está aislado de los modelos de dominio.

La conversión de divisas todavía no está implementada. Los totales se muestran por moneda y nunca se suman EUR y USD silenciosamente.

## Instalación

Requiere Python 3.13 y un entorno virtual. Para instalar el proyecto y las dependencias de desarrollo en el entorno existente:

```bash
.venv/bin/python -m pip install -e '.[dev]'
```

## Ejecución

La demo offline usa el proveedor ficticio de forma predeterminada:

```bash
.venv/bin/openportfolio
```

También se puede indicar la configuración y el proveedor explícitamente:

```bash
.venv/bin/openportfolio --portfolio examples/demo_portfolio.yaml --provider fake
```

La primera configuración operativa no contiene posiciones, cantidades, precios de compra ni datos personales. Incluye H4ZF.DE, NVDA, GOOGL, MSFT y NESR.DE como instrumentos activos. Para comprobar sus cotizaciones reales sin ejecutar reglas, crear alertas, usar notifiers ni leer o escribir `alert_state.json`:

```bash
.venv/bin/openportfolio --check-quotes \
  --portfolio examples/operational_review.yaml \
  --provider yfinance
```

La comprobación muestra para cada instrumento el nombre, símbolo solicitado, precio, moneda recibida, timestamp, procedencia (`source`) y resultado. Una moneda distinta de `expected_currency` o cualquier fallo de consulta marca el símbolo como fallido; aun así se consultan los demás y el proceso termina con código distinto de cero al final.

El adaptador de yfinance solicita primero velas intradía de 5 minutos de la sesión ordinaria (`period="1d"`, `interval="5m"`, `prepost=False`). Elige la vela válida más reciente cuyo intervalo de cinco minutos ya haya terminado y cuya fecha, en la zona horaria de la vela, coincida con la fecha de consulta. Si no existe una vela así —incluidos mercado cerrado, respuesta vacía, datos inválidos o fallo técnico— recurre al último cierre diario utilizable de los últimos cinco días.

`INTRADAY` significa que el precio procede de una vela intradía completa de 5 minutos; `DAILY_CLOSE` significa que procede del respaldo de cierre diario. El timestamp siempre corresponde al dato de mercado y no a una hora inventada de ejecución. yfinance es un servicio externo no contractual y no una fuente profesional con tiempo real garantizado: requiere red, puede sufrir límites o cambios de API, tener retrasos y devolver moneda o timestamps incompletos. OpenPortfolio trata esas situaciones como errores explícitos del instrumento; no sustituye precios ausentes por cero. No hay reintentos complejos en este incremento.

## Revisión y alertas

El ejemplo incluido es completamente ficticio. Sus precios y umbrales están preparados para producir de forma determinista una alerta `REVIEW` con el proveedor `fake`:

```bash
.venv/bin/openportfolio review --provider fake --notifier console
```

En la demo, cada posición configura en YAML `reference_price`, `review_change_percent` y `high_change_percent`. `reference_price` es el precio fijo contra el que se calcula `((precio actual - referencia) / referencia) * 100`; no se reajusta automáticamente en esta v1. Una alerta solo inicia una revisión humana conforme al IOS: no constituye asesoramiento financiero ni una recomendación o instrucción de compra, venta o mantenimiento.

La configuración operativa separa las reglas de las posiciones mediante `review_rules`. Los cinco instrumentos tienen una referencia inicial fija y reglas activas: una variación absoluta desde el 5 % (inclusive) y menor del 10 % produce `REVIEW`; desde el 10 % (inclusive) produce `HIGH`. Se detectan tanto subidas como caídas y el mensaje conserva el signo.

La revisión conserva por defecto el estado de entregas en `state/alert_state.json`. El archivo JSON tiene una versión de esquema y un mapa `delivered_alerts`; cada entrada contiene únicamente el ID determinista de alerta, el ID del evento y la severidad entregada. No contiene configuración de ntfy, topics ni secretos. La ruta puede cambiarse con `--state-path`.

Después de que cualquier `Notifier` confirme una entrega, el estado se reemplaza de forma atómica. Una ejecución posterior suprime la misma condición por instrumento y dirección; un escalado de `REVIEW` a `HIGH` vuelve a notificarse. Al regresar por debajo del 5 % la condición se rearma para permitir una alerta en un cruce futuro. Los fallos de entrega y las ejecuciones `--dry-run` no registran una entrega.

Cada revisión operativa real completada correctamente produce al menos una push. Si hay alertas nuevas se envían las alertas de mercado `REVIEW`/`HIGH`, sin añadir un mensaje genérico. Si no hay movimientos relevantes se envía una confirmación operativa que indica que se revisaron los cinco instrumentos. Si todos los movimientos quedan suprimidos por deduplicación, la confirmación indica que no hay alertas nuevas y cuántos movimientos ya habían sido notificados; no afirma que no existan movimientos relevantes. Esta confirmación es un mensaje operativo independiente, no una alerta de mercado: no tiene severidad `REVIEW`/`HIGH` y nunca se guarda en `alert_state.json`. Un fallo de cotización, revisión, estado o notificación termina con error y no genera una confirmación falsa de éxito.

Si el archivo todavía no existe, la ejecución se considera la primera y el servicio crea el directorio al persistir el resultado de la revisión. Si está corrupto o usa una versión desconocida, la aplicación termina con un error explícito y conserva el archivo anterior. El estado local es generado y está excluido de Git.

Para construir y ver exactamente el contenido de una notificación ntfy sin realizar ninguna llamada HTTP ni exigir un topic:

```bash
.venv/bin/openportfolio review --provider fake --notifier ntfy --dry-run
```

### Configuración de ntfy

1. Instala la aplicación oficial de ntfy en el móvil.
2. Elige un topic largo, aleatorio y no reutilizado, y suscríbete a él en la aplicación. No uses ninguno de los valores ficticios del repositorio.
3. Trata el topic como un secreto: en un servidor público, un topic anónimo funciona como un enlace privado y quien lo conozca puede publicar o suscribirse.
4. Configura las variables solo en el entorno de ejecución:

```bash
export OPENPORTFOLIO_NTFY_SERVER='https://ntfy.sh'
export OPENPORTFOLIO_NTFY_TOPIC='<topic-secreto-configurado-localmente>'
.venv/bin/openportfolio review --provider fake --notifier ntfy
```

`OPENPORTFOLIO_NTFY_SERVER` es opcional y usa `https://ntfy.sh` por defecto. `OPENPORTFOLIO_NTFY_TOPIC` es obligatorio para un envío real. Esta versión no implementa autenticación de usuario de ntfy ni despliega un servidor propio.

### Revisiones programadas y ejecución manual en GitHub Actions

El workflow ejecuta automáticamente `review-and-notify` de lunes a viernes a las 08:05, 12:05, 16:05 y 20:05 en la zona IANA `Europe/Madrid`. GitHub adapta esos horarios al verano y al invierno sin conversiones UTC manuales. Los workflows programados siempre usan la rama predeterminada y GitHub Actions puede retrasar el inicio en periodos de carga elevada.

Abre **Actions → Portfolio review → Run workflow** y selecciona una operación:

- `fake`: revisión determinista offline con datos ficticios.
- `dry-run`: previsualización ficticia sin notificación ni cambios de estado.
- `check-real-quotes`: consulta yfinance, pero queda aislada de reglas, estado, notifier y secretos.
- `send-test-notification`: envía exactamente el mensaje marcado como `PRUEBA`, sin yfinance, cartera operativa ni estado real.
- `review-and-notify`: ejecuta manualmente el mismo camino que el schedule: consulta yfinance, ejecuta las cinco reglas operativas, restaura y guarda `alert_state.json` y envía por ntfy las alertas o la confirmación operativa aplicable.

Los dos envíos ntfy requieren crear el GitHub Actions secret `OPENPORTFOLIO_NTFY_TOPIC`; el workflow no imprime su valor y falla de forma explícita si falta. Las operaciones manuales `fake`, `dry-run`, `check-real-quotes`, `send-test-notification` y `review-and-notify` permanecen disponibles. `check-real-quotes` no construye ni contacta ntfy y no requiere secretos; `send-test-notification` conserva su único mensaje específico de prueba y ninguna de las dos operaciones usa el estado de alertas.

Las revisiones reales comparten un grupo de concurrencia estable con `cancel-in-progress: false`: una revisión iniciada termina antes de que otra pueda restaurar y modificar el mismo estado. Tanto el schedule como `review-and-notify` restauran el estado antes de revisar y guardan después la versión actualizada.

GitHub Actions cache es la solución inicial de persistencia de la v1, no almacenamiento permanente garantizado: GitHub puede desalojar cachés, aplica límites de retención y una caché ausente hace que la siguiente ejecución se comporte como una primera ejecución. Por ello reduce duplicados entre runners, pero no ofrece las garantías de una base de datos.

## Tests

La suite ordinaria usa únicamente el proveedor ficticio y no necesita internet:

```bash
.venv/bin/python -m pytest
```
