# Hoja de ruta de OpenPortfolio

## Criterios generales

Las fases son incrementales: cada una conserva la separación entre dominio, adquisición de datos, análisis, alertas y presentación descrita en [architecture.md](architecture.md). Los ejemplos y pruebas usarán exclusivamente datos ficticios. Ninguna fase autoriza conexión a brokers, ejecución de operaciones ni recomendaciones automáticas de compra o venta.

Las decisiones concretas de esta hoja de ruta forman el **baseline v0.1**, sujeto a validación explícita durante la PoC. Cualquier cambio posterior deberá conservar su motivación y versión.

## Fase 0: fundamentos del repositorio

### Objetivo

Establecer una base mínima, mantenible y verificable para desarrollar la prueba de concepto con Python 3.13.

### Entregables

- estructura modular alineada con la arquitectura;
- configuración del proyecto y gestión explícita de dependencias;
- convenciones de tipado, formato, análisis estático y tests;
- modelos y contratos mínimos del dominio, todavía sin integración externa;
- esquema para configuración humana en un subconjunto sencillo de YAML y contratos JSON para datos generados;
- documentación de desarrollo, configuración y seguridad;
- datos de ejemplo exclusivamente ficticios.

### Validación

- instalación reproducible en un entorno limpio con Python 3.13;
- todas las comprobaciones configuradas pasan localmente;
- tests unitarios demuestran que el dominio no importa yfinance, Streamlit ni Telegram;
- una revisión confirma que no hay secretos ni datos personales versionados;
- ejemplos YAML ficticios pasan la validación de esquema y las salidas JSON respetan sus contratos.

### Fuera de alcance

- consulta de precios reales;
- cálculo completo de una cartera;
- alertas y dashboard;
- workflows de automatización o configuración de producción;
- base de datos.

## Fase 1: cartera ficticia y precios

### Estado actual

En progreso. La primera vertical ya permite cargar y validar una cartera YAML completamente ficticia, modelar instrumentos, posiciones, cartera y cotizaciones con `Decimal`, consultar un proveedor offline determinista o el adaptador aislado de yfinance y mostrar valores por posición y totales separados por moneda.

Siguen pendientes para completar los criterios de esta fase el contrato y la obtención de FX, la conversión trazable a EUR, un `PortfolioSnapshot` consolidado con estado de completitud, la representación JSON de resultados, la política completa de frescura y las pruebas de contrato del adaptador real. Hasta entonces no se considera terminada la Fase 1.

### Objetivo

Cargar una cartera ficticia y valorarla con precios obtenidos mediante una interfaz sustituible de datos de mercado.

### Entregables

- esquema validado de configuración YAML;
- carga de instrumentos y posiciones ficticias;
- contrato de `Market Data` y adaptador inicial de yfinance;
- contrato reemplazable para obtener tipos de cambio vigentes;
- normalización de `MarketQuote`, incluidas moneda, instante, procedencia y frescura;
- caso de uso para producir un `PortfolioSnapshot` básico consolidado en EUR sin perder unidades ni divisas originales;
- resultados generados e intercambio entre módulos representables como JSON;
- dobles de proveedor para tests deterministas.

### Validación

- pruebas unitarias de carga, validación y valoración con precios fijos;
- pruebas de contrato para respuestas válidas, precio ausente, dato obsoleto y error del proveedor;
- pruebas de conversión actual a EUR que conservan tipo de cambio, fecha y fuente, y marcan como parcial cualquier conversión imposible;
- una prueba manual opcional confirma el adaptador sin convertir la red en requisito de la suite;
- se verifica que ninguna capa salvo `Providers` usa la API de yfinance.

### Fuera de alcance

- cartera o extractos reales de Revolut;
- históricos extensos y precios intradía garantizados;
- soporte de múltiples proveedores simultáneos;
- métricas avanzadas, alertas y recomendaciones;
- persistencia en base de datos.

## Fase 2: métricas y reglas

### Objetivo

Calcular métricas reproducibles y detectar condiciones relevantes mediante reglas deterministas y configurables.

### Entregables

