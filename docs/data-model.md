# Modelo conceptual de datos

## Convenciones

Este documento define conceptos y validaciones, no clases ni un esquema físico. Los nombres de tipos son descriptivos y podrán concretarse al implementar.

Las decisiones concretas forman el **baseline v0.1**, sujeto a validación durante la PoC. YAML se usa para configuración mantenida por personas y JSON para modelos generados, eventos, resultados e intercambio entre módulos.

- `Identifier`: identificador opaco, estable y no vacío. No contiene datos personales.
- `Decimal`: número decimal exacto; no se usa coma flotante para dinero, cantidades, precios o porcentajes.
- `CurrencyCode`: código ISO 4217 en mayúsculas cuando exista una moneda aplicable.
- `Timestamp`: fecha e instante con zona horaria, normalizado a UTC para almacenamiento y comparación.
- `LocalDate`: fecha de calendario sin hora cuando el origen solo aporta fecha.
- `Percentage`: proporción decimal con unidad explícita; un valor como 20 % no se confunde con `20`.
- `StructuredData`: estructura serializable como JSON cuyos campos están definidos por un contrato o versión.
- los campos opcionales deben representar ausencia conocida; no se sustituyen silenciosamente por cero ni por la hora actual;
- los datos derivados conservan referencias a sus entradas y a su instante de cálculo.

## Instrument

Representa un activo identificable con independencia del proveedor de precios.

| Campo | Tipo conceptual | Obligatorio | Descripción |
|---|---|---:|---|
| `id` | `Identifier` | Sí | Identidad interna estable. |
| `name` | `Text` | Sí | Nombre descriptivo no personal. |
| `asset_type` | `Enum` | Sí | Categoría controlada, por ejemplo acción, ETF, fondo o efectivo. |
| `currency` | `CurrencyCode` | Sí | Moneda de cotización o valoración propia del instrumento. |
| `isin` | `Text` | No | ISIN normalizado, si se conoce y resulta aplicable. |
| `provider_symbols` | `Map<ProviderName, Text>` | No | Símbolos externos aislados por proveedor. |
| `classification` | `Map<Dimension, Value>` | No | Atributos controlados para exposición, por ejemplo región, sector o narrativa. |
| `active` | `Boolean` | Sí | Indica si se solicita nueva información del instrumento. |

Validación:

- `id` es único dentro del conjunto de instrumentos;
- `name` no está vacío y `currency` es válida;
- un ISIN, si existe, cumple su formato y dígito de control;
- cada proveedor tiene como máximo un símbolo por instrumento en el alcance inicial;
- `asset_type` y las claves de `classification` pertenecen a vocabularios conocidos;
- no se presupone que un símbolo sea universal ni se usa como identidad interna.

## Position

Representa la tenencia agregada de un instrumento en una cartera en un momento determinado.

| Campo | Tipo conceptual | Obligatorio | Descripción |
|---|---|---:|---|
| `id` | `Identifier` | Sí | Identidad de la posición. |
| `portfolio_id` | `Identifier` | Sí | Cartera a la que pertenece. |
| `instrument_id` | `Identifier` | Sí | Instrumento mantenido. |
| `quantity` | `Decimal` | Sí | Unidades netas mantenidas. |
| `average_cost` | `Decimal` | No | Coste medio por unidad conforme a una política declarada. |
| `cost_currency` | `CurrencyCode` | No | Moneda del coste medio. |
| `accumulated_cost` | `Decimal` | No | Coste analítico total de las unidades restantes. |
| `as_of` | `Timestamp` | Sí | Instante al que corresponde la posición. |
| `source` | `Enum` | Sí | Origen controlado, por ejemplo configuración ficticia o importación manual. |

Validación:

