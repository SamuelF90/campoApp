import os
import secrets
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal
from uuid import UUID

import psycopg2
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, ConfigDict, Field

from server.database import (
    CajaNoAbiertaError,
    CajaYaAbiertaError,
    DBManager,
    IdempotenciaConflictoError,
    VentaNoModificableError,
)

load_dotenv()

db = None


# ============================================================
# MODELOS
# ============================================================

class AbrirCajaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    monto_inicial: Decimal = Field(default=Decimal("0"), ge=0)


class CambiarMetodoPagoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metodo_pago: str = Field(min_length=1, max_length=40)


class VentaItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    producto_id: int = Field(gt=0)
    cant: int = Field(gt=0, le=1000)
    nombre: str = Field(min_length=1, max_length=200)
    subtotal: Decimal = Field(ge=0)

    precio_unitario: Decimal | None = Field(default=None, ge=0)

    # La GUI actual utiliza "obs".
    obs: str | None = Field(default=None, max_length=500)

    # Se mantiene también por compatibilidad futura.
    notas: str | None = Field(default=None, max_length=500)

    es_extra: bool = False
    parent_detalle_id: int | None = None
    peso_gramos: Decimal | None = Field(default=None, gt=0)
    precio_kg: Decimal | None = Field(default=None, ge=0)


class CrearVentaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operacion_id: UUID | None = None
    total: Decimal = Field(ge=0)
    metodo_pago: str = Field(min_length=1, max_length=40)
    items: list[VentaItem] = Field(min_length=1, max_length=100)
    tipo_entrega: str = Field(default="Mostrador", min_length=1, max_length=30)
    recargo_delivery: Decimal = Field(default=Decimal("0"), ge=0)


# ============================================================
# CICLO DE VIDA
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    global db

    db = DBManager()
    db.init_db()

    yield

    db.close()


app = FastAPI(
    title="Campo POS API",
    version="2.0.0",
    lifespan=lifespan,
)


api_key_header = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
)


def verificar_api_key(api_key: str | None = Depends(api_key_header)):
    clave_servidor = os.getenv("CAMPO_API_KEY")

    if not clave_servidor:
        raise HTTPException(
            status_code=500,
            detail="La API no está configurada correctamente.",
        )

    if not api_key or not secrets.compare_digest(api_key, clave_servidor):
        raise HTTPException(
            status_code=401,
            detail="API key inválida o ausente.",
            headers={"WWW-Authenticate": "ApiKey"},
        )


# ============================================================
# SISTEMA
# ============================================================

@app.get("/")
def inicio():
    return {
        "app": "Campo POS API",
        "version": "2.0.0",
        "status": "online",
    }


@app.get("/health")
def health():
    try:
        db.probar_conexion()

        return {
            "status": "ok",
        }

    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="No se pudo conectar con la base de datos.",
        ) from exc


# ============================================================
# PRODUCTOS
# ============================================================

@app.get("/productos", dependencies=[Depends(verificar_api_key)])
def obtener_productos(
    categoria: str = "TODOS",
    subcategoria: str = "TODOS",
):
    try:
        productos = db.obtener_productos_filtrados(
            categoria,
            subcategoria,
        )

        return [
            {
                "id": p[0],
                "nombre": p[1],
                "precio": p[2],
                "categoria": p[3],
                "subcategoria": p[4],
                "descripcion": p[5],
                "tipo_venta": p[6],
            }
            for p in productos
        ]

    except psycopg2.Error as exc:
        raise HTTPException(
            status_code=503,
            detail="No se pudo acceder a la base de datos.",
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="No se pudieron obtener los productos.",
        ) from exc


# ============================================================
# CAJA
# ============================================================

@app.get("/caja", dependencies=[Depends(verificar_api_key)])
def obtener_caja():
    try:
        return db.obtener_sesion_abierta()

    except psycopg2.Error as exc:
        raise HTTPException(
            status_code=503,
            detail="No se pudo acceder a la base de datos.",
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="No se pudo consultar la caja.",
        ) from exc


@app.get("/caja/resumen", dependencies=[Depends(verificar_api_key)])
def obtener_resumen_caja():
    try:
        return db.obtener_resumen_caja()

    except psycopg2.Error as exc:
        raise HTTPException(
            status_code=503,
            detail="No se pudo acceder a la base de datos.",
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="No se pudo obtener el resumen de caja.",
        ) from exc


@app.post("/caja/abrir", dependencies=[Depends(verificar_api_key)])
def abrir_caja(datos: AbrirCajaRequest):
    try:
        sesion_id = db.abrir_caja(
            datos.monto_inicial
        )

        return {
            "ok": True,
            "sesion_id": sesion_id,
        }

    except CajaYaAbiertaError as exc:
        raise HTTPException(status_code=409, detail="Ya existe una caja abierta.") from exc

    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    except psycopg2.Error as exc:
        raise HTTPException(
            status_code=503,
            detail="No se pudo acceder a la base de datos.",
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="No se pudo abrir la caja.",
        ) from exc