- coste medio, valor, peso y exposiciones soportadas;
- coste medio ponderado analítico con comisiones de compra, ventas al coste medio vigente y ajustes por *splits* u operaciones corporativas;
- pesos objetivo y cálculo de desviaciones para ETF (70–80 %), acciones individuales (20–30 %), oro (5–10 %), Bitcoin (1–3 %) y sectores configurados;
- reglas configurables para caídas del 20 % (`REVIEW`) y del 25 % (`HIGH`), posición individual próxima o superior al 10 %, Bitcoin superior al 3 % (`HIGH`) y desviaciones de rangos (`REVIEW`);
- `AnalysisEvent` con evidencia, severidad y versión de regla;
- severidades `INFO`, `REVIEW` y `HIGH`, con alertas orientadas siempre a revisión humana;
- política de frescura que distingue cierres de mercado: cotización de más de 24 horas en día de mercado (`stale`/`REVIEW`), más de 72 horas sin explicación (`HIGH`) y FX de más de 24 horas (`stale`);
- política documentada para fallos aislados del proveedor (`INFO`) y fallos consecutivos que impiden valorar (`HIGH`);
- informe textual local que separe resultados de errores técnicos.

### Validación

- tests unitarios con casos límite y aritmética decimal;
- escenarios de aceptación con resultados esperados y entradas fijas;
- pruebas del coste medio confirman que una venta no cambia el coste unitario restante y que dividendos, recompensas e ingresos de efectivo no lo reducen;
- pruebas con calendarios demuestran que un mercado cerrado no se clasifica como dato obsoleto;
- repetición de una misma entrada produce los mismos eventos;
- revisión de que los textos no constituyen recomendaciones de compra o venta.

### Fuera de alcance

- predicciones, puntuaciones opacas o decisiones mediante IA;
- optimización automática y rebalanceo ejecutable;
- fiscalidad, FIFO, lotes fiscales y atribución avanzada de rendimiento;
- entrega por canales externos;
- activos o divisas cuya valoración no esté definida de forma explícita.

## Fase 3: notificaciones Telegram

### Objetivo

Entregar de forma segura alertas informativas derivadas de eventos deterministas.

### Entregables

- transformación de `AnalysisEvent` a `Alert`;
- deduplicación, agrupación, reapertura y estados de entrega;
- reenvío solo por aumento de severidad, cambio material configurable o vencimiento de recordatorio;
- interfaz de canal y adaptador de Telegram;
- plantillas que incluyan evidencia, procedencia y frescura cuando corresponda;
- configuración por variables de entorno y guía de secretos;
- automatización programada a las 22:30 UTC de lunes a viernes y activación manual mediante `workflow_dispatch`;
- hasta tres intentos con espera progresiva para fallos temporales, sin reintentar errores de configuración, validación o credenciales;
- permisos `contents: read`, timeout y concurrencia limitada, secretos en GitHub Actions Secrets y artifacts temporales solo cuando sean necesarios.

### Validación

- tests del formateo, agrupación, deduplicación y manejo de errores con un canal simulado;
- tests prueban que Telegram solo recibe `REVIEW`, `HIGH` o un fallo técnico definitivo, y que no envía confirmaciones diarias sin eventos;
- tests prueban una única alerta técnica tras agotar reintentos y ningún reintento ante errores no temporales;
- comprobación manual en un chat de prueba sin exponer token ni identificador;
- revisión de registros para asegurar que no revelan secretos;
- ejecución controlada del workflow y verificación de permisos mínimos y fallo observable.

### Fuera de alcance

- recepción de órdenes o comandos financieros por Telegram;
- recomendaciones personalizadas y botones de trading;
- garantía de entrega, alta disponibilidad o escalado multiusuario;
- almacenamiento de secretos en archivos del repositorio;
- comprobaciones intradía, garantía de ejecución exacta al minuto y commits automáticos de resultados;
- resumen semanal, que podrá añadirse como salida separada posteriormente;
- otros canales de mensajería.

## Fase 4: importación manual de Revolut

### Objetivo

Permitir que una persona importe localmente un archivo exportado por Revolut mediante un proceso explícito, revisable y sin conexión automática a la entidad.

### Entregables

- especificación versionada de cada formato a partir de una exportación nueva y representativa al iniciar la fase;
- adaptadores independientes para, al menos, las familias conocidas de valores en inglés y materias primas/oro en español; no habrá un parser único rígido;
- normalización a `Transaction` de `BUY`, `SELL`, `DIVIDEND`, `FEE`, `CASH_TOP_UP`, `CASH_WITHDRAWAL`, `REWARD`, `FX_CONVERSION`, `STOCK_SPLIT` y `CORPORATE_ACTION`;
- conservación local de la fila y subtipo originales junto al resultado normalizado; un tipo desconocido se mantiene y se marca para revisión;
- previsualización, validación y confirmación antes de incorporar datos;
- informe de filas aceptadas, duplicadas y rechazadas sin registrar contenido sensible;
- documentación de minimización, conservación y exclusión de archivos reales del control de versiones;
- muestras sintéticas que no reproduzcan datos personales ni extractos reales;
- conservación de moneda e importe originales, FX, valor convertido a EUR y fuente y fecha del cambio; la falta de FX histórico produce una transacción incompleta.