- referencia a un `Instrument` existente;
- la pareja `portfolio_id`–`instrument_id` es única para un mismo `as_of` en una vista agregada;
- `quantity` no es negativa en la PoC, que no admite posiciones cortas;
- `average_cost` es no negativo y exige `cost_currency`; si no puede calcularse, ambos quedan ausentes y se informa;
- `accumulated_cost` es igual a cantidad por coste medio dentro de la precisión acordada;
- el coste medio es ponderado: una compra añade su importe y comisión al coste acumulado; una venta reduce el coste acumulado al coste medio vigente sin cambiar el coste unitario restante;
- dividendos, recompensas e ingresos en efectivo no reducen el coste medio; *splits* y operaciones corporativas ajustan cantidad y coste unitario sin alterar el coste total;
- la política usada para agregar transacciones es consistente, reproducible y analítica, no fiscal; FIFO queda fuera de la PoC;
- se conserva precisión decimal y solo se redondea al presentar;
- una cantidad cero puede conservarse solo si existe una razón de trazabilidad; no aporta exposición actual.

## Transaction

Representa un movimiento normalizado que modifica unidades o efectivo. Es la entrada futura para reconstruir posiciones, no una orden de trading.

| Campo | Tipo conceptual | Obligatorio | Descripción |
|---|---|---:|---|
| `id` | `Identifier` | Sí | Identidad interna estable e idempotente. |
| `portfolio_id` | `Identifier` | Sí | Cartera afectada. |
| `instrument_id` | `Identifier` | Condicional | Instrumento afectado cuando el tipo lo requiere. |
| `type` | `Enum` | Sí | `BUY`, `SELL`, `DIVIDEND`, `FEE`, `CASH_TOP_UP`, `CASH_WITHDRAWAL`, `REWARD`, `FX_CONVERSION`, `STOCK_SPLIT`, `CORPORATE_ACTION` o `UNKNOWN`. |
| `original_type` | `Text` | No | Tipo o subtipo exacto informado por el origen. |
| `trade_date` | `LocalDate` | Sí | Fecha económica informada por el origen. |
| `settled_at` | `Timestamp` | No | Instante de liquidación, si está disponible. |
| `quantity` | `Decimal` | Condicional | Unidades con convención de signo documentada. |
| `unit_price` | `Decimal` | Condicional | Precio por unidad para operaciones que lo requieren. |
| `original_amount` | `Decimal` | Condicional | Importe en la moneda original antes de conversión. |
| `original_currency` | `CurrencyCode` | Condicional | Moneda original que nunca se sustituye por la moneda consolidada. |
| `fees` | `Decimal` | Sí | Comisiones no negativas; cero debe ser explícito si se conoce. |
| `fee_currency` | `CurrencyCode` | Condicional | Moneda de las comisiones, si difiere o debe hacerse explícita. |
| `fx_rate` | `Decimal` | No | Tipo aplicado desde la moneda original a EUR. |
| `converted_amount_eur` | `Decimal` | No | Importe histórico convertido a EUR con `fx_rate`. |
| `fx_source` | `Text` | No | Fuente del tipo histórico, preferentemente la propia transacción de Revolut. |
| `fx_observed_at` | `Timestamp` o `LocalDate` | No | Fecha o instante al que corresponde el FX histórico. |
| `source` | `Enum` | Sí | Procedencia controlada. |
| `source_format` | `Text` | No | Familia y versión del formato de importación. |
| `source_reference` | `Text` | No | Referencia técnica no personal para deduplicación. |
| `raw_record` | `StructuredData` | No | Fila original conservada localmente para trazabilidad, nunca versionada ni registrada íntegramente. |
| `normalization_status` | `Enum` | Sí | Normalizada, incompleta, pendiente de revisión, cancelada o rechazada. |
| `imported_at` | `Timestamp` | Sí | Instante de incorporación al sistema. |

Validación:

- cada tipo define qué campos condicionales necesita y su convención de signo; los subtipos de compra o venta se normalizan al tipo económico conservando `original_type`;
- cantidades, precios e importes respetan precisión decimal y coherencia aritmética dentro de una tolerancia declarada;
- `unit_price`, `original_amount` y `fees` no son negativos; compras y ventas requieren cantidad positiva y precio;
- una venta no puede dejar una posición negativa en la PoC;
- toda operación monetaria conserva importe y moneda originales; si se convierte, conserva además FX, valor EUR, fuente y fecha;
- para el coste histórico se prefiere el FX de la transacción de Revolut; nunca se recalcula con el cambio actual;
- si falta un FX histórico requerido, `normalization_status` es incompleta y no se inventa un tipo de cambio;
- un tipo desconocido usa `UNKNOWN`, conserva `original_type` y `raw_record` y queda pendiente de revisión en lugar de descartarse;
- `source_reference`, si existe, es única dentro de una fuente y cartera o produce una detección explícita de duplicado;
- la importación valida `source_format` antes de normalizar y usa un adaptador por familia y versión; una columna obligatoria ausente produce un error explícito, no una importación parcial silenciosa;
- `raw_record` permanece en almacenamiento local protegido y fuera del repositorio público, fixtures y logs;
- crear una `Transaction` no implica ni permite enviar una orden a un broker.

