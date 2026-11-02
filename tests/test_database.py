from decimal import Decimal

import pytest
from fastapi import HTTPException

import server.api as api
from client.api_client import APIClient, APIError
from server.database import (
    CajaNoAbiertaError,
    DBManager,
    METODOS_PAGO_VALIDOS,
)


def test_metodo_pago_invalido_se_rechaza_sin_conexion():
    db = DBManager.__new__(DBManager)

    with pytest.raises(ValueError, match="Método de pago inválido"):
        db.crear_venta(
            total=Decimal("1000"),
            metodo_pago="Crédito",
            items=[],
        )


def test_metodos_de_pago_configurados_son_validos():
    assert "Efectivo" in METODOS_PAGO_VALIDOS
    assert "POS / Tarjeta" in METODOS_PAGO_VALIDOS


def test_venta_requiere_producto_id():
    with pytest.raises(ValueError):
        api.CrearVentaRequest(
            total="1000",
            metodo_pago="Efectivo",
            items=[
                {
                    "cant": 1,
                    "nombre": "Hamburguesa",
                    "subtotal": "1000",
                }
            ],
        )


def test_api_key_valida(monkeypatch):
    monkeypatch.setenv("CAMPO_API_KEY", "clave-de-prueba")

    assert api.verificar_api_key("clave-de-prueba") is None


def test_api_key_invalida_devuelve_401(monkeypatch):
    monkeypatch.setenv("CAMPO_API_KEY", "clave-de-prueba")

    with pytest.raises(HTTPException) as error:
        api.verificar_api_key("otra-clave")

    assert error.value.status_code == 401


def test_venta_sin_caja_devuelve_conflicto(monkeypatch):
    class DBFalsa:
        def crear_venta(self, **kwargs):
            raise CajaNoAbiertaError("No hay caja abierta.")

    monkeypatch.setattr(api, "db", DBFalsa())

    datos = api.CrearVentaRequest(
        total="1000",
        metodo_pago="Efectivo",
        items=[
            {
                "producto_id": 1,
                "cant": 1,
                "nombre": "Hamburguesa",
                "subtotal": "1000",
            }
        ],
    )

    with pytest.raises(HTTPException) as error:
        api.crear_venta(datos)

    assert error.value.status_code == 409


def test_api_client_envia_api_key(monkeypatch):
    llamadas = []

    class Respuesta:
        ok = True
        status_code = 200

        @staticmethod
        def json():
            return {"status": "ok"}

    def request(method, url, **kwargs):
        llamadas.append((method, url, kwargs))
        return Respuesta()

    monkeypatch.setattr("client.api_client.requests.request", request)

    cliente = APIClient(
        base_url="http://testserver",
        api_key="clave-de-prueba",
    )
    cliente.health()

    assert llamadas[0][2]["headers"]["X-API-Key"] == "clave-de-prueba"


def test_api_error_conserva_status_http(monkeypatch):
    class Respuesta:
        ok = False
        status_code = 409

        @staticmethod
        def json():
            return {"detail": "Conflicto"}

    monkeypatch.setattr(
        "client.api_client.requests.request",
        lambda *args, **kwargs: Respuesta(),
    )

    cliente = APIClient(
        base_url="http://testserver",
        api_key="clave-de-prueba",
    )

    with pytest.raises(APIError) as error:
        cliente.health()

    assert error.value.status_code == 409


def test_venta_usa_precio_del_catalogo_y_no_del_cliente():
    class CursorFalso:
        def __init__(self):
            self.rows = []
            self.detalle_params = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def execute(self, query, params=None):
            if "SELECT id, nombre, precio, tipo_venta" in query:
                self.rows = [
                    (
                        1,
                        "Hamburguesa real",
                        Decimal("1000"),
                        "unidad",
                        "Hamburguesas",
                        "General",
                    )
                ]
            elif "SELECT id, operacion_hash" in query:
                self.rows = []
            elif "FROM sesiones_caja" in query:
                self.rows = [(7,)]
            elif "INSERT INTO ventas" in query:
                self.rows = [(41,)]
            elif "INSERT INTO detalle_ventas" in query:
                self.detalle_params = params
                self.rows = []
            else:
                self.rows = []

        def fetchone(self):
            return self.rows[0] if self.rows else None

        def fetchall(self):
            return self.rows

    class ConexionFalsa:
        closed = False

        def __init__(self):
            self.cursor_falso = CursorFalso()

        def cursor(self):
            return self.cursor_falso

        def commit(self):
            pass

        def rollback(self):
            pass

    class PoolFalso:
        def __init__(self):
            self.conexion = ConexionFalsa()

        def getconn(self):
            return self.conexion

        def putconn(self, conn, close=False):
            pass

    db = DBManager.__new__(DBManager)
    db._pool_cerrado = False
    db._pool = PoolFalso()

    venta_id = db.crear_venta(
        total=Decimal("1000"),
        metodo_pago="Efectivo",
        operacion_id="11111111-1111-1111-1111-111111111111",
        items=[
            {
                "producto_id": 1,
                "cant": 1,
                "nombre": "Producto manipulado",
                "precio_unitario": Decimal("1"),
                "subtotal": Decimal("1"),
            }
        ],
    )

    params = db._pool.conexion.cursor_falso.detalle_params

    assert venta_id == 41
    assert params[1] == "Hamburguesa real"
    assert params[3] == Decimal("1000")
    assert params[4] == Decimal("1000")
