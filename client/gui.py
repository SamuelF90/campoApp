import sys
import ctypes
import datetime
import json
import uuid
import os
import queue
import threading
import textwrap
import tkinter as tk
from decimal import Decimal, InvalidOperation
from tkinter import ttk

from client.ventana_cierre import VentanaCierreCaja
from client.ventana_reportes import VentanaReportes
from client.ui_dialogos import (
    mostrar_exito,
    mostrar_info,
    mostrar_advertencia,
    mostrar_error,
    confirmar,
    pedir_texto,
    pedir_monto,
)

from client.impresora_termica import (
    ErrorImpresora,
    cargar_configuracion as cargar_config_impresora,
    guardar_configuracion as guardar_config_impresora,
    imprimir_prueba,
    imprimir_ticket,
    listar_impresoras,
    obtener_impresora_predeterminada,
)

BG = "#141722"
PANEL = "#1c2030"
PANEL_2 = "#252a3e"
HEADER_BG = "#11141d"
ORANGE = "#ff8c2b"
GREEN = "#2ecc71"
RED = "#ff4d4d"
TEXT = "#ffffff"
MUTED = "#a0a5b5"



def _colorref(hex_color):
    """Convierte #RRGGBB al COLORREF que usa Windows (0x00BBGGRR)."""
    valor = hex_color.lstrip("#")
    r = int(valor[0:2], 16)
    g = int(valor[2:4], 16)
    b = int(valor[4:6], 16)
    return r | (g << 8) | (b << 16)


def aplicar_barra_titulo_campo_burger(ventana):
    """
    Colorea la barra nativa de Windows para integrarla con Campo Burger POS.

    Mantiene minimizar, maximizar, mover, redimensionar y cerrar nativos.
    En versiones de Windows que no soportan color exacto, intenta al menos
    activar el modo oscuro de la barra de título.
    """
    if sys.platform != "win32":
        return

    try:
        ventana.update_idletasks()

        hwnd_cliente = ventana.winfo_id()
        hwnd = ctypes.windll.user32.GetParent(hwnd_cliente)
        if not hwnd:
            hwnd = hwnd_cliente

        dwm = ctypes.windll.dwmapi

        # Modo oscuro de la barra. El atributo cambió entre builds de Windows.
        oscuro = ctypes.c_int(1)
        resultado = dwm.DwmSetWindowAttribute(
            hwnd,
            20,  # DWMWA_USE_IMMERSIVE_DARK_MODE (Windows 10/11 recientes)
            ctypes.byref(oscuro),
            ctypes.sizeof(oscuro),
        )
        if resultado != 0:
            dwm.DwmSetWindowAttribute(
                hwnd,
                19,  # fallback para algunas builds anteriores
                ctypes.byref(oscuro),
                ctypes.sizeof(oscuro),
            )

        # Windows 11 permite además definir los colores exactos.
        atributos_colores = (
            (35, HEADER_BG),  # DWMWA_CAPTION_COLOR
            (36, TEXT),       # DWMWA_TEXT_COLOR
            (34, HEADER_BG),  # DWMWA_BORDER_COLOR
        )

        for atributo, color_hex in atributos_colores:
            color = ctypes.c_int(_colorref(color_hex))
            dwm.DwmSetWindowAttribute(
                hwnd,
                atributo,
                ctypes.byref(color),
                ctypes.sizeof(color),
            )

    except Exception:
        # La estética nunca debe impedir que el POS funcione.
        pass


def configurar_barra_titulo_campo_burger(ventana):
    """Aplica/reaplica el estilo cuando Windows muestra o restaura la ventana."""
    ventana.after(
        25,
        lambda: aplicar_barra_titulo_campo_burger(ventana),
    )

    ventana.bind(
        "<Map>",
        lambda _event: ventana.after(
            25,
            lambda: aplicar_barra_titulo_campo_burger(ventana),
        ),
        add="+",
    )



def configurar_estilo_scrollbars(style):
    """Aplica el estilo Campo Burger a las barras de desplazamiento."""

    style.configure(
        "Campo.Vertical.TScrollbar",
        troughcolor=PANEL,
        background="#374151",
        darkcolor="#374151",
        lightcolor="#374151",
        bordercolor=PANEL,
        arrowcolor=TEXT,
        relief="flat",
        gripcount=0,
        arrowsize=12,
    )

    style.map(
        "Campo.Vertical.TScrollbar",
        background=[
            ("active", "#4b5563"),
            ("pressed", ORANGE),
        ],
        arrowcolor=[
            ("active", TEXT),
            ("pressed", TEXT),
        ],
    )

    style.configure(
        "Campo.Horizontal.TScrollbar",
        troughcolor=PANEL,
        background="#374151",
        darkcolor="#374151",
        lightcolor="#374151",
        bordercolor=PANEL,
        arrowcolor=TEXT,
        relief="flat",
        gripcount=0,
        arrowsize=12,
    )

    style.map(
        "Campo.Horizontal.TScrollbar",
        background=[
            ("active", "#4b5563"),
            ("pressed", ORANGE),
        ],
        arrowcolor=[
            ("active", TEXT),
            ("pressed", TEXT),
        ],
    )



def configurar_estilo_comboboxes(style, ventana):
    """Da a los Combobox el mismo estilo oscuro del POS."""

    style.configure(
        "Campo.TCombobox",
        background=PANEL_2,
        fieldbackground=PANEL_2,
        foreground=TEXT,
        arrowcolor=TEXT,
        bordercolor="#374151",
        lightcolor=PANEL_2,
        darkcolor=PANEL_2,
        relief="flat",
        padding=5,
    )

    style.map(
        "Campo.TCombobox",
        fieldbackground=[
            ("readonly", PANEL_2),
            ("disabled", PANEL),
        ],
        background=[
            ("readonly", PANEL_2),
            ("active", "#374151"),
            ("pressed", ORANGE),
        ],
        foreground=[
            ("readonly", TEXT),
            ("disabled", MUTED),
        ],
        arrowcolor=[
            ("readonly", TEXT),
            ("active", ORANGE),
            ("pressed", TEXT),
        ],
        bordercolor=[
            ("focus", ORANGE),
            ("readonly", "#374151"),
        ],
    )

    # Estilo de la lista desplegable interna del Combobox.
    ventana.option_add("*TCombobox*Listbox.background", PANEL_2)
    ventana.option_add("*TCombobox*Listbox.foreground", TEXT)
    ventana.option_add("*TCombobox*Listbox.selectBackground", ORANGE)
    ventana.option_add("*TCombobox*Listbox.selectForeground", TEXT)
    ventana.option_add("*TCombobox*Listbox.font", ("Segoe UI", 9))


def activar_estilo_campo_burger(ventana):
    """Configura el tema ttk y las scrollbars del POS."""
    style = ttk.Style(ventana)

    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    configurar_estilo_scrollbars(style)
    configurar_estilo_comboboxes(style, ventana)
    return style



