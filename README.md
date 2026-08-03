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

La primera configuración operativa no contiene posiciones, cantidades, precios de compra ni datos personales. NVDA y MSFT están activos; el ETF S&P 500, Alphabet y Nestlé permanecen inactivos porque sus símbolos exactos no aparecen documentados en el repositorio. Para consultar los precios activos y ejecutar la revisión real obligatoriamente en modo no notificable:

```bash
.venv/bin/openportfolio review \
  --portfolio examples/operational_review.yaml \
  --provider yfinance \
  --notifier console \
  --dry-run
```

El adaptador usa el último cierre diario utilizable de los últimos cinco días. yfinance es un servicio externo no contractual: requiere red, puede sufrir límites o cambios de API, tener retrasos, devolver moneda o timestamps incompletos y no garantiza disponibilidad ni calidad para todos los símbolos. OpenPortfolio trata esas situaciones como errores explícitos del instrumento; no sustituye precios ausentes por cero. No hay reintentos complejos en este incremento.

## Revisión y alertas

El ejemplo incluido es completamente ficticio. Sus precios y umbrales están preparados para producir de forma determinista una alerta `REVIEW` con el proveedor `fake`:

```bash
.venv/bin/openportfolio review --provider fake --notifier console
```

En la demo, cada posición configura en YAML `reference_price`, `review_change_percent` y `high_change_percent`. Una alerta solo inicia una revisión humana: no es una recomendación ni una instrucción de compra o venta.

La configuración operativa separa las reglas de las posiciones mediante `review_rules`. Sus cinco entradas están desactivadas para no inventar decisiones de inversión. Para cada instrumento hay que decidir `reference_price`, `review_change_percent` y `high_change_percent`, escribirlos como texto decimal y cambiar `enabled` a `true`. El umbral alto debe ser mayor que el de revisión.

La revisión conserva por defecto el estado de entregas en `state/alert_state.json`. El archivo JSON tiene una versión de esquema y un mapa `delivered_alerts`; cada entrada contiene únicamente el ID determinista de alerta, el ID del evento y la severidad entregada. No contiene configuración de ntfy, topics ni secretos. La ruta puede cambiarse con `--state-path`.

Después de que cualquier `Notifier` confirme una entrega, el estado se reemplaza de forma atómica. Una ejecución posterior suprime una alerta con el mismo ID ya entregado. Un escalado de `REVIEW` a `HIGH` produce una identidad determinista diferente y vuelve a notificarse. Los fallos de entrega y las ejecuciones `--dry-run` no modifican el estado. Por ahora no existen recordatorios periódicos, detección de recuperación ni rearme de condiciones.

Si el archivo todavía no existe, la ejecución se considera la primera y se crea el directorio al registrar la primera entrega. Si está corrupto o usa una versión desconocida, la aplicación termina con un error explícito y conserva el archivo anterior. El estado local es generado y está excluido de Git.

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

### Ejecución manual en GitHub Actions

Abre **Actions → Portfolio review → Run workflow** y selecciona:

- `provider`: `fake` es la opción segura predeterminada; `yfinance` usa `examples/operational_review.yaml` y consulta datos reales.
- `dry_run`: está activado por defecto. Para `yfinance` es obligatorio y el workflow rechaza explícitamente cualquier otra combinación.

El workflow ejecuta los tests, restaura el estado y muestra el resultado por consola. No construye ni contacta ntfy, no usa secretos y no tiene `schedule`. Una ejecución dry-run no guarda ni modifica `state/alert_state.json`; el paso de guardado solo puede ejecutarse tras una revisión fake no-dry-run completada. La ejecución yfinance actual consulta únicamente NVDA y MSFT hasta que se confirmen los otros tres símbolos y no aplica reglas hasta completar los valores pendientes.

GitHub Actions cache es la solución inicial de persistencia de la v1, no almacenamiento permanente garantizado: GitHub puede desalojar cachés, aplica límites de retención y una caché ausente hace que la siguiente ejecución se comporte como una primera ejecución. Por ello reduce duplicados entre runners, pero no ofrece las garantías de una base de datos.

## Tests

La suite ordinaria usa únicamente el proveedor ficticio y no necesita internet:

```bash
.venv/bin/python -m pytest
```
