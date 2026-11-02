import os
import hashlib
import json
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation

import psycopg2
from psycopg2 import pool
from psycopg2.errors import UniqueViolation
from dotenv import load_dotenv

load_dotenv()


METODOS_PAGO_VALIDOS = (
    "Efectivo",
    "POS / Tarjeta",
    "Transferencia",
)

TIPOS_ENTREGA_VALIDOS = (
    "Mostrador",
    "Delivery",
    "Retiro",
)


class CajaNoAbiertaError(RuntimeError):
    """Se intenta operar sin una sesión de caja abierta."""


class CajaYaAbiertaError(RuntimeError):
    """Se intenta abrir una segunda sesión de caja."""


class VentaNoModificableError(RuntimeError):
    """La venta no existe o ya no puede modificarse."""


class IdempotenciaConflictoError(RuntimeError):
    """La misma operación fue reutilizada con datos distintos."""


class DBManager:
    """
    Acceso PostgreSQL/Supabase para Campo Burger.

    Usa ThreadedConnectionPool para reutilizar conexiones entre consultas
    y permitir operaciones desde hilos de trabajo sin congelar Tkinter.
    """

    TABLAS_REQUERIDAS = {
        "productos": {
            "id",
            "nombre",
            "precio",
            "categoria",
            "subcategoria",
            "descripcion",
            "tipo_venta",
        },
        "sesiones_caja": {
            "id",
            "fecha_apertura",
            "fecha_cierre",
            "monto_inicial",
            "estado",
        },
        "ventas": {
            "id",
            "sesion_id",
            "fecha_hora",
            "monto_total",
            "metodo_pago",
            "estado",
            "tipo_entrega",
            "recargo_delivery",
            "operacion_id",
            "operacion_hash",
        },
        "detalle_ventas": {
            "id",
            "venta_id",
            "producto_nombre",
            "cantidad",
            "precio_unitario",
            "subtotal",
            "notas",
            "es_extra",
            "parent_detalle_id",
            "peso_gramos",
            "precio_kg",
        },
    }

    def __init__(self):
        self._validar_configuracion()
        self._pool_cerrado = False
        self._pool = self._crear_pool()

    # ==================================================================
    # 1. CONEXIÓN / POOL
    # ==================================================================

    def _validar_configuracion(self):
        requeridas = ("DB_HOST", "DB_USER", "DB_PASSWORD")
        faltantes = [nombre for nombre in requeridas if not os.getenv(nombre)]
        if faltantes:
            raise RuntimeError(
                "Faltan variables obligatorias en .env: " + ", ".join(faltantes)
            )

    def _connection_kwargs(self):
        return {
            "host": os.getenv("DB_HOST"),
            "port": os.getenv("DB_PORT", "5432"),
            "database": os.getenv("DB_NAME", "postgres"),
            "user": os.getenv("DB_USER"),
            "password": os.getenv("DB_PASSWORD"),
            "sslmode": os.getenv("DB_SSLMODE", "require"),
            "connect_timeout": int(os.getenv("DB_CONNECT_TIMEOUT", "10")),
            "application_name": "campo_burger_pos",
        }

    def _crear_pool(self):
        minconn = max(1, int(os.getenv("DB_POOL_MIN", "1")))
        maxconn = max(minconn, int(os.getenv("DB_POOL_MAX", "5")))
        return pool.ThreadedConnectionPool(
            minconn,
            maxconn,
            **self._connection_kwargs(),
        )

    def get_connection(self):
        """
        Crea una conexión independiente.

        Normalmente la aplicación debe usar los métodos de DBManager, que
        trabajan con el pool. Se conserva por compatibilidad/diagnóstico.
        """
        return psycopg2.connect(**self._connection_kwargs())

    @contextmanager
    def _connection(self):
        if self._pool_cerrado:
            raise RuntimeError("El pool de conexiones ya fue cerrado.")

        conn = self._pool.getconn()
        cerrar_al_devolver = False

        try:
            if conn.closed:
                cerrar_al_devolver = True
                raise psycopg2.InterfaceError("La conexión del pool está cerrada.")

            # Se aplica dentro de la misma transacción que usará la consulta.
            # Esto es compatible tanto con conexiones directas como con poolers.
            timezone = os.getenv("DB_TIMEZONE", "America/Asuncion")
            with conn.cursor() as cur:
                cur.execute("SET TIME ZONE %s;", (timezone,))

            yield conn
            conn.commit()

        except Exception:
            try:
                if not conn.closed:
                    conn.rollback()
            except Exception:
                cerrar_al_devolver = True
            raise

        finally:
            try:
                self._pool.putconn(conn, close=cerrar_al_devolver or bool(conn.closed))
            except Exception:
                pass

    def probar_conexion(self):
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        current_database(),
                        current_user,
                        current_setting('TimeZone');
                    """
                )
                database, usuario, timezone = cur.fetchone()

        return {
            "database": database,
            "usuario": usuario,
            "timezone": timezone,
        }

    def init_db(self):
        """Valida la estructura; la propia consulta confirma la conexión."""
        self.verificar_estructura()
        return True

    def close(self):
        if not self._pool_cerrado:
            self._pool.closeall()
            self._pool_cerrado = True

    # ==================================================================
    # 2. ESTRUCTURA
    # ==================================================================

    def verificar_estructura(self):
        nombres_tablas = tuple(self.TABLAS_REQUERIDAS.keys())

        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT table_name, column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name IN %s
                    ORDER BY table_name, ordinal_position;
                    """,
                    (nombres_tablas,),
                )

                encontradas = {}
                for tabla, columna in cur.fetchall():
                    encontradas.setdefault(tabla, set()).add(columna)

        errores = []
        for tabla, requeridas in self.TABLAS_REQUERIDAS.items():
            if tabla not in encontradas:
                errores.append(f"Falta la tabla public.{tabla}")
                continue

            faltantes = sorted(requeridas - encontradas[tabla])
            if faltantes:
                errores.append(
                    f"En public.{tabla} faltan columnas: " + ", ".join(faltantes)
                )

        if errores:
            raise RuntimeError(
                "La estructura de PostgreSQL no coincide con la esperada:\n- "
                + "\n- ".join(errores)
            )

        return True

    def crear_tablas_si_no_existen(self):
        """Compatibilidad con versiones anteriores; no altera el esquema."""
        return self.verificar_estructura()

    # ==================================================================
    # 3. VALIDACIONES
    # ==================================================================

    @staticmethod
    def _decimal(valor, nombre="monto"):
        try:
            resultado = Decimal(str(valor))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError(f"{nombre} no es un número válido.") from exc

        if not resultado.is_finite():
            raise ValueError(f"{nombre} no es un número válido.")
        return resultado

    @classmethod
    def _decimal_no_negativo(cls, valor, nombre="monto"):
        resultado = cls._decimal(valor, nombre)
        if resultado < 0:
            raise ValueError(f"{nombre} no puede ser negativo.")
        return resultado

    @staticmethod
    def _validar_metodo_pago(metodo_pago):
        metodo = str(metodo_pago or "").strip()
        if metodo not in METODOS_PAGO_VALIDOS:
            raise ValueError(
                "Método de pago inválido. Use: " + ", ".join(METODOS_PAGO_VALIDOS)
            )
        return metodo

    @staticmethod
    def _validar_tipo_entrega(tipo_entrega):
        tipo = str(tipo_entrega or "").strip()
        if tipo not in TIPOS_ENTREGA_VALIDOS:
            raise ValueError(
                "Tipo de entrega inválido. Use: " + ", ".join(TIPOS_ENTREGA_VALIDOS)
            )
        return tipo

    # ==================================================================
    # 4. SESIONES DE CAJA
    # ==================================================================

    def obtener_sesion_abierta(self):
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, fecha_apertura, fecha_cierre, monto_inicial, estado
                    FROM sesiones_caja
                    WHERE estado = 'ABIERTA'
                    ORDER BY fecha_apertura DESC, id DESC
                    LIMIT 1;
                    """
                )
                fila = cur.fetchone()

        if not fila:
            return None

        return {
            "id": fila[0],
            "fecha_apertura": fila[1],
            "fecha_cierre": fila[2],
            "monto_inicial": fila[3],
            "estado": fila[4],
        }

    def abrir_caja(self, monto_inicial=0):
        monto = self._decimal_no_negativo(monto_inicial, "monto inicial")

        try:
            with self._connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id
                        FROM sesiones_caja
                        WHERE estado = 'ABIERTA'
                        LIMIT 1
                        FOR UPDATE;
                        """
                    )
                    existente = cur.fetchone()

                    if existente:
                        raise CajaYaAbiertaError(
                            f"Ya existe una caja abierta: sesión #{existente[0]}."
                        )

                    cur.execute(
                        """
                        INSERT INTO sesiones_caja
                            (fecha_apertura, fecha_cierre, monto_inicial, estado)
                        VALUES (NOW(), NULL, %s, 'ABIERTA')
                        RETURNING id;
                        """,
                        (monto,),
                    )
                    sesion_id = cur.fetchone()[0]

            return sesion_id

        except UniqueViolation as exc:
            raise CajaYaAbiertaError("Ya existe una sesión de caja abierta.") from exc

    def cerrar_caja(self):
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id
                    FROM sesiones_caja
                    WHERE estado = 'ABIERTA'
                    ORDER BY fecha_apertura DESC, id DESC
                    LIMIT 1
                    FOR UPDATE;
                    """
                )
                fila = cur.fetchone()

                if not fila:
                    raise CajaNoAbiertaError("No existe una sesión de caja abierta.")

                sesion_id = fila[0]

                cur.execute(
                    """
                    UPDATE sesiones_caja
                    SET estado = 'CERRADA', fecha_cierre = NOW()
                    WHERE id = %s AND estado = 'ABIERTA';
                    """,
                    (sesion_id,),
                )

        return sesion_id

    # ==================================================================
    # 5. PRODUCTOS
    # ==================================================================

    def obtener_categorias_principales(self):
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT categoria
                    FROM productos
                    WHERE categoria IS NOT NULL
                    AND TRIM(categoria) <> ''
                    ORDER BY categoria;
                    """
                )
                return [fila[0] for fila in cur.fetchall()]


    def obtener_subcategorias_por_categoria(self, categoria):
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT subcategoria
                    FROM productos
                    WHERE UPPER(categoria) = UPPER(%s)
                    AND subcategoria IS NOT NULL
                    AND TRIM(subcategoria) <> ''
                    ORDER BY subcategoria;
                    """,
                    (categoria,),
                )

                return [
                    fila[0]
                    for fila in cur.fetchall()
                ]


    def obtener_productos_filtrados(
        self,
        categoria="TODOS",
        subcategoria="TODOS",
    ):
        query = """
            SELECT
                id,
                nombre,
                precio,
                categoria,
                subcategoria,
                descripcion,
                tipo_venta
            FROM productos
            WHERE 1 = 1
        """

        params = []

        categoria = str(
            categoria or "TODOS"
        ).strip()

        subcategoria = str(
            subcategoria or "TODOS"
        ).strip()

        if categoria.upper() != "TODOS":
            query += """
                AND UPPER(categoria) = UPPER(%s)
            """
            params.append(categoria)

        if subcategoria.upper() != "TODOS":
            query += """
                AND UPPER(subcategoria) = UPPER(%s)
            """
            params.append(subcategoria)

        query += """
            ORDER BY
                categoria,
                subcategoria NULLS LAST,
                nombre;
        """

        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    query,
                    tuple(params),
                )

                return cur.fetchall()


    def obtener_producto_por_id(
        self,
        producto_id,
    ):
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        nombre,
                        precio,
                        categoria,
                        subcategoria,
                        descripcion,
                        tipo_venta
                    FROM productos
                    WHERE id = %s;
                    """,
                    (producto_id,),
                )

                return cur.fetchone()


    def agregar_producto(
        self,
        nombre,
        precio,
        categoria,
        subcategoria="",
        descripcion="",
        tipo_venta="unidad",
    ):
        nombre = str(
            nombre or ""
        ).strip()

        categoria = str(
            categoria or ""
        ).strip()

        subcategoria = str(
            subcategoria or ""
        ).strip()

        descripcion = str(
            descripcion or ""
        ).strip()

        tipo_venta = str(
            tipo_venta or "unidad"
        ).strip().lower()

        precio = self._decimal_no_negativo(
            precio,
            "precio",
        )

        if not nombre:
            raise ValueError(
                "El nombre del producto es obligatorio."
            )

        if not categoria:
            raise ValueError(
                "La categoría es obligatoria."
            )

        if tipo_venta not in (
            "unidad",
            "peso",
        ):
            raise ValueError(
                "El tipo de venta debe ser "
                "'unidad' o 'peso'."
            )

        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO productos
                        (
                            nombre,
                            precio,
                            categoria,
                            subcategoria,
                            descripcion,
                            tipo_venta
                        )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s
                    )
                    RETURNING id;
                    """,
                    (
                        nombre,
                        precio,
                        categoria,
                        subcategoria,
                        descripcion,
                        tipo_venta,
                    ),
                )

                return cur.fetchone()[0]


    def actualizar_producto(
        self,
        producto_id,
        nombre,
        precio,
        categoria,
        subcategoria="",
        descripcion="",
        tipo_venta="unidad",
    ):
        nombre = str(
            nombre or ""
        ).strip()

        categoria = str(
            categoria or ""
        ).strip()

        subcategoria = str(
            subcategoria or ""
        ).strip()

        descripcion = str(
            descripcion or ""
        ).strip()

        tipo_venta = str(
            tipo_venta or "unidad"
        ).strip().lower()

        precio = self._decimal_no_negativo(
            precio,
            "precio",
        )

        if not nombre:
            raise ValueError(
                "El nombre del producto es obligatorio."
            )

        if not categoria:
            raise ValueError(
                "La categoría es obligatoria."
            )

        if tipo_venta not in (
            "unidad",
            "peso",
        ):
            raise ValueError(
                "El tipo de venta debe ser "
                "'unidad' o 'peso'."
            )

        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE productos
                    SET
                        nombre = %s,
                        precio = %s,
                        categoria = %s,
                        subcategoria = %s,
                        descripcion = %s,
                        tipo_venta = %s
                    WHERE id = %s;
                    """,
                    (
                        nombre,
                        precio,
                        categoria,
                        subcategoria,
                        descripcion,
                        tipo_venta,
                        producto_id,
                    ),
                )

                if cur.rowcount != 1:
                    raise ValueError(
                        f"No existe el producto "
                        f"#{producto_id}."
                    )


    def eliminar_producto(
        self,
        producto_id,
    ):
        producto = self.obtener_producto_por_id(
            producto_id
        )

        if not producto:
            raise ValueError(
                f"No existe el producto "
                f"#{producto_id}."
            )

        nombre = producto[1]

        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM detalle_ventas
                        WHERE LOWER(
                            TRIM(producto_nombre)
                        ) = LOWER(
                            TRIM(%s)
                        )
                    );
                    """,
                    (nombre,),
                )

                vendido = cur.fetchone()[0]

                if vendido:
                    raise ValueError(
                        "El producto ya posee ventas "
                        "históricas y no puede eliminarse."
                    )

                cur.execute(
                    """
                    DELETE FROM productos
                    WHERE id = %s;
                    """,
                    (producto_id,),
                )
    # ==================================================================
    # 6. VENTAS
    # ==================================================================

    def crear_venta(
        self,
        total,
        metodo_pago,
        items,
        tipo_entrega="Mostrador",
        recargo_delivery=0,
        operacion_id=None,
    ):
        metodo_pago = self._validar_metodo_pago(metodo_pago)
        tipo_entrega = self._validar_tipo_entrega(tipo_entrega)
        total = self._decimal_no_negativo(total, "total")
        recargo = self._decimal_no_negativo(
            recargo_delivery,
            "recargo delivery"
        )

        operacion_id = str(operacion_id or "").strip()
        if not operacion_id:
            raise ValueError("Falta operacion_id para registrar la venta.")

        if not items:
            raise ValueError(
                "La venta debe contener al menos un producto."
            )

        items_normalizados = []

        for posicion, item in enumerate(items, start=1):

            # --------------------------------------------------------------
            # Producto
            # --------------------------------------------------------------
            try:
                producto_id = int(item["producto_id"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"Producto inválido en el ítem {posicion}."
                ) from exc

            if producto_id <= 0:
                raise ValueError(
                    f"Producto inválido en el ítem {posicion}."
                )

            # --------------------------------------------------------------
            # Cantidad
            # --------------------------------------------------------------
            try:
                cantidad = int(item["cant"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"Cantidad inválida en el ítem {posicion}."
                ) from exc

            if cantidad <= 0:
                raise ValueError(
                    f"La cantidad del ítem {posicion} "
                    "debe ser mayor que cero."
                )

            # --------------------------------------------------------------
            # Nombre
            # --------------------------------------------------------------
            nombre = str(
                item.get("nombre") or ""
            ).strip()

            if not nombre:
                raise ValueError(
                    f"Falta el nombre del producto "
                    f"en el ítem {posicion}."
                )

            # --------------------------------------------------------------
            # Subtotal
            # --------------------------------------------------------------
            subtotal = self._decimal_no_negativo(
                item.get("subtotal"),
                f"subtotal del ítem {posicion}",
            )

            # El precio unitario definitivo se obtiene desde productos dentro
            # de la transacción. El valor recibido del cliente no es fuente
            # de verdad.
            precio_unitario = None

            # --------------------------------------------------------------
            # PRODUCTOS VENDIDOS POR PESO
            # --------------------------------------------------------------
            peso_gramos = item.get("peso_gramos")
            precio_kg = item.get("precio_kg")

            # El peso identifica una venta por peso. El precio por kilo del
            # cliente es opcional y, si llega, no se usa como fuente de verdad.
            if peso_gramos is None and precio_kg is None:
                peso_gramos = None
                precio_kg = None

            else:
                if peso_gramos is None:
                    raise ValueError(
                        f"El ítem {posicion} tiene un precio por kilo "
                        "pero no tiene peso."
                    )

                peso_gramos = self._decimal(
                    peso_gramos,
                    f"peso en gramos del ítem {posicion}",
                )

                if precio_kg is not None:
                    precio_kg = self._decimal_no_negativo(
                        precio_kg,
                        f"precio por kilo del ítem {posicion}",
                    )

                if peso_gramos <= 0:
                    raise ValueError(
                        f"El peso del ítem {posicion} "
                        "debe ser mayor que cero."
                    )

            # --------------------------------------------------------------
            # Notas / extras
            # --------------------------------------------------------------
            notas = item.get("obs")

            if notas is None:
                notas = item.get("notas")

            notas = (
                str(notas).strip()
                if notas
                else None
            )

            es_extra = bool(
                item.get("es_extra", False)
            )

            parent_detalle_id = item.get(
                "parent_detalle_id"
            )

            items_normalizados.append(
                {
                    "producto_id": producto_id,
                    "cantidad": cantidad,
                    "nombre": nombre,
                    "precio_unitario": precio_unitario,
                    "subtotal": subtotal,
                    "notas": notas,
                    "es_extra": es_extra,
                    "parent_detalle_id": parent_detalle_id,
                    "peso_gramos": peso_gramos,
                    "precio_kg": precio_kg,
                }
            )

        # --------------------------------------------------------------
        # Guardar venta
        # --------------------------------------------------------------
        with self._connection() as conn:
            with conn.cursor() as cur:

                # El catálogo se consulta dentro de la misma transacción que
                # guarda la venta, para que el cliente no pueda fijar precios.
                producto_ids = [
                    item["producto_id"]
                    for item in items_normalizados
                ]
                cur.execute(
                    """
                    SELECT id, nombre, precio, tipo_venta, categoria, subcategoria
                    FROM productos
                    WHERE id = ANY(%s);
                    """,
                    (producto_ids,),
                )
                productos = {
                    fila[0]: fila
                    for fila in cur.fetchall()
                }

                for posicion, item in enumerate(items_normalizados, start=1):
                    producto = productos.get(item["producto_id"])

                    if producto is None:
                        raise ValueError(
                            f"No existe el producto #{item['producto_id']} "
                            f"del ítem {posicion}."
                        )

                    _, nombre, precio, tipo_venta, categoria, subcategoria = producto
                    precio = self._decimal_no_negativo(
                        precio,
                        f"precio del producto #{item['producto_id']}",
                    )

                    if tipo_venta == "unidad":
                        if (
                            item["peso_gramos"] is not None
                            or item["precio_kg"] is not None
                        ):
                            raise ValueError(
                                f"El producto #{item['producto_id']} "
                                "no se vende por peso."
                            )

                        subtotal = precio * Decimal(item["cantidad"])
                        precio_unitario = precio

                    elif tipo_venta == "peso":
                        if item["peso_gramos"] is None:
                            raise ValueError(
                                f"El producto #{item['producto_id']} "
                                "requiere peso en gramos."
                            )

                        if item["cantidad"] != 1:
                            raise ValueError(
                                "Los productos vendidos por peso deben "
                                "tener cantidad 1."
                            )

                        subtotal = (
                            precio
                            * item["peso_gramos"]
                            / Decimal("1000")
                        )
                        precio_unitario = subtotal
                        item["precio_kg"] = precio

                    else:
                        raise ValueError(
                            f"Tipo de venta inválido para el producto "
                            f"#{item['producto_id']}."
                        )

                    item["nombre"] = nombre
                    item["precio_unitario"] = precio_unitario
                    item["subtotal"] = subtotal
                    item["es_extra"] = (
                        str(categoria or "").strip().upper() == "AGREGADOS"
                        or str(subcategoria or "").strip().upper() == "EXTRAS"
                    )

                esperado = sum(
                    (item["subtotal"] for item in items_normalizados),
                    Decimal("0"),
                ) + recargo

                if total != esperado:
                    raise ValueError(
                        "El total recibido no coincide con los precios "
                        "vigentes del catálogo."
                    )

                contenido_operacion = {
                    "total": str(total),
                    "metodo_pago": metodo_pago,
                    "tipo_entrega": tipo_entrega,
                    "recargo_delivery": str(recargo),
                    "items": [
                        {
                            "producto_id": item["producto_id"],
                            "cantidad": item["cantidad"],
                            "nombre": item["nombre"],
                            "precio_unitario": str(item["precio_unitario"]),
                            "subtotal": str(item["subtotal"]),
                            "notas": item["notas"],
                            "es_extra": item["es_extra"],
                            "parent_detalle_id": item["parent_detalle_id"],
                            "peso_gramos": (
                                str(item["peso_gramos"])
                                if item["peso_gramos"] is not None
                                else None
                            ),
                            "precio_kg": (
                                str(item["precio_kg"])
                                if item["precio_kg"] is not None
                                else None
                            ),
                        }
                        for item in items_normalizados
                    ],
                }

                operacion_hash = hashlib.sha256(
                    json.dumps(
                        contenido_operacion,
                        sort_keys=True,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()

                cur.execute(
                    """
                    SELECT id, operacion_hash
                    FROM ventas
                    WHERE operacion_id = %s;
                    """,
                    (operacion_id,),
                )
                venta_existente = cur.fetchone()

                if venta_existente:
                    if venta_existente[1] != operacion_hash:
                        raise IdempotenciaConflictoError(
                            "La operación ya fue utilizada con datos distintos."
                        )
                    return venta_existente[0]

                cur.execute(
                    """
                    SELECT id
                    FROM sesiones_caja
                    WHERE estado = 'ABIERTA'
                    ORDER BY fecha_apertura DESC, id DESC
                    LIMIT 1
                    FOR UPDATE;
                    """
                )

                fila_sesion = cur.fetchone()

                if not fila_sesion:
                    raise CajaNoAbiertaError(
                        "No puede registrar una venta porque "
                        "no hay una caja abierta."
                    )

                sesion_id = fila_sesion[0]

                cur.execute(
                    """
                    INSERT INTO ventas
                        (
                            sesion_id,
                            fecha_hora,
                            monto_total,
                            metodo_pago,
                            estado,
                            tipo_entrega,
                            recargo_delivery,
                            operacion_id,
                            operacion_hash
                        )
                    VALUES (
                        %s,
                        NOW(),
                        %s,
                        %s,
                        'COMPLETADA',
                        %s,
                        %s,
                        %s,
                        %s
                    )
                    ON CONFLICT (operacion_id)
                    WHERE operacion_id IS NOT NULL
                    DO NOTHING
                    RETURNING id;
                    """,
                    (
                        sesion_id,
                        total,
                        metodo_pago,
                        tipo_entrega,
                        recargo,
                        operacion_id,
                        operacion_hash,
                    ),
                )

                fila_venta = cur.fetchone()

                if fila_venta is None:
                    cur.execute(
                        """
                        SELECT id, operacion_hash
                        FROM ventas
                        WHERE operacion_id = %s;
                        """,
                        (operacion_id,),
                    )
                    venta_existente = cur.fetchone()

                    if not venta_existente:
                        raise RuntimeError(
                            "No se pudo resolver la operación idempotente."
                        )

                    if venta_existente[1] != operacion_hash:
                        raise IdempotenciaConflictoError(
                            "La operación ya fue utilizada con datos distintos."
                        )

                    return venta_existente[0]

                venta_id = fila_venta[0]

                # ----------------------------------------------------------
                # Guardar detalles
                # ----------------------------------------------------------
                for item in items_normalizados:

                    cur.execute(
                        """
                        INSERT INTO detalle_ventas
                            (
                                venta_id,
                                producto_nombre,
                                cantidad,
                                precio_unitario,
                                subtotal,
                                notas,
                                es_extra,
                                parent_detalle_id,
                                peso_gramos,
                                precio_kg
                            )
                        VALUES (
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s
                        );
                        """,
                        (
                            venta_id,
                            item["nombre"],
                            item["cantidad"],
                            item["precio_unitario"],
                            item["subtotal"],
                            item["notas"],
                            item["es_extra"],
                            item["parent_detalle_id"],
                            item["peso_gramos"],
                            item["precio_kg"],
                        ),
                    )

        return venta_id
    def obtener_ventas_sesion_activa(self):
        with self._connection() as conn:
            with conn.cursor() as cur:

                # ----------------------------------------------------------
                # Obtener la sesión de caja actualmente abierta
                # ----------------------------------------------------------
                cur.execute(
                    """
                    SELECT id
                    FROM sesiones_caja
                    WHERE estado = 'ABIERTA'
                    ORDER BY fecha_apertura DESC, id DESC
                    LIMIT 1;
                    """
                )

                fila_sesion = cur.fetchone()

                if not fila_sesion:
                    return []

                sesion_id = fila_sesion[0]

                # ----------------------------------------------------------
                # Obtener las ventas completadas de la sesión
                # ----------------------------------------------------------
                cur.execute(
                    """
                    SELECT
                        id,
                        TO_CHAR(fecha_hora, 'HH24:MI'),
                        monto_total,
                        metodo_pago
                    FROM ventas
                    WHERE sesion_id = %s
                    AND estado = 'COMPLETADA'
                    ORDER BY id DESC;
                    """,
                    (sesion_id,),
                )

                ventas = cur.fetchall()

                if not ventas:
                    return []

                # ----------------------------------------------------------
                # Obtener los IDs de las ventas encontradas
                # ----------------------------------------------------------
                venta_ids = [
                    fila[0]
                    for fila in ventas
                ]

                # ----------------------------------------------------------
                # Obtener los detalles de todas las ventas
                # Incluye ahora peso_gramos y precio_kg
                # ----------------------------------------------------------
                cur.execute(
                    """
                    SELECT
                        venta_id,
                        cantidad,
                        producto_nombre,
                        subtotal,
                        notas,
                        precio_unitario,
                        es_extra,
                        parent_detalle_id,
                        peso_gramos,
                        precio_kg
                    FROM detalle_ventas
                    WHERE venta_id = ANY(%s)
                    ORDER BY venta_id, id;
                    """,
                    (venta_ids,),
                )

                detalles_por_venta = {}

                for fila in cur.fetchall():

                    venta_id = fila[0]

                    detalles_por_venta.setdefault(
                        venta_id,
                        [],
                    ).append(
                        {
                            "cant": fila[1],
                            "nombre": fila[2],
                            "subtotal": fila[3],
                            "obs": fila[4],
                            "precio_unitario": fila[5],
                            "es_extra": fila[6],
                            "parent_detalle_id": fila[7],
                            "peso_gramos": fila[8],
                            "precio_kg": fila[9],
                        }
                    )

        # ------------------------------------------------------------------
        # Mantener el mismo formato que ya esperaba la GUI:
        #
        # (
        #     venta_id,
        #     hora,
        #     monto,
        #     metodo_pago,
        #     detalles
        # )
        # ------------------------------------------------------------------
        return [
            (
                venta_id,
                hora,
                monto,
                metodo,
                detalles_por_venta.get(
                    venta_id,
                    [],
                ),
            )
            for venta_id, hora, monto, metodo in ventas
        ]
    def obtener_detalle_venta(self, venta_id):
        with self._connection() as conn:
            with conn.cursor() as cur:

                # ----------------------------------------------------------
                # Obtener datos generales de la venta
                # ----------------------------------------------------------
                cur.execute(
                    """
                    SELECT
                        id,
                        sesion_id,
                        TO_CHAR(
                            fecha_hora,
                            'YYYY-MM-DD HH24:MI:SS'
                        ),
                        monto_total,
                        metodo_pago,
                        estado,
                        tipo_entrega,
                        recargo_delivery
                    FROM ventas
                    WHERE id = %s;
                    """,
                    (venta_id,),
                )

                venta = cur.fetchone()

                if not venta:
                    return None

                # ----------------------------------------------------------
                # Obtener productos/detalles de la venta
                #
                # Incluye:
                # - peso_gramos
                # - precio_kg
                # ----------------------------------------------------------
                cur.execute(
                    """
                    SELECT
                        cantidad,
                        producto_nombre,
                        precio_unitario,
                        subtotal,
                        notas,
                        es_extra,
                        parent_detalle_id,
                        peso_gramos,
                        precio_kg
                    FROM detalle_ventas
                    WHERE venta_id = %s
                    ORDER BY id;
                    """,
                    (venta_id,),
                )

                detalles = cur.fetchall()

        # ------------------------------------------------------------------
        # Devolver venta en formato diccionario
        # ------------------------------------------------------------------
        return {
            "id": venta[0],
            "sesion_id": venta[1],
            "fecha": venta[2],
            "monto_total": venta[3],
            "metodo_pago": venta[4],
            "estado": venta[5],
            "tipo_entrega": venta[6],
            "recargo_delivery": venta[7],

            "items": [
                {
                    "cant": detalle[0],
                    "nombre": detalle[1],
                    "precio_unitario": detalle[2],
                    "subtotal": detalle[3],
                    "obs": detalle[4],
                    "es_extra": detalle[5],
                    "parent_detalle_id": detalle[6],
                    "peso_gramos": detalle[7],
                    "precio_kg": detalle[8],
                }
                for detalle in detalles
            ],
        }
    def obtener_historial_ventas(self, fecha_inicio=None, fecha_fin=None):
        query = """
            SELECT id, sesion_id, fecha_hora, monto_total, metodo_pago,
                   estado, tipo_entrega, recargo_delivery
            FROM ventas
            WHERE 1 = 1
        """
        params = []

        if fecha_inicio is not None:
            query += " AND fecha_hora >= %s"
            params.append(fecha_inicio)
        if fecha_fin is not None:
            query += " AND fecha_hora <= %s"
            params.append(fecha_fin)

        query += " ORDER BY fecha_hora DESC, id DESC;"

        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, tuple(params))
                return cur.fetchall()

    def actualizar_metodo_pago_venta(self, ticket_id, nuevo_metodo):
        nuevo_metodo = self._validar_metodo_pago(nuevo_metodo)

        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE ventas AS v
                    SET metodo_pago = %s
                    FROM sesiones_caja AS s
                    WHERE v.id = %s
                      AND v.sesion_id = s.id
                      AND s.estado = 'ABIERTA'
                      AND v.estado = 'COMPLETADA';
                    """,
                    (nuevo_metodo, ticket_id),
                )
                if cur.rowcount != 1:
                    raise VentaNoModificableError(
                        "La venta no existe, ya fue anulada o no pertenece "
                        "a la sesión de caja abierta."
                    )

    def anular_venta(self, ticket_id):
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE ventas AS v
                    SET estado = 'ANULADA'
                    FROM sesiones_caja AS s
                    WHERE v.id = %s
                      AND v.sesion_id = s.id
                      AND s.estado = 'ABIERTA'
                      AND v.estado = 'COMPLETADA';
                    """,
                    (ticket_id,),
                )
                if cur.rowcount != 1:
                    raise VentaNoModificableError(
                        "La venta no existe, ya fue anulada o no pertenece "
                        "a la sesión de caja abierta."
                    )

    # ==================================================================
    # 7. RESÚMENES / REPORTES
    # ==================================================================

    def obtener_resumen_caja(self):
        """
        Devuelve el resumen de la sesión ABIERTA con una sola conexión.
        """
        cero = Decimal("0")

        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, fecha_apertura, monto_inicial
                    FROM sesiones_caja
                    WHERE estado = 'ABIERTA'
                    ORDER BY fecha_apertura DESC, id DESC
                    LIMIT 1;
                    """
                )
                sesion = cur.fetchone()

                if not sesion:
                    return {
                        "sesion_id": None,
                        "fecha_apertura": None,
                        "monto_inicial": cero,
                        "total_ventas": cero,
                        "desglose_pagos": {},
                        "desglose_categorias": {},
                        "efectivo_esperado": cero,
                        "ventas_completadas": 0,
                        "ventas_anuladas": 0,
                    }

                sesion_id, fecha_apertura, monto_inicial = sesion

                cur.execute(
                    """
                    SELECT metodo_pago, COUNT(*), COALESCE(SUM(monto_total), 0)
                    FROM ventas
                    WHERE sesion_id = %s
                      AND estado = 'COMPLETADA'
                    GROUP BY metodo_pago
                    ORDER BY metodo_pago;
                    """,
                    (sesion_id,),
                )

                desglose_pagos = {}
                total_ventas = cero
                ventas_completadas = 0

                for metodo, cantidad, monto in cur.fetchall():
                    monto = monto or cero
                    desglose_pagos[metodo] = monto
                    total_ventas += monto
                    ventas_completadas += cantidad

                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM ventas
                    WHERE sesion_id = %s
                      AND estado = 'ANULADA';
                    """,
                    (sesion_id,),
                )
                ventas_anuladas = cur.fetchone()[0]

                cur.execute(
                    """
                    SELECT
                        COALESCE(p.categoria, 'VARIOS') AS categoria,
                        COALESCE(NULLIF(TRIM(p.subcategoria), ''), 'General') AS subcategoria,
                        COALESCE(SUM(dv.subtotal), 0) AS total
                    FROM detalle_ventas AS dv
                    JOIN ventas AS v ON v.id = dv.venta_id
                    LEFT JOIN productos AS p
                        ON LOWER(TRIM(p.nombre)) = LOWER(TRIM(dv.producto_nombre))
                    WHERE v.sesion_id = %s
                      AND v.estado = 'COMPLETADA'
                    GROUP BY p.categoria, p.subcategoria
                    ORDER BY categoria, subcategoria;
                    """,
                    (sesion_id,),
                )

                desglose_categorias = {}
                for categoria, subcategoria, monto in cur.fetchall():
                    desglose_categorias.setdefault(categoria, {})[subcategoria] = monto

        monto_inicial = monto_inicial or cero
        efectivo = desglose_pagos.get("Efectivo", cero)

        return {
            "sesion_id": sesion_id,
            "fecha_apertura": fecha_apertura,
            "monto_inicial": monto_inicial,
            "total_ventas": total_ventas,
            "desglose_pagos": desglose_pagos,
            "desglose_categorias": desglose_categorias,
            "efectivo_esperado": monto_inicial + efectivo,
            "ventas_completadas": ventas_completadas,
            "ventas_anuladas": ventas_anuladas,
        }

    def obtener_resumen_sesiones(self, limite=100):
        """Historial de dinero manejado por sesión de caja."""
        limite = max(1, min(int(limite), 1000))

        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        s.id,
                        s.fecha_apertura,
                        s.fecha_cierre,
                        s.estado,
                        s.monto_inicial,
                        COUNT(v.id) FILTER (WHERE v.estado = 'COMPLETADA') AS ventas,
                        COUNT(v.id) FILTER (WHERE v.estado = 'ANULADA') AS anuladas,
                        COALESCE(SUM(v.monto_total) FILTER (
                            WHERE v.estado = 'COMPLETADA'
                              AND v.metodo_pago = 'Efectivo'
                        ), 0) AS efectivo,
                        COALESCE(SUM(v.monto_total) FILTER (
                            WHERE v.estado = 'COMPLETADA'
                              AND v.metodo_pago = 'POS / Tarjeta'
                        ), 0) AS pos,
                        COALESCE(SUM(v.monto_total) FILTER (
                            WHERE v.estado = 'COMPLETADA'
                              AND v.metodo_pago = 'Transferencia'
                        ), 0) AS transferencia,
                        COALESCE(SUM(v.monto_total) FILTER (
                            WHERE v.estado = 'COMPLETADA'
                        ), 0) AS total_ventas,
                        s.monto_inicial + COALESCE(SUM(v.monto_total) FILTER (
                            WHERE v.estado = 'COMPLETADA'
                              AND v.metodo_pago = 'Efectivo'
                        ), 0) AS efectivo_esperado
                    FROM sesiones_caja s
                    LEFT JOIN ventas v ON v.sesion_id = s.id
                    GROUP BY s.id
                    ORDER BY s.id DESC
                    LIMIT %s;
                    """,
                    (limite,),
                )

                filas = cur.fetchall()

        return [
            {
                "sesion_id": f[0],
                "fecha_apertura": f[1],
                "fecha_cierre": f[2],
                "estado": f[3],
                "monto_inicial": f[4],
                "ventas": f[5],
                "anuladas": f[6],
                "efectivo": f[7],
                "pos": f[8],
                "transferencia": f[9],
                "total_ventas": f[10],
                "efectivo_esperado": f[11],
            }
            for f in filas
        ]
    def obtener_resumen_semanal(self, limite_semanas=52):
        """Totales semanales por medio de pago, de lunes a domingo."""

        limite_semanas = max(
            1,
            min(int(limite_semanas), 260)
        )

        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        DATE_TRUNC(
                            'week',
                            fecha_hora
                        )::date AS semana_inicio,

                        (
                            DATE_TRUNC(
                                'week',
                                fecha_hora
                            )::date + 6
                        ) AS semana_fin,

                        COUNT(*) AS cantidad_ventas,

                        COALESCE(
                            SUM(monto_total) FILTER (
                                WHERE metodo_pago = 'Efectivo'
                            ),
                            0
                        ) AS efectivo,

                        COALESCE(
                            SUM(monto_total) FILTER (
                                WHERE metodo_pago = 'POS / Tarjeta'
                            ),
                            0
                        ) AS pos,

                        COALESCE(
                            SUM(monto_total) FILTER (
                                WHERE metodo_pago = 'Transferencia'
                            ),
                            0
                        ) AS transferencia,

                        COALESCE(
                            SUM(monto_total),
                            0
                        ) AS total

                    FROM ventas

                    WHERE estado = 'COMPLETADA'

                    GROUP BY
                        DATE_TRUNC(
                            'week',
                            fecha_hora
                        )

                    ORDER BY
                        semana_inicio DESC

                    LIMIT %s;
                    """,
                    (limite_semanas,),
                )

                filas = cur.fetchall()

        return [
            {
                "semana_inicio": fila[0],
                "semana_fin": fila[1],
                "cantidad_ventas": fila[2],
                "efectivo": fila[3],
                "pos": fila[4],
                "transferencia": fila[5],
                "total": fila[6],
            }
            for fila in filas
        ]

    def obtener_resumen_mensual(self, limite_meses=24):
        """Totales mensuales por medio de pago."""
        limite_meses = max(1, min(int(limite_meses), 120))

        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        DATE_TRUNC('month', fecha_hora)::date AS mes,
                        COUNT(*) AS cantidad_ventas,
                        COALESCE(SUM(monto_total) FILTER (
                            WHERE metodo_pago = 'Efectivo'
                        ), 0) AS efectivo,
                        COALESCE(SUM(monto_total) FILTER (
                            WHERE metodo_pago = 'POS / Tarjeta'
                        ), 0) AS pos,
                        COALESCE(SUM(monto_total) FILTER (
                            WHERE metodo_pago = 'Transferencia'
                        ), 0) AS transferencia,
                        COALESCE(SUM(monto_total), 0) AS total
                    FROM ventas
                    WHERE estado = 'COMPLETADA'
                    GROUP BY DATE_TRUNC('month', fecha_hora)
                    ORDER BY mes DESC
                    LIMIT %s;
                    """,
                    (limite_meses,),
                )
                filas = cur.fetchall()

        return [
            {
                "mes": f[0],
                "cantidad_ventas": f[1],
                "efectivo": f[2],
                "pos": f[3],
                "transferencia": f[4],
                "total": f[5],
            }
            for f in filas
        ]


Database = DBManager