def preparar_dialogo_campo_burger(
    dialog,
    parent,
    titulo,
    ancho,
    alto,
    on_close=None,
):
    """
    Convierte un Toplevel pequeño en un diálogo 100% Campo Burger:
    sin barra blanca de Windows, con encabezado oscuro, botón X y arrastre.
    """

    if on_close is None:
        on_close = dialog.destroy

    dialog.overrideredirect(True)
    dialog.configure(bg=BG)
    dialog.resizable(False, False)

    try:
        dialog.transient(parent)
    except tk.TclError:
        pass

    # ------------------------------------------------------------
    # BARRA SUPERIOR PERSONALIZADA
    # ------------------------------------------------------------
    header = tk.Frame(
        dialog,
        bg=HEADER_BG,
        height=38,
        cursor="fleur",
    )
    header.pack(fill="x")
    header.pack_propagate(False)

    lbl_titulo = tk.Label(
        header,
        text=titulo,
        font=("Segoe UI", 10, "bold"),
        fg=TEXT,
        bg=HEADER_BG,
        cursor="fleur",
    )
    lbl_titulo.pack(side="left", padx=12)

    btn_cerrar = tk.Button(
        header,
        text="×",
        command=on_close,
        bg=HEADER_BG,
        fg=MUTED,
        activebackground=RED,
        activeforeground=TEXT,
        relief="flat",
        bd=0,
        font=("Segoe UI", 14, "bold"),
        width=3,
        cursor="hand2",
    )
    btn_cerrar.pack(side="right", fill="y")

    # ------------------------------------------------------------
    # ARRASTRE
    # ------------------------------------------------------------
    drag = {"x": 0, "y": 0}

    def iniciar_arrastre(event):
        drag["x"] = event.x_root - dialog.winfo_x()
        drag["y"] = event.y_root - dialog.winfo_y()

    def mover(event):
        x = event.x_root - drag["x"]
        y = event.y_root - drag["y"]
        dialog.geometry(f"+{x}+{y}")

    for widget in (header, lbl_titulo):
        widget.bind("<ButtonPress-1>", iniciar_arrastre)
        widget.bind("<B1-Motion>", mover)

    # ------------------------------------------------------------
    # CENTRADO SOBRE LA VENTANA PRINCIPAL
    # ------------------------------------------------------------
    dialog.update_idletasks()

    try:
        if parent is not None and parent.winfo_exists() and parent.winfo_viewable():
            x = parent.winfo_rootx() + max(
                0, (parent.winfo_width() - ancho) // 2
            )
            y = parent.winfo_rooty() + max(
                0, (parent.winfo_height() - alto) // 2
            )
        else:
            x = max(0, (dialog.winfo_screenwidth() - ancho) // 2)
            y = max(0, (dialog.winfo_screenheight() - alto) // 2)
    except tk.TclError:
        x = max(0, (dialog.winfo_screenwidth() - ancho) // 2)
        y = max(0, (dialog.winfo_screenheight() - alto) // 2)

    dialog.geometry(f"{ancho}x{alto}+{x}+{y}")
    dialog.lift()

    # En Windows, una ventana overrideredirect puede mostrarse sin tomar
    # correctamente el foco del teclado. Lo forzamos después de mapearla.
    def activar_ventana():
        try:
            dialog.lift()
            dialog.focus_force()
        except tk.TclError:
            pass

    dialog.after_idle(activar_ventana)
    dialog.after(80, activar_ventana)

    return header



def resource_path(nombre):
    """Ruta compatible con desarrollo y ejecutable de PyInstaller."""
    base_path = getattr(
        sys,
        "_MEIPASS",
        os.path.abspath("."),
    )
    return os.path.join(base_path, nombre)


def aplicar_icono_campo_burger(ventana):
    try:
        ventana.iconbitmap(resource_path("campo_burger.ico"))
    except (tk.TclError, OSError):
        pass



def fmt_gs(monto):
    try:
        return f"Gs. {int(Decimal(str(monto))):,}".replace(",", ".")
    except (InvalidOperation, ValueError, TypeError):
        return f"Gs. {monto}"


def fmt_gramos(valor):
    """Formatea gramos sin ceros decimales innecesarios."""
    try:
        gramos = Decimal(str(valor))
    except (InvalidOperation, ValueError, TypeError):
        return str(valor)

    if gramos == gramos.to_integral_value():
        return str(int(gramos))

    return format(gramos.normalize(), "f")


def parse_gs(texto):
    if texto is None:
        return None

    limpio = str(texto).strip()
    for token in ("Gs.", "GS.", "Gs", "GS", "₲"):
        limpio = limpio.replace(token, "")

    limpio = limpio.replace(".", "").replace(",", "").replace(" ", "")

    if limpio == "":
        return Decimal("0")

    try:
        monto = Decimal(limpio)
    except InvalidOperation as exc:
        raise ValueError("El monto ingresado no es válido.") from exc

    if monto < 0:
        raise ValueError("El monto no puede ser negativo.")

    return monto


def bind_mousewheel_local(canvas):
    def _on_mousewheel(event):
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_enter(_event):
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

    def _on_leave(_event):
        canvas.unbind_all("<MouseWheel>")

    canvas.bind("<Enter>", _on_enter)
    canvas.bind("<Leave>", _on_leave)


class VentanaHistorialVentas(tk.Toplevel):
    """Ventas completadas de la sesión de caja abierta."""

    def __init__(self, parent, db):
        super().__init__(parent)
        self.parent = parent
        self.db = db

        self.title("Ventas del Turno")
        self.geometry("720x540")
        self.minsize(620, 440)
        self.configure(bg=BG)
        self.transient(parent)
        configurar_barra_titulo_campo_burger(self)

        tk.Label(
            self,
            text="VENTAS DEL TURNO ACTUAL",
            font=("Helvetica", 12, "bold"),
            fg=ORANGE,
            bg=BG,
        ).pack(pady=12)

        canvas_frame = tk.Frame(self, bg=BG)
        canvas_frame.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        self.canvas = tk.Canvas(canvas_frame, bg=BG, highlightthickness=0)
        bind_mousewheel_local(self.canvas)

        scrollbar = ttk.Scrollbar(
            canvas_frame,
            orient="vertical",
            command=self.canvas.yview,
            style="Campo.Vertical.TScrollbar",
        )

        self.frame_ventas = tk.Frame(self.canvas, bg=BG)
        self.window_id = self.canvas.create_window(
            (0, 0),
            window=self.frame_ventas,
            anchor="nw",
        )

        self.frame_ventas.bind(
            "<Configure>",
            lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfig(self.window_id, width=e.width),
        )

        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.cargar_datos()

    def _mostrar_cargando(self, texto="Cargando ventas..."):
        for widget in self.frame_ventas.winfo_children():
            widget.destroy()
        tk.Label(
            self.frame_ventas,
            text=texto,
            font=("Helvetica", 10),
            fg=MUTED,
            bg=BG,
        ).pack(pady=150)

    def cargar_datos(self):
        self._mostrar_cargando()
        self.parent.run_async(
            self.db.obtener_ventas_sesion_activa,
            self._render_ventas,
            self._mostrar_error,
        )

    def _mostrar_error(self, mensaje):
        if not self.winfo_exists():
            return
        mostrar_error(
            self,
            "Error",
            (
                "No se pudieron consultar las ventas del turno.\n\n"
                f"{mensaje}"
            ),
        )
        self._mostrar_cargando("No se pudieron cargar las ventas.")

    def _render_ventas(self, ventas):
        if not self.winfo_exists():
            return

        for widget in self.frame_ventas.winfo_children():
            widget.destroy()

        if not ventas:
            tk.Label(
                self.frame_ventas,
                text="Todavía no hay ventas completadas en este turno.",
                font=("Helvetica", 11),
                fg=MUTED,
                bg=BG,
            ).pack(pady=150)
            return

        for venta_id, hora, monto, metodo, detalle in ventas:
            card = tk.Frame(self.frame_ventas, bg=PANEL, padx=12, pady=10)
            card.pack(fill="x", pady=4)

            top = tk.Frame(card, bg=PANEL)
            top.pack(fill="x")

            tk.Label(
                top,
                text=f"Ticket #{venta_id}  |  {hora} hs  |  {fmt_gs(monto)}",
                font=("Helvetica", 9, "bold"),
                fg=TEXT,
                bg=PANEL,
            ).pack(side="left")

            tk.Button(
                top,
                text="Anular",
                command=lambda vid=venta_id: self.anular(vid),
                bg=RED,
                fg=TEXT,
                relief="flat",
                font=("Helvetica", 8, "bold"),
                cursor="hand2",
                padx=8,
            ).pack(side="right")

            combo = ttk.Combobox(
                top,
                values=["Efectivo", "POS / Tarjeta", "Transferencia"],
                state="readonly",
                width=16,
                style="Campo.TCombobox",
            )
            combo.set(metodo)
            combo.pack(side="right", padx=8)
            combo.bind(
                "<<ComboboxSelected>>",
                lambda _e, vid=venta_id, cb=combo: self.cambiar_metodo(
                    vid, cb.get()
                ),
            )

            detalle_frame = tk.Frame(card, bg=BG, padx=10, pady=7)
            detalle_frame.pack(fill="x", pady=(7, 0))

            for item in detalle:
                obs = f" ({item['obs']})" if item.get("obs") else ""
                prefijo = "  + " if item.get("es_extra") else "• "

                if item.get("peso_gramos") is not None:
                    detalle_texto = (
                        f"{prefijo}{item['nombre']}{obs} — "
                        f"{fmt_gramos(item['peso_gramos'])} g × "
                        f"{fmt_gs(item['precio_kg'])}/kg"
                        f" — {fmt_gs(item['subtotal'])}"
                    )
                else:
                    detalle_texto = (
                        f"{prefijo}{item['cant']}x {item['nombre']}{obs}"
                        f"  —  {fmt_gs(item['subtotal'])}"
                    )

                tk.Label(
                    detalle_frame,
                    text=detalle_texto,
                    font=("Helvetica", 8),
                    fg=TEXT,
                    bg=BG,
                    anchor="w",
                    justify="left",
                ).pack(fill="x")

    def cambiar_metodo(self, venta_id, nuevo_metodo):
        self.parent.run_async(
            lambda: self.db.actualizar_metodo_pago_venta(venta_id, nuevo_metodo),
            lambda _resultado: self._despues_de_modificar(),
            lambda mensaje: mostrar_error(
                self,
                "No se pudo modificar",
                mensaje,
            ),
        )

    def _despues_de_modificar(self):
        self.parent.actualizar_header_caja_async()
        self.cargar_datos()

    def anular(self, venta_id):
        if not confirmar(
            self,
            "Anular venta",
            f"¿Desea anular el Ticket #{venta_id}?",
            texto_confirmar="Sí, anular",
            texto_cancelar="Cancelar",
            peligro=True,
        ):
            return

        self._mostrar_cargando("Anulando venta...")
        self.parent.run_async(
            lambda: self.db.anular_venta(venta_id),
            lambda _resultado: self._despues_de_modificar(),
            self._mostrar_error,
        )


class VentanaTicket(tk.Toplevel):
    """Vista previa del ticket y guardado opcional en TXT."""

    def __init__(
        self,
        parent,
        venta_id,
        items,
        total,
        metodo_pago,
        tipo_entrega,
        recargo_delivery,
    ):
        super().__init__(parent)
        self.title(f"Ticket #{venta_id}")
        self.geometry("390x520")
        self.configure(bg=BG)
        self.transient(parent)
        configurar_barra_titulo_campo_burger(self)

        self.texto_ticket = self._armar_texto(
            venta_id,
            items,
            total,
            metodo_pago,
            tipo_entrega,
            recargo_delivery,
        )

        txt = tk.Text(
            self,
            bg="#0f1119",
            fg=TEXT,
            font=("Courier New", 10),
            width=42,
        )
        txt.insert("1.0", self.texto_ticket)
        txt.config(state="disabled")
        txt.pack(fill="both", expand=True, padx=10, pady=10)

        tk.Button(
            self,
            text="Guardar / Imprimir Ticket",
            command=self.guardar_e_imprimir,
            bg=ORANGE,
            fg=TEXT,
            relief="flat",
            font=("Helvetica", 9, "bold"),
            pady=7,
            cursor="hand2",
        ).pack(fill="x", padx=10, pady=(0, 10))

    def _armar_texto(
        self,
        venta_id,
        items,
        total,
        metodo_pago,
        tipo_entrega,
        recargo_delivery,
    ):
        ancho = 42
        ahora = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")

        lineas = [
            "CAMPO BURGER & ACAI".center(ancho),
            f"Ticket #{venta_id}",
            f"Fecha: {ahora}",
            f"Entrega: {tipo_entrega}",
            "-" * ancho,
        ]

        def agregar_envuelto(texto):
            partes = textwrap.wrap(
                str(texto),
                width=ancho,
                break_long_words=False,
                break_on_hyphens=False,
            )
            lineas.extend(partes or [""])

        for item in items:
            obs = f" ({item['obs']})" if item.get("obs") else ""

            if item.get("peso_gramos") is not None:
                agregar_envuelto(f"{item['nombre']}{obs}")
                agregar_envuelto(
                    f"{fmt_gramos(item['peso_gramos'])} g x "
                    f"{fmt_gs(item['precio_kg'])}/kg"
                )
            else:
                agregar_envuelto(
                    f"{item['cant']}x {item['nombre']}{obs}"
                )

            monto = fmt_gs(item["subtotal"])
            lineas.append(monto.rjust(ancho))

        if Decimal(str(recargo_delivery)) > 0:
            lineas.append("-" * ancho)
            agregar_envuelto(
                f"Recargo delivery: {fmt_gs(recargo_delivery)}"
            )

        lineas.extend(
            [
                "-" * ancho,
                f"TOTAL: {fmt_gs(total)}",
                f"Pago: {metodo_pago}",
                "=" * ancho,
            ]
        )

        return "\n".join(lineas)

    def guardar_e_imprimir(self):
        os.makedirs("tickets", exist_ok=True)

        ruta = os.path.join(
            "tickets",
            f"ticket_{datetime.datetime.now():%Y%m%d_%H%M%S}.txt",
        )

        try:
            with open(ruta, "w", encoding="utf-8") as archivo:
                archivo.write(self.texto_ticket)

        except OSError as exc:
            mostrar_error(
                self,
                "Error al guardar ticket",
                f"No se pudo guardar el ticket.\n\n{exc}",
            )
            return

        self.config(cursor="watch")

        # ESTA FUNCIÓN VA DENTRO DE guardar_e_imprimir()
        def realizar_impresion():
            config = cargar_config_impresora()

            nombre_impresora = str(
                config.get("nombre_impresora", "")
            ).strip()

            if not nombre_impresora:
                raise ErrorImpresora(
                    "No hay una impresora configurada.\n\n"
                    "Ingresá a 'Impresora' y seleccioná una impresora instalada."
                )

            impresoras_disponibles = listar_impresoras()

            if nombre_impresora not in impresoras_disponibles:
                raise ErrorImpresora(
                    "No se detecta la impresora configurada.\n\n"
                    f"Impresora: {nombre_impresora}\n\n"
                    "Verificá que esté encendida, conectada y disponible en Windows."
                )

            return imprimir_ticket(self.texto_ticket)

        def impresion_exitosa(impresora):
            if not self.winfo_exists():
                return

            self.config(cursor="")

            mostrar_exito(
                self,
                "Ticket impreso",
                (
                    "El ticket fue enviado correctamente a la impresora térmica.\n\n"
                    f"Impresora: {impresora}"
                ),
            )

        def impresion_error(mensaje):
            if not self.winfo_exists():
                return

            self.config(cursor="")

            mostrar_error(
                self,
                "No se pudo imprimir",
                (
                    f"{mensaje}\n\n"
                    "El ticket igualmente quedó guardado en:\n"
                    f"{ruta}"
                ),
            )

        self.master.run_async(
            realizar_impresion,
            impresion_exitosa,
            impresion_error,
        )


class VentanaConfiguracionImpresora(tk.Toplevel):
    """Selecciona la impresora térmica instalada y permite imprimir una prueba."""

    def __init__(self, parent):
        super().__init__(parent)

        self.parent = parent
        self.title("Impresora térmica")
        self.configure(bg=BG)

        preparar_dialogo_campo_burger(
            self,
            parent,
            "Impresora térmica 80 mm",
            520,
            390,
        )

        self.wait_visibility()
        self.grab_set()

        config = cargar_config_impresora()

        tk.Label(
            self,
            text="CONFIGURACIÓN DE IMPRESIÓN",
            font=("Segoe UI", 12, "bold"),
            fg=ORANGE,
            bg=BG,
        ).pack(pady=(20, 5))

        tk.Label(
            self,
            text=(
                "Instalá primero el driver de la impresora en Windows. "
                "Luego seleccionala aquí una sola vez."
            ),
            font=("Segoe UI", 9),
            fg=MUTED,
            bg=BG,
            justify="center",
            wraplength=440,
        ).pack(pady=(0, 15))

        frame = tk.Frame(self, bg=BG)
        frame.pack(fill="x", padx=36)

        tk.Label(
            frame,
            text="Impresora:",
            font=("Segoe UI", 9, "bold"),
            fg=TEXT,
            bg=BG,
            anchor="w",
        ).pack(fill="x", pady=(0, 5))

        try:
            impresoras = listar_impresoras()
        except ErrorImpresora as exc:
            impresoras = []
            mostrar_error(self, "Impresoras", str(exc))

        self.combo_impresora = ttk.Combobox(
            frame,
            values=impresoras,
            state="readonly",
            style="Campo.TCombobox",
            width=48,
        )
        self.combo_impresora.pack(fill="x")

        configurada = config.get("nombre_impresora", "")
        predeterminada = ""

        try:
            predeterminada = obtener_impresora_predeterminada()
        except ErrorImpresora:
            pass

        seleccion = (
            configurada
            if configurada in impresoras
            else predeterminada
            if predeterminada in impresoras
            else impresoras[0]
            if impresoras
            else ""
        )

        if seleccion:
            self.combo_impresora.set(seleccion)

        opciones = tk.Frame(self, bg=BG)
        opciones.pack(fill="x", padx=36, pady=16)

        self.var_cortar = tk.BooleanVar(
            value=bool(config.get("cortar_papel", True))
        )

        tk.Checkbutton(
            opciones,
            text="Corte automático al finalizar",
            variable=self.var_cortar,
            bg=BG,
            fg=TEXT,
            activebackground=BG,
            activeforeground=TEXT,
            selectcolor=PANEL_2,
            font=("Segoe UI", 9),
        ).pack(anchor="w")

        tk.Label(
            self,
            text=(
                "Formato configurado: papel de 80 mm · 42 caracteres por línea · "
                "ESC/POS RAW"
            ),
            font=("Segoe UI", 8),
            fg=MUTED,
            bg=BG,
        ).pack(pady=(0, 12))

        botones = tk.Frame(self, bg=BG)
        botones.pack(side="bottom", fill="x", padx=36, pady=22)

        tk.Button(
            botones,
            text="Imprimir prueba",
            command=self._imprimir_prueba,
            bg=PANEL_2,
            fg=TEXT,
            relief="flat",
            padx=16,
            pady=7,
            cursor="hand2",
        ).pack(side="left")

        tk.Button(
            botones,
            text="Guardar",
            command=self._guardar,
            bg=GREEN,
            fg=TEXT,
            relief="flat",
            padx=18,
            pady=7,
            cursor="hand2",
        ).pack(side="right")

    def _guardar_config(self):
        nombre = self.combo_impresora.get().strip()

        if not nombre:
            mostrar_advertencia(
                self,
                "Impresora",
                "Seleccioná una impresora instalada en Windows.",
            )
            return None

        return guardar_config_impresora(
            nombre_impresora=nombre,
            ancho_caracteres=42,
            cortar_papel=self.var_cortar.get(),
            copias=1,
        )

    def _guardar(self):
        config = self._guardar_config()
        if config is None:
            return

        mostrar_exito(
            self,
            "Impresora configurada",
            (
                "La impresora térmica quedó configurada para los "
                "próximos tickets."
            ),
        )
        self.destroy()

    def _imprimir_prueba(self):
        config = self._guardar_config()
        if config is None:
            return

        try:
            impresora = imprimir_prueba(
                config["nombre_impresora"]
            )
        except ErrorImpresora as exc:
            mostrar_error(
                self,
                "Prueba de impresión",
                str(exc),
            )
            return

        mostrar_exito(
            self,
            "Prueba enviada",
            f"Se envió un ticket de prueba a:\n\n{impresora}",
        )


class VentanaPrincipal(tk.Tk):
    """Interfaz principal del POS."""

    CATEGORIAS_MENU = [
        "Hamburguesas",
        "Lomitos y Sándwiches",
        "Agregados",
        "Bebidas",
        "Helados y Postres",
        "Papas Fritas",
        "Milanesas",
    ]

    MAPEO_CATEGORIAS_ANTIGUAS = {
        "papas y agregados": "Papas Fritas",
        "postres": "Helados y Postres",
    }

    def __init__(self, db):
        super().__init__()
        self.db = db
        self.carrito = {}
        self._secuencia_carrito = 0
        self.catalogo_productos = []
        self.categoria_actual = "TODOS"

        self._ui_queue = queue.Queue()
        self._cerrando = False
        self._venta_en_proceso = False
        self._venta_pendiente = None
        self._catalogo_cargando = False
        self._header_en_proceso = False

        self.title("Campo Burger & Açaí - Punto de Venta")
        aplicar_icono_campo_burger(self)
        self.geometry("1180x760")
        self.minsize(980, 650)
        self.configure(bg=BG)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        # Estilo global ttk del POS (scrollbars, etc.).
        self._ttk_style = activar_estilo_campo_burger(self)

        self.withdraw()
        if not self._asegurar_caja_abierta():
            self.destroy()
            return

        self.crear_interfaz()
        self.deiconify()
        configurar_barra_titulo_campo_burger(self)

        # Procesa callbacks provenientes de threads en el hilo de Tkinter.
        self.after(40, self._procesar_cola_ui)

        self.cargar_catalogo_async(inicial=True)
        self.actualizar_header_caja_async()

    # ================================================================
    # ASINCRONÍA SEGURA PARA TKINTER
    # ================================================================

    def run_async(self, trabajo, al_terminar, al_error=None):
        """Ejecuta trabajo de BD fuera del hilo gráfico."""

        def worker():
            try:
                resultado = trabajo()
            except Exception as exc:
                if al_error is not None:
                    self._ui_queue.put((al_error, (str(exc),), {}))
            else:
                if al_terminar is not None:
                    self._ui_queue.put((al_terminar, (resultado,), {}))

        threading.Thread(target=worker, daemon=True).start()

    def _procesar_cola_ui(self):
        if self._cerrando:
            return

        try:
            while True:
                callback, args, kwargs = self._ui_queue.get_nowait()
                try:
                    callback(*args, **kwargs)
                except tk.TclError:
                    pass
                except Exception as exc:
                    print(f"[UI] Error aplicando callback: {exc}")
        except queue.Empty:
            pass

        if not self._cerrando:
            self.after(40, self._procesar_cola_ui)

    # ================================================================
    # SESIÓN / APERTURA
    # ================================================================

    def _asegurar_caja_abierta(self):
        try:
            sesion = self.db.obtener_sesion_abierta()

        except Exception as exc:
            mostrar_error(
                self,
                "Error de base de datos",
                (
                    "No se pudo consultar la sesión de caja.\n\n"
                    f"{exc}"
                ),
            )
            return False

        if sesion is not None:
            return True

        while True:
            monto = pedir_monto(
                self,
                "Apertura de caja",
                (
                    "No existe una sesión de caja abierta.\n\n"
                    "Ingresá el fondo inicial disponible "
                    "en la caja."
                ),
                etiqueta="Fondo inicial:",
                valor_inicial="0",
                texto_confirmar="Abrir caja",
            )

            if monto is None:
                return False

            try:
                sesion_id = self.db.abrir_caja(monto)

                mostrar_exito(
                    self,
                    "Caja abierta",
                    (
                        f"Sesión #{sesion_id} abierta correctamente.\n\n"
                        f"Fondo inicial: {fmt_gs(monto)}"
                    ),
                )

                return True

            except ValueError as exc:
                mostrar_advertencia(
                    self,
                    "Monto inválido",
                    str(exc),
                )

            except Exception as exc:
                mostrar_error(
                    self,
                    "No se pudo abrir la caja",
                    str(exc),
                )
                return False
    # ================================================================
    # CATÁLOGO EN MEMORIA
    # ================================================================

    def cargar_catalogo_async(self, inicial=False):
        if self._catalogo_cargando:
            return

        self._catalogo_cargando = True
        self.btn_recargar_catalogo.config(state="disabled", text="…")
        self._mostrar_estado_catalogo("Cargando catálogo...")

        self.run_async(
            lambda: self.db.obtener_productos_filtrados("TODOS"),
            lambda productos: self._catalogo_cargado(productos, inicial),
            self._error_catalogo,
        )

    def _mostrar_estado_catalogo(self, texto):
        for widget in self.frame_productos_container.winfo_children():
            widget.destroy()
        tk.Label(
            self.frame_productos_container,
            text=texto,
            bg=BG,
            fg=MUTED,
            font=("Helvetica", 11),
        ).pack(pady=100)

    def _catalogo_cargado(self, productos, inicial=False):
        self.catalogo_productos = list(productos)
        self._catalogo_cargando = False
        self.btn_recargar_catalogo.config(state="normal", text="↻")

        print(f"[CATALOGO] {len(self.catalogo_productos)} productos cargados.")
        self.cargar_menu_productos("TODOS" if inicial else self.categoria_actual)

    def _error_catalogo(self, mensaje):
        self._catalogo_cargando = False
        self.btn_recargar_catalogo.config(state="normal", text="↻")
        self._mostrar_estado_catalogo("No se pudo cargar el catálogo.")
        mostrar_error(
            self,
            "Error de catálogo",
            (
                "No se pudieron cargar los productos desde Supabase.\n\n"
                f"{mensaje}"
            ),
        )

    @classmethod
    def _normalizar_categoria_catalogo(cls, categoria):
        original = str(categoria or "").strip()
        return cls.MAPEO_CATEGORIAS_ANTIGUAS.get(original.casefold(), original)

    # ================================================================
    # HEADER DE CAJA
    # ================================================================

    def actualizar_header_caja_async(self):
        if self._header_en_proceso or self._cerrando:
            return

        self._header_en_proceso = True
        self.run_async(
            self.db.obtener_resumen_caja,
            self._aplicar_header,
            self._error_header,
        )

    def _aplicar_header(self, resumen):
        self._header_en_proceso = False

        if not resumen.get("sesion_id"):
            self.lbl_sesion.config(text="CAJA CERRADA")
            self.lbl_resumen_header.config(text="")
            return

        self.lbl_sesion.config(
            text=f"SESIÓN #{resumen['sesion_id']} · ABIERTA"
        )
        self.lbl_resumen_header.config(
            text=(
                f"Ventas: {fmt_gs(resumen['total_ventas'])}   |   "
                f"Efectivo esperado: {fmt_gs(resumen['efectivo_esperado'])}"
            )
        )

    def _error_header(self, mensaje):
        self._header_en_proceso = False
        self.lbl_resumen_header.config(text=f"Error de caja: {mensaje}")

    # Compatibilidad con ventanas que llamaban el método sincrónico.
    def actualizar_header_caja(self):
        self.actualizar_header_caja_async()

    # ================================================================
    # CONSTRUCCIÓN UI
    # ================================================================

    def crear_interfaz(self):
        self._crear_header()
        self._crear_navegacion()
        self._crear_contenido()

    def _crear_header(self):
        header = tk.Frame(self, bg=HEADER_BG, padx=14, pady=10)
        header.pack(fill="x")

        # ------------------------------------------------------------
        # Lado izquierdo: título
        # ------------------------------------------------------------
        titulo_frame = tk.Frame(header, bg=HEADER_BG)
        titulo_frame.pack(side="left")

        tk.Label(
            titulo_frame,
            text="🍔 CAMPO BURGER & AÇAÍ",
            font=("Segoe UI", 18, "bold"),
            fg=ORANGE,
            bg=HEADER_BG,
        ).pack(anchor="w")

        self.lbl_sesion = tk.Label(
            titulo_frame,
            text="",
            font=("Segoe UI", 9, "bold"),
            fg=GREEN,
            bg=HEADER_BG,
        )
        self.lbl_sesion.pack(anchor="w")

        # ------------------------------------------------------------
        # Lado derecho: resumen + botones de acción
        # ------------------------------------------------------------
        derecha_header = tk.Frame(header, bg=HEADER_BG)
        derecha_header.pack(side="right", anchor="e")

        self.lbl_resumen_header = tk.Label(
            derecha_header,
            text="Cargando resumen...",
            font=("Segoe UI", 9),
            fg=MUTED,
            bg=HEADER_BG,
        )
        self.lbl_resumen_header.pack(anchor="e", pady=(0, 6))

        acciones = tk.Frame(derecha_header, bg=HEADER_BG)
        acciones.pack(anchor="e")

        tk.Button(
            acciones,
            text="Ventas del Turno",
            command=self.abrir_historial,
            bg=PANEL_2,
            fg=ORANGE,
            relief="flat",
            font=("Helvetica", 9, "bold"),
            padx=10,
            cursor="hand2",
        ).pack(side="left", padx=4)

        tk.Button(
            acciones,
            text="Reportes",
            command=self.abrir_reportes,
            bg=PANEL_2,
            fg=TEXT,
            relief="flat",
            font=("Helvetica", 9, "bold"),
            padx=10,
            cursor="hand2",
        ).pack(side="left", padx=4)

        tk.Button(
            acciones,
            text="Impresora",
            command=self.abrir_configuracion_impresora,
            bg=PANEL_2,
            fg=TEXT,
            relief="flat",
            font=("Helvetica", 9, "bold"),
            padx=10,
            cursor="hand2",
        ).pack(side="left", padx=4)

        self.btn_recargar_catalogo = tk.Button(
            acciones,
            text="↻",
            command=lambda: self.cargar_catalogo_async(inicial=False),
            bg=PANEL_2,
            fg=MUTED,
            relief="flat",
            font=("Helvetica", 10, "bold"),
            width=3,
            cursor="hand2",
        )
        self.btn_recargar_catalogo.pack(side="left", padx=4)

        tk.Button(
            acciones,
            text="Cerrar Caja",
            command=self.abrir_cierre_caja,
            bg=RED,
            fg=TEXT,
            relief="flat",
            font=("Helvetica", 9, "bold"),
            padx=10,
            cursor="hand2",
        ).pack(side="left", padx=(4, 0))
    def _crear_navegacion(self):
        self.frame_nav = tk.Frame(self, bg=PANEL, padx=10, pady=8)
        self.frame_nav.pack(fill="x")

        self.botones_categoria = {}

        boton_todos = tk.Button(
            self.frame_nav,
            text="TODOS",
            command=lambda: self.cargar_menu_productos("TODOS"),
            bg=ORANGE,
            fg=TEXT,
            relief="flat",
            font=("Helvetica", 9, "bold"),
            padx=10,
            cursor="hand2",
        )
        boton_todos.pack(side="left", padx=3)
        self.botones_categoria["TODOS"] = boton_todos

        for categoria in self.CATEGORIAS_MENU:
            boton = tk.Button(
                self.frame_nav,
                text=categoria.upper(),
                command=lambda cat=categoria: self.cargar_menu_productos(cat),
                bg=PANEL_2,
                fg=TEXT,
                relief="flat",
                font=("Helvetica", 8, "bold"),
                padx=8,
                cursor="hand2",
            )
            boton.pack(side="left", padx=2)
            self.botones_categoria[categoria] = boton
        
    def _crear_contenido(self):
        main = tk.Frame(self, bg=BG)
        main.pack(fill="both", expand=True, padx=10, pady=10)

        left = tk.Frame(main, bg=BG)
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))

        self.canvas_menu = tk.Canvas(left, bg=BG, highlightthickness=0)
        scrollbar_menu = ttk.Scrollbar(
            left,
            orient="vertical",
            command=self.canvas_menu.yview,
            style="Campo.Vertical.TScrollbar",
        )
        bind_mousewheel_local(self.canvas_menu)

        self.frame_productos_container = tk.Frame(self.canvas_menu, bg=BG)
        self.menu_window_id = self.canvas_menu.create_window(
            (0, 0),
            window=self.frame_productos_container,
            anchor="nw",
        )

        self.frame_productos_container.bind(
            "<Configure>",
            lambda _e: self.canvas_menu.configure(
                scrollregion=self.canvas_menu.bbox("all")
            ),
        )
        self.canvas_menu.bind(
            "<Configure>",
            lambda e: self.canvas_menu.itemconfig(self.menu_window_id, width=e.width),
        )
        self.canvas_menu.configure(yscrollcommand=scrollbar_menu.set)
        self.canvas_menu.pack(side="left", fill="both", expand=True)
        scrollbar_menu.pack(side="right", fill="y")

        right = tk.Frame(main, bg=PANEL, width=370, padx=10, pady=10)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)

        tk.Label(
            right,
            text="DETALLE DE LA VENTA",
            font=("Helvetica", 11, "bold"),
            fg=ORANGE,
            bg=PANEL,
        ).pack(anchor="w", pady=(0, 8))

        cart_frame = tk.Frame(right, bg=PANEL)
        cart_frame.pack(fill="both", expand=True)

        self.canvas_carrito = tk.Canvas(
            cart_frame,
            bg=PANEL,
            highlightthickness=0,
        )
        scrollbar_carrito = ttk.Scrollbar(
            cart_frame,
            orient="vertical",
            command=self.canvas_carrito.yview,
            style="Campo.Vertical.TScrollbar",
        )
        bind_mousewheel_local(self.canvas_carrito)

        self.frame_carrito_items = tk.Frame(self.canvas_carrito, bg=PANEL)
        self.cart_window_id = self.canvas_carrito.create_window(
            (0, 0),
            window=self.frame_carrito_items,
            anchor="nw",
        )
        self.frame_carrito_items.bind(
            "<Configure>",
            lambda _e: self.canvas_carrito.configure(
                scrollregion=self.canvas_carrito.bbox("all")
            ),
        )
        self.canvas_carrito.bind(
            "<Configure>",
            lambda e: self.canvas_carrito.itemconfig(
                self.cart_window_id,
                width=e.width,
            ),
        )
        self.canvas_carrito.configure(yscrollcommand=scrollbar_carrito.set)
        self.canvas_carrito.pack(side="left", fill="both", expand=True)
        scrollbar_carrito.pack(side="right", fill="y")

        self.lbl_total = tk.Label(
            right,
            text="TOTAL: Gs. 0",
            font=("Helvetica", 13, "bold"),
            fg=TEXT,
            bg=PANEL,
        )
        self.lbl_total.pack(anchor="e", pady=(10, 6))

        self.btn_confirmar_venta = tk.Button(
            right,
            text="Confirmar Venta",
            command=self.confirmar_venta,
            bg=GREEN,
            fg=TEXT,
            activebackground=GREEN,
            activeforeground=TEXT,
            relief="flat",
            font=("Helvetica", 10, "bold"),
            pady=8,
            cursor="hand2",
        )
        self.btn_confirmar_venta.pack(fill="x", pady=(0, 6))

        tk.Button(
            right,
            text="Vaciar Carrito",
            command=self.vaciar_carrito,
            bg=PANEL_2,
            fg=MUTED,
            relief="flat",
            pady=5,
            cursor="hand2",
        ).pack(fill="x")

    # ================================================================
    # MENÚ / CATÁLOGO LOCAL
    # ================================================================

    def _actualizar_boton_categoria_activa(self, categoria):
        for nombre, boton in self.botones_categoria.items():
            boton.configure(bg=ORANGE if nombre == categoria else PANEL_2)

    def cargar_menu_productos(self, categoria="TODOS"):
        self.categoria_actual = categoria

        if self._catalogo_cargando and not self.catalogo_productos:
            self._mostrar_estado_catalogo("Cargando catálogo...")
            return

        if categoria == "TODOS":
            productos = list(self.catalogo_productos)
        else:
            buscada = categoria.strip().casefold()
            productos = [
                producto
                for producto in self.catalogo_productos
                if self._normalizar_categoria_catalogo(producto[3])
                .strip()
                .casefold()
                == buscada
            ]

        for widget in self.frame_productos_container.winfo_children():
            widget.destroy()

        self.canvas_menu.yview_moveto(0)
        self._actualizar_boton_categoria_activa(categoria)

        if not productos:
            tk.Label(
                self.frame_productos_container,
                text="No hay productos en esta categoría.",
                bg=BG,
                fg=MUTED,
                font=("Helvetica", 11),
            ).pack(pady=100)
            return

        productos_por_subcategoria = {}
        for producto in productos:
            subcategoria = str(producto[4]).strip() if producto[4] else "VARIOS"
            productos_por_subcategoria.setdefault(subcategoria, []).append(producto)

        for subcategoria, lista_productos in productos_por_subcategoria.items():
            cabecera = tk.Frame(
                self.frame_productos_container,
                bg=PANEL,
                padx=10,
                pady=6,
            )
            cabecera.pack(fill="x", pady=(10, 4))

            tk.Label(
                cabecera,
                text=subcategoria.upper(),
                font=("Helvetica", 10, "bold"),
                fg=ORANGE,
                bg=PANEL,
            ).pack(anchor="w")

            for producto in lista_productos:
                self._crear_tarjeta_producto(producto)

    def _crear_tarjeta_producto(self, producto):
        nombre = producto[1]
        precio = producto[2]
        descripcion = producto[5]
        tipo_venta = (
            str(producto[6] or "unidad").strip().lower()
            if len(producto) > 6
            else "unidad"
        )

        card = tk.Frame(
            self.frame_productos_container,
            bg=PANEL,
            padx=12,
            pady=8,
        )
        card.pack(fill="x", pady=2)

        info = tk.Frame(card, bg=PANEL)
        info.pack(side="left", fill="x", expand=True)

        tk.Label(
            info,
            text=nombre,
            font=("Helvetica", 10, "bold"),
            fg=TEXT,
            bg=PANEL,
            anchor="w",
        ).pack(fill="x")

        if descripcion:
            tk.Label(
                info,
                text=descripcion,
                font=("Helvetica", 8),
                fg=MUTED,
                bg=PANEL,
                anchor="w",
                justify="left",
                wraplength=480,
            ).pack(fill="x", pady=2)

        texto_precio = (
            f"{fmt_gs(precio)} / kg"
            if tipo_venta == "peso"
            else fmt_gs(precio)
        )

        tk.Label(
            info,
            text=texto_precio,
            font=("Helvetica", 9, "bold"),
            fg=MUTED,
            bg=PANEL,
            anchor="w",
        ).pack(fill="x")

        tk.Button(
            card,
            text="+ Agregar",
            command=lambda prod=producto: self.agregar_al_carrito(prod),
            bg=PANEL_2,
            fg=TEXT,
            relief="flat",
            font=("Helvetica", 9, "bold"),
            padx=12,
            pady=5,
            cursor="hand2",
        ).pack(side="right")

    # ================================================================
    # CARRITO
    # ================================================================

    def agregar_al_carrito(self, producto):
        tipo_venta = (
            str(producto[6] or "unidad").strip().lower()
            if len(producto) > 6
            else "unidad"
        )

        if tipo_venta == "peso":
            self._abrir_dialogo_peso(producto)
            return

        producto_id = producto[0]

        if producto_id in self.carrito:
            self.carrito[producto_id]["cantidad"] += 1
        else:
            self.carrito[producto_id] = {
                "producto": producto,
                "cantidad": 1,
                "obs": None,
                "peso_gramos": None,
                "precio_kg": None,
                "subtotal_peso": None,
            }

        self.refrescar_carrito_ui()

    def _abrir_dialogo_peso(self, producto):
        nombre = producto[1]
        precio_kg = Decimal(str(producto[2]))

        dialog = tk.Toplevel(self)
        dialog.title("Ingresar peso")
        preparar_dialogo_campo_burger(
            dialog,
            self,
            "Ingresar peso",
            380,
            305,
        )
        dialog.wait_visibility()
        dialog.grab_set()

        tk.Label(
            dialog,
            text=nombre,
            font=("Helvetica", 12, "bold"),
            fg=ORANGE,
            bg=BG,
        ).pack(pady=(18, 4))

        tk.Label(
            dialog,
            text=f"{fmt_gs(precio_kg)} / kg",
            font=("Helvetica", 10, "bold"),
            fg=TEXT,
            bg=BG,
        ).pack(pady=(0, 14))

        tk.Label(
            dialog,
            text="Peso en gramos",
            font=("Helvetica", 9, "bold"),
            fg=TEXT,
            bg=BG,
        ).pack()

        peso_var = tk.StringVar()

        def validar_peso_tecla(nuevo_valor):
            return nuevo_valor == "" or nuevo_valor.isdigit()

        validacion_peso = (
            dialog.register(validar_peso_tecla),
            "%P",
        )

        entry_peso = tk.Entry(
            dialog,
            textvariable=peso_var,
            font=("Helvetica", 14),
            justify="center",
            width=16,
            bg=PANEL_2,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground="#374151",
            highlightcolor=ORANGE,
            validate="key",
            validatecommand=validacion_peso,
        )
        entry_peso.pack(pady=7)

        def enfocar_peso():
            try:
                dialog.lift()
                dialog.focus_force()
                entry_peso.focus_force()
                entry_peso.icursor(tk.END)
            except tk.TclError:
                pass

        # Ejecutarlo después de que Windows termine de mostrar el diálogo.
        dialog.after_idle(enfocar_peso)
        dialog.after(100, enfocar_peso)

        # Al hacer clic también aseguramos el foco de teclado.
        entry_peso.bind(
            "<Button-1>",
            lambda _e: dialog.after_idle(enfocar_peso),
            add="+",
        )

        lbl_total = tk.Label(
            dialog,
            text="Total: Gs. 0",
            font=("Helvetica", 12, "bold"),
            fg=GREEN,
            bg=BG,
        )
        lbl_total.pack(pady=8)

        def calcular():
            texto = peso_var.get().strip()

            if not texto:
                lbl_total.config(text="Total: Gs. 0")
                return None

            try:
                gramos = Decimal(texto.replace(",", "."))
            except InvalidOperation:
                lbl_total.config(text="Peso inválido", fg=RED)
                return None

            if not gramos.is_finite() or gramos <= 0:
                lbl_total.config(text="Peso inválido", fg=RED)
                return None

            if gramos != gramos.to_integral_value():
                lbl_total.config(text="Ingrese gramos enteros", fg=RED)
                return None

            subtotal = precio_kg * gramos / Decimal("1000")
            lbl_total.config(
                text=f"Total: {fmt_gs(subtotal)}",
                fg=GREEN,
            )
            return gramos, subtotal

        def actualizar_total(*_args):
            calcular()

        peso_var.trace_add("write", actualizar_total)

        def confirmar():
            resultado = calcular()

            if resultado is None:
                mostrar_advertencia(
                    dialog,
                    "Peso inválido",
                    "Ingrese un peso válido en gramos, mayor que cero.",
                )
                return

            gramos, subtotal = resultado

            self._secuencia_carrito += 1
            clave_carrito = (
                "peso",
                producto[0],
                self._secuencia_carrito,
            )

            self.carrito[clave_carrito] = {
                "producto": producto,
                "cantidad": 1,
                "obs": None,
                "peso_gramos": gramos,
                "precio_kg": precio_kg,
                "subtotal_peso": subtotal,
            }

            dialog.destroy()
            self.refrescar_carrito_ui()

        botones = tk.Frame(dialog, bg=BG)
        botones.pack(side="bottom", pady=18)

        tk.Button(
            botones,
            text="Agregar",
            command=confirmar,
            bg=GREEN,
            fg=TEXT,
            relief="flat",
            padx=16,
            pady=5,
            cursor="hand2",
        ).pack(side="left", padx=5)

        tk.Button(
            botones,
            text="Cancelar",
            command=dialog.destroy,
            bg=PANEL_2,
            fg=TEXT,
            relief="flat",
            padx=16,
            pady=5,
            cursor="hand2",
        ).pack(side="left", padx=5)

        dialog.bind("<Return>", lambda _e: confirmar())
        dialog.bind("<Escape>", lambda _e: dialog.destroy())

    def cambiar_cantidad(self, clave_carrito, delta):
        if clave_carrito not in self.carrito:
            return

        entrada = self.carrito[clave_carrito]

        # Los productos por peso representan una pesada concreta.
        # Para cambiar el peso se quita y se vuelve a agregar.
        if entrada.get("peso_gramos") is not None:
            return

        entrada["cantidad"] += delta

        if entrada["cantidad"] <= 0:
            del self.carrito[clave_carrito]

        self.refrescar_carrito_ui()

    def editar_observacion(self, clave_carrito):
        if clave_carrito not in self.carrito:
            return

        actual = self.carrito[clave_carrito].get("obs") or ""

        dialog = tk.Toplevel(self)
        dialog.title("Observación")
        preparar_dialogo_campo_burger(
            dialog,
            self,
            "Observación",
            340,
            190,
        )
        dialog.wait_visibility()
        dialog.grab_set()

        tk.Label(
            dialog,
            text="Observación del producto:",
            fg=TEXT,
            bg=BG,
            font=("Helvetica", 9, "bold"),
        ).pack(pady=(15, 6))

        entry = tk.Entry(
            dialog,
            font=("Helvetica", 10),
            width=34,
            bg=PANEL_2,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground="#374151",
            highlightcolor=ORANGE,
        )
        entry.insert(0, actual)
        entry.pack(pady=4)

        def enfocar_observacion():
            try:
                dialog.lift()
                dialog.focus_force()
                entry.focus_force()
                entry.icursor(tk.END)
            except tk.TclError:
                pass

        dialog.after_idle(enfocar_observacion)
        dialog.after(100, enfocar_observacion)

        def guardar():
            texto = entry.get().strip()
            self.carrito[clave_carrito]["obs"] = texto or None
            dialog.destroy()
            self.refrescar_carrito_ui()

        botones = tk.Frame(dialog, bg=BG)
        botones.pack(pady=12)

        tk.Button(
            botones,
            text="Guardar",
            command=guardar,
            bg=GREEN,
            fg=TEXT,
            relief="flat",
            padx=12,
        ).pack(side="left", padx=4)

        tk.Button(
            botones,
            text="Cancelar",
            command=dialog.destroy,
            bg=PANEL_2,
            fg=TEXT,
            relief="flat",
            padx=12,
        ).pack(side="left", padx=4)

    def quitar_del_carrito(self, clave_carrito):
        self.carrito.pop(clave_carrito, None)
        self.refrescar_carrito_ui()

    def vaciar_carrito(self):
        self.carrito.clear()
        self.refrescar_carrito_ui()

    def refrescar_carrito_ui(self):
        for widget in self.frame_carrito_items.winfo_children():
            widget.destroy()

        total = Decimal("0")

        for clave_carrito, entrada in self.carrito.items():
            producto = entrada["producto"]
            cantidad = entrada["cantidad"]

            peso_gramos = entrada.get("peso_gramos")
            es_por_peso = peso_gramos is not None

            if es_por_peso:
                precio_kg = Decimal(str(entrada["precio_kg"]))
                subtotal = Decimal(str(entrada["subtotal_peso"]))
            else:
                precio = Decimal(str(producto[2]))
                subtotal = precio * cantidad
                precio_kg = None

            total += subtotal

            obs = entrada.get("obs")
            obs_texto = f" ({obs})" if obs else ""

            fila = tk.Frame(
                self.frame_carrito_items,
                bg=PANEL,
                pady=5,
            )
            fila.pack(fill="x", pady=2)

            tk.Label(
                fila,
                text=f"{producto[1]}{obs_texto}",
                fg=ORANGE if obs else TEXT,
                bg=PANEL,
                anchor="w",
                justify="left",
                wraplength=320,
                font=("Helvetica", 9, "bold" if obs else "normal"),
            ).pack(anchor="w")

            if es_por_peso:
                tk.Label(
                    fila,
                    text=(
                        f"{fmt_gramos(peso_gramos)} g × "
                        f"{fmt_gs(precio_kg)}/kg"
                    ),
                    fg=MUTED,
                    bg=PANEL,
                    anchor="w",
                    font=("Helvetica", 8),
                ).pack(anchor="w", pady=(1, 0))

            controles = tk.Frame(fila, bg=PANEL)
            controles.pack(fill="x", pady=(3, 0))

            if es_por_peso:
                tk.Label(
                    controles,
                    text="Por peso",
                    bg=PANEL,
                    fg=MUTED,
                    font=("Helvetica", 8, "bold"),
                ).pack(side="left")
            else:
                tk.Button(
                    controles,
                    text="-",
                    command=lambda clave=clave_carrito: self.cambiar_cantidad(
                        clave, -1
                    ),
                    width=2,
                    bg=PANEL_2,
                    fg=TEXT,
                    relief="flat",
                ).pack(side="left")

                tk.Label(
                    controles,
                    text=str(cantidad),
                    width=3,
                    bg=PANEL,
                    fg=TEXT,
                ).pack(side="left")

                tk.Button(
                    controles,
                    text="+",
                    command=lambda clave=clave_carrito: self.cambiar_cantidad(
                        clave, 1
                    ),
                    width=2,
                    bg=PANEL_2,
                    fg=TEXT,
                    relief="flat",
                ).pack(side="left")

            tk.Button(
                controles,
                text="Obs",
                command=lambda clave=clave_carrito: self.editar_observacion(
                    clave
                ),
                bg=PANEL_2,
                fg=ORANGE,
                relief="flat",
                font=("Helvetica", 8, "bold"),
            ).pack(side="left", padx=4)

            tk.Label(
                controles,
                text=fmt_gs(subtotal),
                bg=PANEL,
                fg=MUTED,
                font=("Helvetica", 8),
            ).pack(side="left", padx=5)

            tk.Button(
                controles,
                text="Quitar",
                command=lambda clave=clave_carrito: self.quitar_del_carrito(
                    clave
                ),
                bg=RED,
                fg=TEXT,
                relief="flat",
                font=("Helvetica", 8),
            ).pack(side="right")

        self.lbl_total.config(
            text=f"TOTAL: {fmt_gs(total)}"
        )

    # ================================================================
    # CONFIRMAR VENTA SIN CONGELAR TKINTER
    # ================================================================

    def confirmar_venta(self):
        if self._venta_en_proceso:
            return

        if not self.carrito:
            mostrar_advertencia(
                self,
                "Carrito vacío",
                "Agregá al menos un producto antes de confirmar.",
            )
            return

        configuracion = self._pedir_datos_venta()

        if configuracion is None:
            return

        metodo_pago, tipo_entrega, recargo = configuracion
        items = []
        subtotal_productos = Decimal("0")

        for entrada in self.carrito.values():
            producto = entrada["producto"]
            cantidad = entrada["cantidad"]

            peso_gramos = entrada.get("peso_gramos")
            es_por_peso = peso_gramos is not None

            if es_por_peso:
                peso_gramos = Decimal(str(peso_gramos))
                precio_kg = Decimal(str(entrada["precio_kg"]))
                subtotal = Decimal(str(entrada["subtotal_peso"]))

                # Cada pesada es un detalle independiente.
                cantidad = 1
                precio_unitario = subtotal
            else:
                precio_kg = None
                precio_unitario = Decimal(str(producto[2]))
                subtotal = precio_unitario * cantidad

            subtotal_productos += subtotal

            categoria = (
                self._normalizar_categoria_catalogo(producto[3])
                .strip()
                .upper()
            )
            subcategoria = str(
                producto[4] or ""
            ).strip().upper()

            es_extra = (
                categoria == "AGREGADOS"
                or subcategoria == "EXTRAS"
            )

            items.append(
                {
                    "producto_id": producto[0],
                    "cant": cantidad,
                    "nombre": producto[1],
                    "precio_unitario": precio_unitario,
                    "subtotal": subtotal,
                    "obs": entrada.get("obs"),
                    "es_extra": es_extra,
                    "parent_detalle_id": None,
                    "peso_gramos": peso_gramos,
                    "precio_kg": precio_kg,
                }
            )

        total = subtotal_productos + recargo

        firma_venta = json.dumps(
            {
                "total": str(total),
                "metodo_pago": metodo_pago,
                "tipo_entrega": tipo_entrega,
                "recargo_delivery": str(recargo),
                "items": items,
            },
            sort_keys=True,
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        )

        if (
            self._venta_pendiente
            and self._venta_pendiente["firma"] == firma_venta
        ):
            operacion_id = self._venta_pendiente["operacion_id"]
        else:
            operacion_id = str(uuid.uuid4())
            self._venta_pendiente = {
                "operacion_id": operacion_id,
                "firma": firma_venta,
            }

        self._venta_en_proceso = True
        self.btn_confirmar_venta.config(
            state="disabled",
            text="Procesando...",
        )
        self.config(cursor="watch")

        self.run_async(
            lambda: self.db.crear_venta(
                total=total,
                metodo_pago=metodo_pago,
                items=items,
                tipo_entrega=tipo_entrega,
                recargo_delivery=recargo,
                operacion_id=operacion_id,
            ),
            lambda venta_id: self._venta_registrada_ui(
                venta_id,
                items,
                total,
                metodo_pago,
                tipo_entrega,
                recargo,
            ),
            self._error_venta_ui,
        )

    def _venta_registrada_ui(
        self,
        venta_id,
        items,
        total,
        metodo_pago,
        tipo_entrega,
        recargo,
    ):
        self._venta_en_proceso = False
        self._venta_pendiente = None
        self.btn_confirmar_venta.config(state="normal", text="Confirmar Venta")
        self.config(cursor="")

        VentanaTicket(
            self,
            venta_id,
            items,
            total,
            metodo_pago,
            tipo_entrega,
            recargo,
        )

        self.vaciar_carrito()
        self.actualizar_header_caja_async()

    def _error_venta_ui(self, mensaje):
        self._venta_en_proceso = False
        self.btn_confirmar_venta.config(state="normal", text="Confirmar Venta")
        self.config(cursor="")
        mostrar_error(
            self,
            "No se pudo registrar la venta",
            mensaje,
        )

    def _pedir_datos_venta(self):
        dialog = tk.Toplevel(self)
        dialog.title("Confirmar venta")

        resultado = {"valor": None}

        def cerrar_dialogo():
            resultado["valor"] = None
            dialog.destroy()

        preparar_dialogo_campo_burger(
            dialog,
            self,
            "Confirmar venta",
            360,
            325,
            on_close=cerrar_dialogo,
        )
        dialog.wait_visibility()
        dialog.grab_set()

        tk.Label(
            dialog,
            text="Método de pago",
            bg=BG,
            fg=TEXT,
            font=("Helvetica", 9, "bold"),
        ).pack(pady=(18, 5))

        combo_pago = ttk.Combobox(
            dialog,
            values=["Efectivo", "POS / Tarjeta", "Transferencia"],
            state="readonly",
            width=25,
            style="Campo.TCombobox",
        )
        combo_pago.current(0)
        combo_pago.pack()

        tk.Label(
            dialog,
            text="Tipo de entrega",
            bg=BG,
            fg=TEXT,
            font=("Helvetica", 9, "bold"),
        ).pack(pady=(14, 5))

        combo_entrega = ttk.Combobox(
            dialog,
            values=["Mostrador", "Retiro", "Delivery"],
            state="readonly",
            width=25,
            style="Campo.TCombobox",
        )
        combo_entrega.current(0)
        combo_entrega.pack()

        frame_delivery = tk.Frame(dialog, bg=BG)
        tk.Label(
            frame_delivery,
            text="Recargo delivery:",
            bg=BG,
            fg=MUTED,
            font=("Helvetica", 9),
        ).pack(side="left", padx=(0, 5))

        entry_recargo = tk.Entry(
            frame_delivery,
            width=15,
            bg=PANEL_2,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground="#374151",
            highlightcolor=ORANGE,
        )
        entry_recargo.insert(0, "0")
        entry_recargo.pack(side="left")

        def actualizar_delivery(_event=None):
            if combo_entrega.get() == "Delivery":
                frame_delivery.pack(pady=14)
            else:
                frame_delivery.pack_forget()
                entry_recargo.delete(0, tk.END)
                entry_recargo.insert(0, "0")

        combo_entrega.bind("<<ComboboxSelected>>", actualizar_delivery)

        def confirmar():
            try:
                recargo = (
                    parse_gs(entry_recargo.get())
                    if combo_entrega.get() == "Delivery"
                    else Decimal("0")
                )
            except ValueError as exc:
                mostrar_advertencia(
                    dialog,
                    "Recargo inválido",
                    str(exc),
                )
                return

            resultado["valor"] = (
                combo_pago.get(),
                combo_entrega.get(),
                recargo,
            )
            dialog.destroy()

        botones = tk.Frame(dialog, bg=BG)
        botones.pack(side="bottom", pady=18)

        tk.Button(
            botones,
            text="Confirmar",
            command=confirmar,
            bg=GREEN,
            fg=TEXT,
            relief="flat",
            padx=14,
            pady=5,
        ).pack(side="left", padx=5)

        tk.Button(
            botones,
            text="Cancelar",
            command=cerrar_dialogo,
            bg=PANEL_2,
            fg=TEXT,
            relief="flat",
            padx=14,
            pady=5,
        ).pack(side="left", padx=5)

        dialog.bind("<Escape>", lambda _e: cerrar_dialogo())
        dialog.wait_window()
        return resultado["valor"]

    # ================================================================
    # OTRAS VENTANAS
    # ================================================================

    def abrir_historial(self):
        VentanaHistorialVentas(self, self.db)

    def abrir_reportes(self):
        VentanaReportes(self, self.db)

    def abrir_configuracion_impresora(self):
        VentanaConfiguracionImpresora(self)

    def abrir_cierre_caja(self):
        VentanaCierreCaja(self, self.db)

    def on_caja_cerrada(self):
        self.vaciar_carrito()
        self.after(150, self.on_close)

    def on_close(self):
        if self._venta_en_proceso:
            mostrar_advertencia(
                self,
                "Venta en proceso",
                "Espere a que termine de registrarse la venta antes de cerrar.",
            )
            return

        self._cerrando = True
        try:
            self.db.close()
        finally:
            self.destroy()