@app.post("/caja/cerrar", dependencies=[Depends(verificar_api_key)])
def cerrar_caja():
    try:
        sesion_id = db.cerrar_caja()

        return {
            "ok": True,
            "sesion_id": sesion_id,
        }

    except CajaNoAbiertaError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    except psycopg2.Error as exc:
        raise HTTPException(
            status_code=503,
            detail="No se pudo acceder a la base de datos.",
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="No se pudo cerrar la caja.",
        ) from exc


# ============================================================
# VENTAS
# ============================================================

# IMPORTANTE:
# Esta ruta debe declararse ANTES de /ventas/{venta_id}.
@app.get("/ventas/sesion-activa", dependencies=[Depends(verificar_api_key)])
def obtener_ventas_sesion_activa():
    try:
        return db.obtener_ventas_sesion_activa()

    except psycopg2.Error as exc:
        raise HTTPException(
            status_code=503,
            detail="No se pudo acceder a la base de datos.",
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="No se pudieron obtener las ventas del turno.",
        ) from exc


@app.post("/ventas", dependencies=[Depends(verificar_api_key)])
def crear_venta(datos: CrearVentaRequest):
    try:
        items = [
            item.model_dump(exclude_none=True)
            for item in datos.items
        ]

        venta_id = db.crear_venta(
            total=datos.total,
            metodo_pago=datos.metodo_pago,
            items=items,
            tipo_entrega=datos.tipo_entrega,
            recargo_delivery=datos.recargo_delivery,
            operacion_id=str(datos.operacion_id or uuid.uuid4()),
        )

        return {
            "ok": True,
            "venta_id": venta_id,
        }

    except IdempotenciaConflictoError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    except CajaNoAbiertaError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    except psycopg2.Error as exc:
        raise HTTPException(
            status_code=503,
            detail="No se pudo acceder a la base de datos.",
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="No se pudo registrar la venta.",
        ) from exc


@app.patch(
    "/ventas/{venta_id}/metodo-pago",
    dependencies=[Depends(verificar_api_key)],
)
def actualizar_metodo_pago(
    venta_id: int,
    datos: CambiarMetodoPagoRequest,
):
    try:
        db.actualizar_metodo_pago_venta(
            venta_id,
            datos.metodo_pago,
        )

        return {
            "ok": True,
            "venta_id": venta_id,
            "metodo_pago": datos.metodo_pago,
        }

    except VentaNoModificableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    except psycopg2.Error as exc:
        raise HTTPException(
            status_code=503,
            detail="No se pudo acceder a la base de datos.",
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="No se pudo modificar el método de pago.",
        ) from exc


@app.post("/ventas/{venta_id}/anular", dependencies=[Depends(verificar_api_key)])
def anular_venta(venta_id: int):
    try:
        db.anular_venta(
            venta_id
        )

        return {
            "ok": True,
            "venta_id": venta_id,
        }

    except VentaNoModificableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    except psycopg2.Error as exc:
        raise HTTPException(
            status_code=503,
            detail="No se pudo acceder a la base de datos.",
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="No se pudo anular la venta.",
        ) from exc


@app.get("/ventas/{venta_id}", dependencies=[Depends(verificar_api_key)])
def obtener_venta(venta_id: int):
    try:
        detalle = db.obtener_detalle_venta(
            venta_id
        )

        if not detalle:
            raise HTTPException(
                status_code=404,
                detail="Venta no encontrada.",
            )

        return {
            "venta_id": venta_id,
            "detalle": detalle,
        }

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="No se pudo obtener la venta.",
        ) from exc


# ============================================================
# REPORTES
# ============================================================

@app.get("/reportes/sesiones", dependencies=[Depends(verificar_api_key)])
def obtener_resumen_sesiones(
    limite: int = 100,
):
    try:
        return db.obtener_resumen_sesiones(
            limite
        )

    except psycopg2.Error as exc:
        raise HTTPException(
            status_code=503,
            detail="No se pudo acceder a la base de datos.",
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="No se pudo obtener el historial de sesiones.",
        ) from exc


@app.get("/reportes/semanal", dependencies=[Depends(verificar_api_key)])
def obtener_resumen_semanal(
    limite_semanas: int = 52,
):
    try:
        return db.obtener_resumen_semanal(
            limite_semanas
        )

    except psycopg2.Error as exc:
        raise HTTPException(
            status_code=503,
            detail="No se pudo acceder a la base de datos.",
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="No se pudo obtener el resumen semanal.",
        ) from exc


@app.get("/reportes/mensual", dependencies=[Depends(verificar_api_key)])
def obtener_resumen_mensual(
    limite_meses: int = 24,
):
    try:
        return db.obtener_resumen_mensual(
            limite_meses
        )

    except psycopg2.Error as exc:
        raise HTTPException(
            status_code=503,
            detail="No se pudo acceder a la base de datos.",
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="No se pudo obtener el resumen mensual.",
        ) from exc
