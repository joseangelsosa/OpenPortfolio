# OpenPortfolio

OpenPortfolio es una prueba de concepto *open source* para observar una cartera de inversión a largo plazo. Su finalidad es informativa: no recomienda compras o ventas, no ejecuta operaciones y no se conecta a brokers.

El proyecto contiene una primera vertical de la Fase 1: carga una cartera completamente ficticia desde YAML, obtiene cotizaciones mediante un proveedor reemplazable y muestra la valoración de cada posición en su moneda original. La demo predeterminada es determinista y no usa red. El adaptador opcional de yfinance está aislado de los modelos de dominio.

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

## Tests

La suite ordinaria usa únicamente el proveedor ficticio y no necesita internet:

```bash
.venv/bin/python -m pytest
```
