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

La consulta real es opcional, requiere red y puede fallar de forma explícita si el proveedor no encuentra un símbolo, devuelve datos vacíos o no está disponible:

```bash
.venv/bin/openportfolio --portfolio examples/demo_portfolio.yaml --provider yfinance
```

## Revisión y alertas

El ejemplo incluido es completamente ficticio. Sus precios y umbrales están preparados para producir de forma determinista una alerta `REVIEW` con el proveedor `fake`:

```bash
.venv/bin/openportfolio review --provider fake --notifier console
```

Cada posición configura en YAML `reference_price`, `review_change_percent` y `high_change_percent`. Una alerta solo inicia una revisión humana: no es una recomendación ni una instrucción de compra o venta.

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

En GitHub, abre **Settings → Secrets and variables → Actions** y crea estos repository secrets:

- `OPENPORTFOLIO_NTFY_TOPIC`: el topic secreto elegido.
- `OPENPORTFOLIO_NTFY_SERVER`: el servidor alternativo, si se necesita. Si no existe, el workflow usa `https://ntfy.sh`.

Después abre **Actions → Portfolio review → Run workflow**. El workflow ejecuta primero todos los tests y la revisión ficticia en consola; únicamente después intenta el envío real. Solo tiene disparador manual, por lo que todavía no existe ejecución programada. Si falta el topic, el paso final falla con un error de configuración saneado.

## Tests

La suite ordinaria usa únicamente el proveedor ficticio y no necesita internet:

```bash
.venv/bin/python -m pytest
```
