# Cambios recientes del backend

Este documento resume los refuerzos realizados sobre el backend de Campo POS.
La arquitectura original se mantiene: el cliente de escritorio se comunica
con FastAPI y FastAPI accede a PostgreSQL.

El objetivo fue mejorar seguridad, validación, diagnóstico y confiabilidad sin
cambiar el flujo principal de caja y ventas.

## Resumen sencillo

El backend ya contaba con buenas bases: transacciones, pool de conexiones,
consultas parametrizadas e idempotencia para evitar ventas duplicadas.

Sobre esa base se agregaron cuatro mejoras principales:

1. Las rutas importantes ahora requieren una API key.
2. Los errores HTTP ahora tienen respuestas más claras y seguras.
3. El backend ya no confía ciegamente en los precios enviados por el cliente.
4. Se agregaron pruebas automatizadas para validar estos comportamientos.

## 1. Autenticación con API key

Las operaciones del POS ahora requieren el header:

```text
X-API-Key: clave-configurada
```

La clave se configura en el servidor mediante:

```env
CAMPO_API_KEY=una-clave-segura
```

Están protegidas estas áreas:

```text
/productos
/caja
/ventas
/reportes
```

Las rutas `/` y `/health` permanecen públicas para facilitar la verificación
del servicio.

El cliente de escritorio fue actualizado para enviar automáticamente la API
key. Puede leerla desde la variable de entorno `CAMPO_API_KEY` o desde:

```text
%LOCALAPPDATA%\CampoBurgerPOS\config.json
```

Ejemplo de configuración del cliente:

```json
{
  "api_url": "http://127.0.0.1:8000",
  "api_key": "una-clave-segura"
}
```

Esta API key es una protección inicial para evitar accesos no autorizados.
No reemplaza un sistema completo de usuarios, roles y claves independientes
por negocio.

## 2. Validación de errores HTTP

El backend ahora diferencia los errores según su causa:

| Situación | Respuesta |
| --- | --- |
| API key ausente o incorrecta | `401` |
| Datos inválidos de entrada | `422` |
| Caja ya abierta | `409` |
| Venta sin caja abierta | `409` |
| Venta no modificable | `409` |
| Conflicto de idempotencia | `409` |
| Problema con PostgreSQL | `503` |
| Error inesperado | `500` |

Antes, varias situaciones diferentes terminaban como `400` o exponían el
mensaje interno de la excepción. Ahora se devuelven mensajes más controlados
al cliente.

También se redujo la información expuesta por `/health`. La respuesta exitosa
ahora es solamente:

```json
{
  "status": "ok"
}
```

## 3. Backend como autoridad de precios

Antes el cliente enviaba el nombre, precio, subtotal y total de la venta.

Ahora cada ítem debe incluir el identificador real del producto:

```json
{
  "producto_id": 12,
  "cant": 2,
  "nombre": "Hamburguesa",
  "subtotal": "20000"
}
```

El backend consulta `productos` dentro de la misma transacción de la venta y:

- Comprueba que el producto exista.
- Obtiene el nombre real del catálogo.
- Obtiene el precio vigente del catálogo.
- Verifica si se vende por unidad o por peso.
- Recalcula el subtotal.
- Recalcula el total de la venta.
- Determina si el producto es un extra.
- Guarda en el detalle el precio y nombre normalizados.

Si el cliente intenta enviar un precio manipulado, el total ya no coincide con
el catálogo y la venta es rechazada.

### Productos por unidad

El subtotal se calcula así:

```text
precio vigente x cantidad
```

### Productos por peso

El subtotal se calcula así:

```text
precio vigente por kilo x peso en gramos / 1000
```

El precio por kilo enviado por el cliente ya no es la fuente de verdad.

### Cambio de contrato

`producto_id` ahora es obligatorio en cada elemento de `POST /ventas`.

El cliente incluido en este repositorio ya fue actualizado para enviarlo. Si
existiera otro cliente externo que consuma la API, también debe actualizarse.

Estos cambios no agregan columnas nuevas a PostgreSQL. Se utilizan las tablas y
columnas de productos que ya existían.

