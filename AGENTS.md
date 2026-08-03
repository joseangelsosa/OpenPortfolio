# Instrucciones del repositorio

Estas instrucciones se aplican permanentemente a cualquier trabajo realizado en este repositorio.

## Desarrollo

- Usar Python 3.13.
- Mantener el código sencillo, modular, tipado y comprobable mediante tests.
- Separar claramente la adquisición de datos, las reglas de análisis, las alertas y la presentación.
- Tratar yfinance como un proveedor reemplazable detrás de una interfaz propia; no integrar su API directamente en toda la aplicación.

## Seguridad y alcance

- Nunca incluir credenciales, tokens ni datos personales en el repositorio.
- Usar variables de entorno para todos los secretos.
- No ejecutar operaciones de trading ni conectarse a un broker.

## Verificación

- Antes de terminar cualquier cambio, ejecutar todas las comprobaciones disponibles y comunicar sus resultados.
- No instalar dependencias ni crear commits salvo que la tarea lo solicite expresamente.