### Validación

- tests con archivos completamente ficticios para columnas, formatos regionales, fechas, decimales, monedas, duplicados, estados y tipos desconocidos;
- reconciliación de cantidades y totales contra resultados esperados;
- una columna esperada ausente produce un error explícito y nunca una importación parcial silenciosa;
- la validación representativa cubre acciones y ETF, oro, dividendos, compras, ventas, comisiones, conversiones de divisa y operaciones canceladas o incompletas;
- inspección de que archivos originales y salidas sensibles no se versionan ni aparecen en logs;
- una revisión humana confirma el mapeo de columnas para cada formato soportado.

### Fuera de alcance

- conexión a la API, scraping, credenciales o sincronización automática con Revolut;
- inclusión de extractos reales en tests o documentación;
- modificación de datos en Revolut;
- importación desde otros brokers o bancos;
- cálculo fiscal exhaustivo.

## Fase 5: dashboard Streamlit

### Objetivo

Ofrecer una vista local, interactiva y de solo lectura de la cartera, sus métricas y eventos.

### Entregables

- dashboard que consuma casos de uso existentes;
- vistas de posiciones, pesos, exposiciones, frescura de cotizaciones y eventos;
- estados claros para carga, ausencia de datos y errores;
- filtros y formatos de presentación sin duplicar reglas de negocio;
- indicaciones visibles sobre finalidad informativa y procedencia de datos.

### Validación

- tests de la lógica de presentación separada de Streamlit;
- pruebas manuales de los recorridos principales y estados vacíos o fallidos;
- comparación de cifras con el informe producido por los mismos casos de uso;
- revisión de que la interfaz no ofrece acciones de trading ni revela secretos.

### Fuera de alcance

- despliegue público de producción;
- autenticación, multiusuario y permisos por cartera;
- edición compleja de datos desde el dashboard;
- cálculos exclusivos de la interfaz;
- streaming de precios en tiempo real.

## Fase 6: incorporación controlada del Investment Operating System

### Objetivo

Incorporar gradualmente una capa versionada de políticas, contexto y apoyo a decisiones humanas, sin debilitar la arquitectura, la trazabilidad ni los límites de seguridad de OpenPortfolio.

### Entregables

- políticas y reglas deterministas para objetivos, rangos y concentración por activo, sector, región, divisa y narrativa;
- registro de tesis con estados `SOLID`, `WATCH`, `DETERIORATING` o `BROKEN`, decisiones relevantes y excepciones al marco;
- checklists mensuales, trimestrales y anuales, y revisiones activadas por precio, peso o deterioro de datos;
- explicación trazable de la regla del IOS activada e historial versionado de cambios del propio IOS;
- después de completar los elementos anteriores, explicaciones opcionales asistidas por IA y claramente etiquetadas;
- separación explícita entre hechos, reglas deterministas y comentarios generados.

### Validación

- revisión humana de seguridad, privacidad y finalidad antes de integrar datos o servicios;
- tests demuestran que las políticas, tesis, checklists e historial son comprensibles y utilizables sin IA;
- entradas, resultados y errores son trazables y existe una ruta de desactivación;
- cualquier texto generado se identifica como tal y conserva acceso a la evidencia determinista;
- la fase solo se acepta si el IOS no se modifica automáticamente y los criterios acordados se cumplen sin ampliar implícitamente el alcance financiero.

### Fuera de alcance

- adopción integral o irreversible de IOS;
- delegar decisiones de inversión o disparo de alertas a IA;
- envío de datos personales, credenciales o extractos sin una evaluación y autorización específicas;
- automatización de trading, integración con brokers o recomendaciones de compra y venta;
- predicciones de mercado;
- modificación automática de políticas, tesis o reglas del IOS.

## Decisiones de paso entre fases

El avance a una fase requiere que los entregables y validaciones de la anterior estén completos, que no existan secretos o datos reales en el repositorio y que los límites de alcance continúen documentados. Telegram, GitHub Actions, Revolut, Streamlit e IOS requieren además una revisión humana antes de activar su primera integración externa.
