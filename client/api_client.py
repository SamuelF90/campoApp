import os
from datetime import date, datetime
from decimal import Decimal

import requests

from client.config import cargar_configuracion

# ============================================================
# EXCEPCIONES
# ============================================================

class APIError(Exception):
    """
    Error controlado producido al comunicarnos con Campo POS API.
    """
    def __init__(self, mensaje, status_code=None):
        super().__init__(mensaje)
        self.status_code = status_code


# ============================================================
# CLIENTE DE CAMPO POS API
# ============================================================

class APIClient:
    """
    Comunicación entre Campo POS y FastAPI.

    Esta clase mantiene, siempre que sea posible, la misma
    interfaz que utilizaba DBManager para facilitar la migración.
    """

    def __init__(
        self,
        base_url=None,
        timeout=90,
        api_key=None,
    ):
        configuracion = cargar_configuracion()

        if base_url is None:
            base_url = configuracion["api_url"]

        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.api_key = (
            api_key
            or os.getenv("CAMPO_API_KEY")
            or configuracion.get("api_key")
        )


    # ========================================================
    # UTILIDADES INTERNAS
    # ========================================================

    def _json_safe(self, valor):
        """
        Convierte objetos no serializables por requests.

        Decimal se envía como texto para no perder precisión.
        """

        if isinstance(valor, Decimal):
            return str(valor)

        if isinstance(valor, dict):
            return {
                clave: self._json_safe(valor_interno)
                for clave, valor_interno in valor.items()
            }

        if isinstance(valor, (list, tuple)):
            return [
                self._json_safe(elemento)
                for elemento in valor
            ]

        return valor


    @staticmethod
    def _parse_datetime(valor):
        """
        Convierte una fecha ISO recibida por JSON nuevamente
        en datetime para mantener compatibilidad con el POS.
        """

        if valor is None or isinstance(valor, datetime):
            return valor

        if isinstance(valor, str):
            try:
                return datetime.fromisoformat(valor)
            except ValueError:
                return valor

        return valor


    @staticmethod
    def _parse_date(valor):
        """
        Convierte YYYY-MM-DD recibido por JSON a date.
        """

        if valor is None:
            return None

        if isinstance(valor, datetime):
            return valor.date()

        if isinstance(valor, date):
            return valor

        if isinstance(valor, str):
            try:
                return date.fromisoformat(valor)
            except ValueError:
                return valor

        return valor


    def _request(self, method, endpoint, **kwargs):
        """
        Ejecuta una petición HTTP contra Campo POS API.
        """

        url = f"{self.base_url}{endpoint}"

        if "json" in kwargs:
            kwargs["json"] = self._json_safe(
                kwargs["json"]
            )

        headers = dict(kwargs.get("headers") or {})
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        kwargs["headers"] = headers

        try:
            response = requests.request(
                method,
                url,
                timeout=self.timeout,
                **kwargs,
            )

        except requests.ConnectionError as exc:
            raise APIError(
                "No se pudo conectar con el servidor."
            ) from exc

        except requests.Timeout as exc:
            raise APIError(
                "El servidor tardó demasiado en responder."
            ) from exc

        except requests.RequestException as exc:
            raise APIError(
                "Ocurrió un error de comunicación con el servidor."
            ) from exc

        try:
            data = response.json()
        except ValueError:
            data = None

        if not response.ok:

            if isinstance(data, dict):
                mensaje = data.get(
                    "detail",
                    f"Error HTTP {response.status_code}",
                )
            else:
                mensaje = f"Error HTTP {response.status_code}"

            raise APIError(mensaje, status_code=response.status_code)

        return data


    # ========================================================
    # ESTADO
    # ========================================================

    def health(self):
        return self._request(
            "GET",
            "/health",
        )


    # ========================================================
    # PRODUCTOS
    # ========================================================

    def obtener_productos(
        self,
        categoria="TODOS",
        subcategoria="TODOS",
    ):
        return self._request(
            "GET",
            "/productos",
            params={
                "categoria": categoria,
                "subcategoria": subcategoria,
            },
        )


    def obtener_productos_filtrados(
        self,
        categoria="TODOS",
        subcategoria="TODOS",
    ):
        """
        Convierte el JSON de productos al formato histórico
        de tuplas utilizado por gui.py.

        (
            id,
            nombre,
            precio,
            categoria,
            subcategoria,
            descripcion,
            tipo_venta,
        )
        """

        productos = self.obtener_productos(
            categoria,
            subcategoria,
        )

        return [
            (
                producto["id"],
                producto["nombre"],
                producto["precio"],
                producto["categoria"],
                producto["subcategoria"],
                producto["descripcion"],
                producto["tipo_venta"],
            )
            for producto in productos
        ]


    # ========================================================
    # CAJA
    # ========================================================

    def obtener_caja(self):
        sesion = self._request(
            "GET",
            "/caja",
        )

        if not sesion:
            return None

        sesion["fecha_apertura"] = self._parse_datetime(
            sesion.get("fecha_apertura")
        )

        sesion["fecha_cierre"] = self._parse_datetime(
            sesion.get("fecha_cierre")
        )

        return sesion


    def obtener_sesion_abierta(self):
        return self.obtener_caja()


    def abrir_caja(self, monto_inicial=0):
        resultado = self._request(
            "POST",
            "/caja/abrir",
            json={
                "monto_inicial": monto_inicial,
            },
        )

        return resultado["sesion_id"]


    def cerrar_caja(self):
        resultado = self._request(
            "POST",
            "/caja/cerrar",
        )

        return resultado["sesion_id"]


    def obtener_resumen_caja(self):
        resumen = self._request(
            "GET",
            "/caja/resumen",
        )

        if resumen and resumen.get("fecha_apertura"):
            resumen["fecha_apertura"] = self._parse_datetime(
                resumen["fecha_apertura"]
            )

        return resumen


    # ========================================================
    # VENTAS
    # ========================================================

    def crear_venta(
        self,
        total,
        metodo_pago,
        items,
        tipo_entrega="Mostrador",
        recargo_delivery=0,
        operacion_id=None,
    ):
        resultado = self._request(
            "POST",
            "/ventas",
            json={
                "operacion_id": operacion_id,
                "total": total,
                "metodo_pago": metodo_pago,
                "items": items,
                "tipo_entrega": tipo_entrega,
                "recargo_delivery": recargo_delivery,
            },
        )

        return resultado["venta_id"]


    def obtener_ventas_sesion_activa(self):
        ventas = self._request(
            "GET",
            "/ventas/sesion-activa",
        )

        # El DBManager original devolvía tuplas.
        return [
            tuple(venta)
            for venta in ventas
        ]


    def obtener_venta(self, venta_id):
        return self._request(
            "GET",
            f"/ventas/{venta_id}",
        )


    def obtener_detalle_venta(self, venta_id):
        resultado = self.obtener_venta(venta_id)

        if not resultado:
            return None

        return resultado.get("detalle")


    def actualizar_metodo_pago_venta(
        self,
        ticket_id,
        nuevo_metodo,
    ):
        self._request(
            "PATCH",
            f"/ventas/{ticket_id}/metodo-pago",
            json={
                "metodo_pago": nuevo_metodo,
            },
        )

        return True


    def anular_venta(self, ticket_id):
        self._request(
            "POST",
            f"/ventas/{ticket_id}/anular",
        )

        return True


    # ========================================================
    # REPORTES
    # ========================================================

    def obtener_resumen_sesiones(self, limite=100):
        filas = self._request(
            "GET",
            "/reportes/sesiones",
            params={
                "limite": limite,
            },
        )

        for fila in filas:
            fila["fecha_apertura"] = self._parse_datetime(
                fila.get("fecha_apertura")
            )

            fila["fecha_cierre"] = self._parse_datetime(
                fila.get("fecha_cierre")
            )

        return filas


    def obtener_resumen_semanal(
        self,
        limite_semanas=52,
    ):
        filas = self._request(
            "GET",
            "/reportes/semanal",
            params={
                "limite_semanas": limite_semanas,
            },
        )

        for fila in filas:
            fila["semana_inicio"] = self._parse_date(
                fila.get("semana_inicio")
            )

            fila["semana_fin"] = self._parse_date(
                fila.get("semana_fin")
            )

        return filas


    def obtener_resumen_mensual(
        self,
        limite_meses=24,
    ):
        filas = self._request(
            "GET",
            "/reportes/mensual",
            params={
                "limite_meses": limite_meses,
            },
        )

        for fila in filas:
            fila["mes"] = self._parse_date(
                fila.get("mes")
            )

        return filas


    # ========================================================
    # CIERRE DEL CLIENTE
    # ========================================================

    def close(self):
        """
        Compatibilidad con DBManager.

        APIClient no mantiene un pool PostgreSQL local.
        """
        pass