## PortfolioSnapshot

Representa una valoración inmutable de una cartera para un instante, construida con posiciones y cotizaciones concretas.

| Campo | Tipo conceptual | Obligatorio | Descripción |
|---|---|---:|---|
| `id` | `Identifier` | Sí | Identidad de la instantánea. |
| `portfolio_id` | `Identifier` | Sí | Cartera valorada. |
| `as_of` | `Timestamp` | Sí | Instante canónico de la valoración. |
| `base_currency` | `CurrencyCode` | Sí | Moneda consolidada; es EUR en el baseline v0.1. |
| `positions` | `List<PositionValuation>` | Sí | Posiciones y valores derivados con referencias a posición y cotización. |
| `total_market_value` | `Decimal` | No | Suma de valores válidos en moneda base. |
| `exposures` | `Map<Dimension, Map<Value, Decimal>>` | Sí | Pesos o valores agregados por dimensiones soportadas. |
| `quote_ids` | `Set<Identifier>` | Sí | Cotizaciones de activos y FX utilizadas. |
| `completeness` | `Enum` | Sí | Completa, parcial o no valorable. |
| `calculated_at` | `Timestamp` | Sí | Instante de cálculo. |

`PositionValuation` incluye conceptualmente la referencia a la posición, cantidad y unidad originales, precio y moneda empleados, valor original, peso y valor convertido a EUR. Cualquier conversión referencia el tipo de cambio, `MarketQuote`, proveedor y fecha utilizados. No introduce un modelo independiente en esta fase.

Validación:

- es inmutable una vez creado;
- todas las posiciones pertenecen a `portfolio_id` y no son posteriores a `as_of`;
- cada valoración monetaria referencia una cotización válida y, cuando proceda, una conversión explícita a `base_currency`;
- `base_currency` es EUR en el baseline, sin modificar la unidad ni moneda originales de oro, acciones en USD u otros activos;
- `total_market_value` solo se considera completo si todas las posiciones materiales son valorables;
- los pesos de una instantánea completa suman uno dentro de una tolerancia decimal declarada;
- las exposiciones se derivan de las mismas valoraciones, sin doble conteo;
- precios ausentes, incompatibilidad de moneda o antigüedad excesiva degradan `completeness` en lugar de producir ceros silenciosos.

## MarketQuote

Representa una observación de mercado normalizada y desacoplada del objeto nativo del proveedor.

| Campo | Tipo conceptual | Obligatorio | Descripción |
|---|---|---:|---|
| `id` | `Identifier` | Sí | Identidad de la observación. |
| `instrument_id` | `Identifier` | Sí | Instrumento observado. |
| `price` | `Decimal` | Sí | Precio normalizado. |
| `currency` | `CurrencyCode` | Sí | Moneda del precio. |
| `observed_at` | `Timestamp` | Sí | Instante al que corresponde el dato de mercado. |
| `retrieved_at` | `Timestamp` | Sí | Instante de obtención. |
| `provider` | `ProviderName` | Sí | Proveedor que originó el dato. |
| `provider_symbol` | `Text` | Sí | Símbolo solicitado al proveedor. |
| `kind` | `Enum` | Sí | Naturaleza del dato, por ejemplo cierre, último disponible o tipo de cambio. |
| `quality` | `Enum` | Sí | Estado de frescura normalizado, por ejemplo válido, `stale` o no utilizable. |

Validación:

- referencia a un `Instrument` existente y el símbolo concuerda con su mapeo para el proveedor;
- `price` es estrictamente positivo;
- `observed_at` no es posterior a `retrieved_at`, salvo una tolerancia documentada por desfases de reloj;
- moneda y naturaleza del precio son explícitas;
- una cotización FX identifica de forma inequívoca el par y sentido de conversión; puede modelarse mediante un instrumento de par de divisas explícito;
- la obsolescencia se calcula con una política configurada, calendario aplicable e instante de valoración;
- durante un día de mercado, más de 24 horas implica `stale` y `REVIEW`; más de 72 horas sin explicación por fin de semana o festivo implica `HIGH`; un FX de más de 24 horas en una ejecución ordinaria es `stale`;
- un mercado cerrado conforme a su calendario no se clasifica por sí mismo como dato obsoleto;
- una ausencia o error del proveedor no se representa como cotización con precio cero.

## AnalysisEvent

Representa el resultado estructurado de una regla determinista aplicada a entradas identificables.

| Campo | Tipo conceptual | Obligatorio | Descripción |
|---|---|---:|---|
| `id` | `Identifier` | Sí | Identidad reproducible o trazable del evento. |
| `portfolio_id` | `Identifier` | Sí | Cartera analizada. |
| `snapshot_id` | `Identifier` | No | Instantánea evaluada, si la regla depende de ella. |
| `rule_id` | `Identifier` | Sí | Regla que produjo el evento. |
| `rule_version` | `Text` | Sí | Versión de la semántica de la regla. |
| `type` | `Enum` | Sí | Desviación, concentración, evento de mercado o calidad de datos. |
| `severity` | `Enum` | Sí | `INFO`, `REVIEW` o `HIGH`, determinado por la regla. |
| `subject_refs` | `Set<Identifier>` | Sí | Instrumentos, posiciones o agrupaciones afectadas. |
| `observed_values` | `Map<Text, Scalar>` | Sí | Valores que justifican el resultado, con unidades. |
| `thresholds` | `Map<Text, Scalar>` | Sí | Umbrales aplicados, con unidades. |
| `occurred_at` | `Timestamp` | Sí | Instante económico o analítico del evento. |
| `evaluated_at` | `Timestamp` | Sí | Instante de ejecución de la regla. |
| `evidence` | `StructuredData` | Sí | Referencias y datos mínimos para auditar el resultado. |

Validación:

- `rule_id` y `rule_version` identifican de forma inequívoca la lógica aplicada;
- valores observados y umbrales tienen unidades y monedas compatibles;
- `severity` se obtiene de configuración o lógica determinista, nunca de texto generado por IA;
- los umbrales proceden del YAML versionado y no están codificados rígidamente;
- el baseline cubre caídas del 20 % (`REVIEW`) y 25 % (`HIGH`), concentración individual próxima o superior al 10 % (`REVIEW`), rangos de ETF 70–80 %, acciones 20–30 % y oro 5–10 % (`REVIEW` fuera del rango), Bitcoin superior al 3 % (`HIGH`) y sectores fuera de su rango (`REVIEW`);
- un fallo aislado del proveedor es `INFO`; fallos consecutivos que impiden valorar son `HIGH`;
- `subject_refs` y las referencias de evidencia existen en las entradas;
- el mismo conjunto canónico de regla, versión, configuración e inputs permite deduplicar o reproducir el evento;
- los fallos técnicos se modelan como estado operativo o evento de calidad de datos cuando proceda, no como señal de inversión.

## Alert

Representa una notificación preparada a partir de eventos; su estado de entrega no altera los eventos originales.

