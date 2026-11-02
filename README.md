# Campo POS

La documentación de los cambios recientes del backend está disponible en
[`README_CAMBIOS_BACKEND.md`](README_CAMBIOS_BACKEND.md).

Sistema de punto de venta de escritorio para Campo Burger & Açaí, orientado a negocios gastronómicos pequeños y medianos.

## Resumen de mejoras recientes

El backend ahora cuenta con controles adicionales para mejorar la seguridad y
la confiabilidad del sistema:

- Las rutas importantes requieren una API key para poder utilizarse.
- Los errores se responden de forma más clara según el problema ocurrido.
- El backend consulta los precios reales desde la base de datos.
- Los subtotales y totales se recalculan en el servidor.
- Cada producto de una venta se identifica mediante `producto_id`.
- Se validan mejor cantidades, montos, UUID y datos inesperados.
- `/health` ya no muestra información sensible de la base de datos.
- Se agregaron pruebas automatizadas para validar estos comportamientos.
- El cliente fue actualizado para enviar la API key y el ID de cada producto.

En resumen, el backend ya no confía completamente en los datos recibidos del
cliente: verifica quién realiza la operación, comprueba la información y
calcula los importes desde el catálogo central.

La versión actual utiliza una arquitectura cliente-servidor: la aplicación Windows no se conecta directamente a PostgreSQL. El cliente consume una API FastAPI desplegada en Render, y el backend accede a PostgreSQL en Supabase.

---

## Arquitectura actual

```text
Cajero
  ↓
Campo POS Desktop
Python + Tkinter
  ↓ HTTPS / JSON
APIClient
  ↓
FastAPI en Render
  ↓
DBManager / psycopg2
  ↓
Supabase PostgreSQL
```

### Flujo de una venta

```text
client/gui.py
  ↓
client/api_client.py
  ↓
POST /ventas
  ↓
server/api.py
  ↓
CrearVentaRequest
  ↓
server/database.py
  ↓
PostgreSQL / Supabase
```

El cliente genera un `operacion_id` para las ventas. El backend utiliza ese identificador junto con un hash de la operación para evitar ventas duplicadas ante reintentos o respuestas ambiguas.

---

## Tecnologías

### Cliente de escritorio

- Python 3.11
- Tkinter
- Requests
- PyWin32
- PyInstaller

### Backend

- FastAPI
- Uvicorn
- Pydantic
- Psycopg2

### Infraestructura

- Render — hosting de FastAPI
- Supabase — PostgreSQL
- HTTPS entre el cliente y la API

### Instalación Windows

- PyInstaller
- Inno Setup

---

## Estructura del proyecto

```text
POS_Hamburgueseria/
├── client/
│   ├── __init__.py
│   ├── main.py
│   ├── api_client.py
│   ├── config.py
│   ├── gui.py
│   ├── impresora_termica.py
│   ├── ui_dialogos.py
│   ├── ventana_cierre.py
│   └── ventana_reportes.py
├── server/
│   ├── __init__.py
│   ├── api.py
│   └── database.py
├── tests/
│   ├── __init__.py
│   └── test_database.py
├── installer/
│   └── CampoBurgerPOS.iss
├── scripts/
│   ├── build_campoburger.ps1
│   └── crear_instalador.ps1
├── sql/
│   ├── add_ventas_idempotencia.sql
│   └── reportes.sql
├── CampoBurgerPOS.spec
├── campo_burger.ico
├── requirements.txt
├── requirements-dev.txt
├── .env.example
└── README.md
```

---

## Módulos principales

### `client/main.py`

Punto de entrada del cliente de escritorio. Crea `APIClient` e inicia `VentanaPrincipal`.

### `client/gui.py`

Interfaz principal del POS. Gestiona catálogo, carrito, ventas, tickets, historial, caja e interacción con la impresora.

### `client/api_client.py`

Capa HTTP del cliente. Se comunica con FastAPI y mantiene una interfaz similar al antiguo acceso directo a base de datos.

El cliente usa actualmente un timeout de 90 segundos.

### `client/config.py`

Gestiona la URL de la API. La URL de producción por defecto es:

```text
https://campo-pos-api.onrender.com
```

La configuración local puede almacenarse en:

```text
%LOCALAPPDATA%\CampoBurgerPOS\config.json
```

### `server/api.py`

Backend FastAPI. Expone endpoints para productos, caja, ventas y reportes.

Ejemplos:

```text
GET   /health
GET   /productos
GET   /caja
GET   /caja/resumen
POST  /caja/abrir
POST  /caja/cerrar
GET   /ventas/sesion-activa
POST  /ventas
PATCH /ventas/{venta_id}/metodo-pago
POST  /ventas/{venta_id}/anular
GET   /reportes/semanal
GET   /reportes/mensual
```

### `server/database.py`

Capa de acceso a PostgreSQL. Usa un `ThreadedConnectionPool`, transacciones y validaciones antes de persistir los datos.

### `client/impresora_termica.py`

Integración con impresoras instaladas en Windows mediante PyWin32.

---

## Configuración

### Backend

Crear un archivo `.env` a partir de `.env.example`.

Las credenciales PostgreSQL son utilizadas únicamente por el backend y no deben incluirse en el ejecutable Windows.

Variables principales:

```text
DB_HOST
DB_PORT
DB_NAME
DB_USER
DB_PASSWORD
DB_SSLMODE
DB_CONNECT_TIMEOUT
DB_POOL_MIN
DB_POOL_MAX
DB_TIMEZONE
CAMPO_API_KEY
```

Nunca versionar el archivo `.env`.

La API requiere la variable `CAMPO_API_KEY` para las rutas de productos,
caja, ventas y reportes. El cliente debe enviar esa clave en el header
`X-API-Key`. Para el cliente instalado puede configurarse en
`%LOCALAPPDATA%\CampoBurgerPOS\config.json`:

```json
{
  "api_url": "https://campo-pos-api.onrender.com",
  "api_key": "clave-configurada-en-render"
}
```

La misma clave compartida es una protección inicial contra accesos no
autorizados, pero no reemplaza usuarios, roles ni claves independientes por
instalación.

### Cliente

El cliente no necesita credenciales PostgreSQL.

Solo necesita conocer la URL HTTPS de Campo POS API.

También necesita la API key configurada para poder consultar el catálogo,
registrar ventas y operar la caja.

---

## Desarrollo local

Instalar dependencias:

```powershell
python -m pip install -r requirements-dev.txt
```

Ejecutar la API:

```powershell
python -m uvicorn server.api:app --reload
```

Ejecutar el cliente:

```powershell
python -m client.main
```

Para apuntar temporalmente el cliente a otra API se puede utilizar `CAMPO_API_URL` o actualizar `config.json`.

---

## Build del cliente Windows

```powershell
.\scripts\build_campoburger.ps1
```

Resultado:

```text
dist\CampoBurgerPOS\CampoBurgerPOS.exe
```

El proceso de build verifica que archivos `.env` y componentes del servidor no se distribuyan con el cliente.

---

## Instalador Windows

Después del build:

```powershell
.\scripts\crear_instalador.ps1
```

Resultado:

```text
installer\CampoBurgerPOS_Setup.exe
```

---

## Seguridad y confiabilidad

La arquitectura actual evita distribuir credenciales PostgreSQL en las PCs del negocio.

Además se implementó idempotencia para `POST /ventas`:

```text
operacion_id UUID
+
operacion_hash
+
índice UNIQUE
```

Esto permite reconocer una misma operación y evita duplicar una venta si existe un reintento.

### Pendientes importantes

El proyecto todavía tiene mejoras previstas antes de considerarlo completamente endurecido para múltiples clientes o exposición pública a mayor escala:

- autenticación y autorización de usuarios
- roles y permisos
- auditoría de operaciones
- backend como autoridad completa de precios y totales
- reintentos automáticos de red
- persistencia local de operaciones pendientes
- tests automáticos aislados
- migraciones formales de base de datos
- actualización automática del cliente
- firma digital del instalador
- monitoreo y alertas
- soporte multiempresa / multisucursal

---

## Despliegue actual

Backend:

```text
Render
https://campo-pos-api.onrender.com
```

Base de datos:

```text
Supabase PostgreSQL
```

Cliente:

```text
CampoBurgerPOS.exe
  ↓ HTTPS
FastAPI / Render
  ↓
Supabase PostgreSQL
```

---

## Rama de desarrollo actual

La arquitectura cliente-servidor y los cambios recientes se encuentran en:

```text
refactor/estructura-comercial
```

Actualmente esta rama contiene la versión más avanzada del proyecto y está por delante de `main`.

Antes de utilizar `main` como referencia para revisión técnica, conviene integrar esta rama cuando se considere estable.

---

## Objetivo

Convertir Campo POS en una solución reutilizable y mantenible para negocios gastronómicos, manteniendo una aplicación de caja rápida en Windows y centralizando lógica, datos y futuras funciones administrativas en el backend.