## 4. Validaciones de entrada

Los modelos de FastAPI ahora validan, entre otros datos:

- Identificadores UUID válidos para `operacion_id`.
- Cantidades mayores que cero.
- Montos no negativos.
- Límites razonables para cantidades e ítems.
- Longitud máxima de nombres y observaciones.
- Rechazo de campos desconocidos.

Esto evita que datos inesperados lleguen directamente a la capa de base de
datos.

## 5. Pruebas automatizadas

El archivo anterior era una prueba manual que dependía de una base real y no
era recogida por pytest.

Ahora `tests/test_database.py` contiene pruebas automatizadas para:

- Validación de métodos de pago.
- Requisito de `producto_id`.
- API key válida e inválida.
- Traducción de una venta sin caja a HTTP `409`.
- Envío de la API key desde el cliente.
- Conservación del código HTTP en `APIError`.
- Uso del precio del catálogo frente a un precio manipulado.

Actualmente se ejecutan nueve pruebas.

## Configuración local

Crear el entorno virtual e instalar dependencias:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
```

Crear `.env` a partir de `.env.example`:

```powershell
Copy-Item .env.example .env
```

El archivo `.env` debe contener credenciales reales de PostgreSQL:

```env
DB_HOST=host-real-de-postgresql
DB_PORT=5432
DB_NAME=postgres
DB_USER=usuario-real
DB_PASSWORD=contraseña-real
DB_SSLMODE=require
DB_CONNECT_TIMEOUT=10
DB_POOL_MIN=1
DB_POOL_MAX=5
DB_TIMEZONE=America/Asuncion

CAMPO_API_URL=http://127.0.0.1:8000
CAMPO_API_KEY=una-clave-segura
```

`TU_HOST_DE_POSTGRESQL`, `TU_USUARIO` y valores similares son solamente
placeholders. No permiten conectarse a una base real.

El backend utiliza PostgreSQL directamente. No necesita instalar PostgREST.
La base debe tener las tablas requeridas por `DBManager`, incluyendo
`productos`, `sesiones_caja`, `ventas` y `detalle_ventas`.

## Cómo ejecutar el backend

Desde la raíz del proyecto:

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn server.api:app --reload
```

La API quedará disponible en:

```text
http://127.0.0.1:8000
```

## Cómo revisar la API

Abrir Swagger:

```text
http://127.0.0.1:8000/docs
```

En Swagger se puede pulsar **Authorize** y escribir la API key. Luego se puede
probar `/productos`, `/caja` y `/ventas`.

También se puede probar desde PowerShell:

```powershell
$headers = @{ "X-API-Key" = "una-clave-segura" }
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/productos" `
  -Headers $headers
```

Sin la API key, una ruta protegida debe responder `401`.

## Cómo ejecutar el cliente

Abrir una segunda terminal de VS Code:

```powershell
.\.venv\Scripts\Activate.ps1
$env:CAMPO_API_URL="http://127.0.0.1:8000"
$env:CAMPO_API_KEY="una-clave-segura"
python -m client.main
```

Desde el POS se puede comprobar el flujo completo:

1. Cargar productos.
2. Abrir caja.
3. Agregar productos al carrito.
4. Registrar una venta.
5. Consultar la venta y el resumen de caja.

## Cómo ejecutar las pruebas

```powershell
.\.venv\Scripts\Activate.ps1
python -m pytest -q
```

Resultado esperado actualmente:

```text
9 passed
```

También se puede verificar que no haya errores de sintaxis:

```powershell
python -m compileall -q server client tests
```

## Archivos modificados

```text
server/api.py
server/database.py
client/api_client.py
client/config.py
client/gui.py
tests/test_database.py
requirements-dev.txt
.env.example
README.md
```

## Próximas mejoras posibles

Estas mejoras no forman parte de este cambio, pero representan una evolución
natural del sistema:

- Usuarios, roles y permisos.
- Auditoría de operaciones.
- Claves independientes por instalación.
- Migraciones formales de base de datos.
- Copias de seguridad automatizadas.
- Monitoreo y alertas.