| Campo | Tipo conceptual | Obligatorio | Descripción |
|---|---|---:|---|
| `id` | `Identifier` | Sí | Identidad de la alerta. |
| `event_ids` | `NonEmptySet<Identifier>` | Sí | Eventos incluidos. |
| `channel` | `Enum` | Sí | Canal de salida, inicialmente local y después Telegram. |
| `destination_ref` | `SecretReference` | No | Referencia externa al destino; nunca el secreto o identificador sensible en claro. |
| `priority` | `Enum` | Sí | Prioridad derivada de severidades mediante política determinista. |
| `title` | `Text` | Sí | Resumen informativo. |
| `body` | `Text` | Sí | Contenido renderizado con evidencia comprensible. |
| `created_at` | `Timestamp` | Sí | Instante de creación. |
| `deduplication_key` | `Text` | Sí | Clave estable para evitar repeticiones no deseadas. |
| `lifecycle_status` | `Enum` | Sí | Abierta, resuelta o reabierta respecto de la condición analizada. |
| `delivery_status` | `Enum` | Sí | Pendiente, enviada, fallida o suprimida. |
| `delivery_attempts` | `List<DeliveryAttempt>` | Sí | Metadatos mínimos de intentos, sin secretos. |
| `last_notified_at` | `Timestamp` | No | Último envío de esta condición deduplicada. |
| `reminder_due_at` | `Timestamp` | No | Próximo recordatorio permitido por la política. |
| `previous_alert_id` | `Identifier` | No | Alerta anterior de la misma condición, si existe. |
| `generated_explanation` | `Text` | No | Explicación opcional etiquetada como generada por IA. |

`DeliveryAttempt` contiene conceptualmente instante, resultado y un código de error saneado. No almacena tokens, contenido sensible de respuestas ni credenciales.

Validación:

- contiene al menos un `AnalysisEvent` existente y todos corresponden a una agrupación compatible;
- prioridad, agrupación y clave de deduplicación siguen políticas versionadas;
- una condición abierta solo se reenvía si aumenta la severidad, cambia materialmente según la tolerancia configurada o vence `reminder_due_at`;
- una condición resuelta puede pasar a reabierta cuando vuelva a cruzar el umbral, conservando el vínculo con su historial;
- Telegram solo recibe `REVIEW`, `HIGH` o un fallo técnico definitivo; `INFO` queda disponible para consulta y no se envía un mensaje diario de estado correcto;
- `title` y `body` no prometen certeza, no recomiendan operaciones y distinguen evidencia de explicación;
- una explicación generada es opcional, está etiquetada y no modifica eventos, prioridad ni estado;
- las transiciones de ciclo de vida y entrega son válidas e independientes, y cada intento añade trazabilidad sin sobrescribir el historial;
- destinos y credenciales se resuelven fuera del modelo mediante configuración segura en tiempo de ejecución.

## Relaciones y ciclo de datos

- un `Instrument` puede aparecer en muchas `Position`, `Transaction` y `MarketQuote`;
- una cartera contiene posiciones y transacciones, identificadas por `portfolio_id`, aunque `Portfolio` como entidad agregada no se define todavía como modelo persistente independiente;
- muchas `Transaction` pueden reconstruir una `Position`; alternativamente, una posición ficticia inicial puede proceder directamente de configuración;
- un `PortfolioSnapshot` reúne valoraciones de muchas `Position` y referencia las `MarketQuote` utilizadas;
- una regla evalúa una instantánea o cotizaciones y produce cero o más `AnalysisEvent`;
- uno o más eventos compatibles originan un `Alert`; un evento puede participar en alertas distintas por canal sin ser modificado.

## Coherencia temporal, monetaria y de procedencia

Cada cálculo define un `as_of` y evita mezclar observaciones posteriores a ese instante. La frescura se evalúa respecto a ese momento y no solo respecto a la hora de descarga. Los instantes canónicos se conservan en UTC y la presentación usa `Europe/Madrid`. Las conversiones entre monedas requieren una observación de tipo de cambio identificable; si no existe, el sistema marca la valoración como parcial.

El coste histórico conserva el FX de la operación y nunca se reexpresa con el cambio actual. La valoración usa el FX vigente de un proveedor reemplazable. En ambos casos se conservan importe y moneda originales, tipo aplicado, valor EUR, fuente y fecha. EUR es la moneda consolidada del baseline v0.1, no una sustitución de la naturaleza monetaria original del dato.

Los campos derivados deben poder rastrearse hasta posiciones, transacciones, cotizaciones, configuración y versión de regla. La procedencia no incluye datos personales: identifica el tipo de fuente y referencias técnicas mínimas. Esta trazabilidad permite distinguir una condición financiera calculada de un error de adquisición o de una explicación opcional generada por IA.
