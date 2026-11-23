"""Ventana para ver y editar las reglas de una carpeta (ver reglas.py).

Cada cambio se guarda al momento en el ``.file-organizer-reglas.json`` de la carpeta, así que
también lo nota una vigilancia que esté en marcha.
"""

from __future__ import annotations

import contextlib
import tkinter as tk
import tkinter.font as tkfont
from collections.abc import Callable
from tkinter import ttk

from file_organizer.excepciones import ErrorReglas
from file_organizer.gui import tema
from file_organizer.gui.componentes import BotonRedondeado, CabeceraSeccion, CampoRedondeado
from file_organizer.reglas import Regla, ReglasCarpeta

EXPLICACION = (
    "Los ficheros cuyo nombre encaja con el patrón (* y ? de comodines: *.pdf, factura*) van "
    "a esa carpeta del destino, en vez de a la carpeta de su tipo; si agrupas por fecha, "
    "debajo va el año y el mes, salvo que pongas tú {año} o {mes}. Gana la primera que "
    "coincide."
)
SIN_REGLAS = "Esta carpeta aún no tiene reglas."
# Ancho con el que se abre la ventana, que es también el mínimo. Se puede ensanchar hasta
# ANCHO_MAXIMO (más ancho solo dejaría huecos: patrones y carpetas son cortos) y alargar hasta
# el alto de la pantalla, para ver más reglas.
ANCHO_VENTANA = 640
ANCHO_MAXIMO = 1000
MARGEN = 20
# Líneas de más que se reservan al abrir para los mensajes (un error largo ocupa dos, y la
# prueba de un nombre otra); mientras no hay mensajes, ese sitio lo aprovecha la lista.
LINEAS_RESERVADAS = 3


class DialogoReglas:
    """Lista de reglas de una carpeta, con añadir, quitar, cambiar el orden y probar un nombre."""

    def __init__(
        self,
        padre: tk.Misc,
        reglas: ReglasCarpeta,
        fuentes: dict[str, tema.Fuente],
        agrupa_por_fecha: bool = False,
    ) -> None:
        self.reglas = reglas
        # Con la agrupación por fecha elegida, «Probar un nombre» muestra también AAAA/MM.
        self.agrupa_por_fecha = agrupa_por_fecha
        self.fuentes = fuentes
        carpeta = reglas.ruta.parent
        self.ventana = tk.Toplevel(padre)
        self.ventana.title(f"Reglas de {carpeta.name or carpeta}")
        self.ventana.configure(bg=tema.FONDO_TARJETA)
        self.ventana.transient(padre.winfo_toplevel())
        self._construir_ui()
        self._refrescar_lista()
        self.ventana.bind("<Escape>", lambda _evento: self.cerrar())
        self._centrar_sobre(padre.winfo_toplevel())
        # Los textos largos se reparten al ancho de la ventana cuando se ensancha.
        self.ventana.bind("<Configure>", self._al_cambiar_tamano)
        # Modal: mientras está abierta no se cambia de carpeta ni se organiza.
        with contextlib.suppress(tk.TclError):
            self.ventana.grab_set()
        self.patron.campo.focus_set()

    # ---------------------------------------------------------------- interfaz

    def _construir_ui(self) -> None:
        """Explicación, lista con sus botones, formulario para añadir y prueba de un nombre."""
        contenido = ttk.Frame(self.ventana, style="Tarjeta.TFrame")
        contenido.pack(fill="both", expand=True, padx=MARGEN, pady=14)

        CabeceraSeccion(
            contenido, texto="Reglas de la carpeta", fondo_exterior=tema.FONDO_TARJETA
        ).pack(fill="x")
        self.explicacion = ttk.Label(
            contenido,
            text=EXPLICACION,
            style="Etiqueta.TLabel",
            justify="left",
            wraplength=ANCHO_VENTANA - 2 * MARGEN,
        )
        self.explicacion.pack(fill="x", pady=(6, 10))

        marco_lista = ttk.Frame(contenido, style="Tarjeta.TFrame")
        marco_lista.pack(fill="both", expand=True)
        # La barra solo aparece cuando hay más reglas de las que caben (ver _al_desplazar_lista).
        self.barra_lista = ttk.Scrollbar(
            marco_lista, orient="vertical", style="Vertical.TScrollbar"
        )
        self.lista = tk.Listbox(
            marco_lista,
            height=4,
            font=self.fuentes["registro"],
            bg=tema.FONDO_CAMPO,
            fg=tema.TEXTO_PRINCIPAL,
            selectbackground=tema.FONDO_RESALTADO,
            selectforeground=tema.CIAN,
            highlightthickness=1,
            highlightbackground=tema.BORDE_CAMPO,
            highlightcolor=tema.BORDE_CAMPO,
            borderwidth=0,
            activestyle="none",
            exportselection=False,
            yscrollcommand=self._al_desplazar_lista,
        )
        self.barra_lista.configure(command=self.lista.yview)
        self.lista.pack(side="left", fill="both", expand=True)

        fila_lista = ttk.Frame(contenido, style="Tarjeta.TFrame")
        fila_lista.pack(fill="x", pady=(8, 12))
        self.boton_subir = self._boton(fila_lista, "Subir", lambda: self._mover(-1))
        self.boton_bajar = self._boton(fila_lista, "Bajar", lambda: self._mover(+1))
        self.boton_quitar = self._boton(fila_lista, "Quitar", self._quitar, variante="peligro")
        for boton in (self.boton_subir, self.boton_bajar):
            boton.pack(side="left", padx=(0, 8))
        self.boton_quitar.pack(side="right")

        formulario = ttk.Frame(contenido, style="Tarjeta.TFrame")
        formulario.pack(fill="x")
        formulario.columnconfigure(0, weight=2)
        formulario.columnconfigure(1, weight=3)
        ttk.Label(formulario, text="PATRÓN", style="Etiqueta.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )
        ttk.Label(formulario, text="CARPETA", style="Etiqueta.TLabel").grid(
            row=0, column=1, sticky="w", padx=(8, 0), pady=(0, 4)
        )
        self.patron = CampoRedondeado(formulario, fondo_exterior=tema.FONDO_TARJETA)
        self.patron.grid(row=1, column=0, sticky="ew")
        self.carpeta = CampoRedondeado(formulario, fondo_exterior=tema.FONDO_TARJETA)
        self.carpeta.grid(row=1, column=1, sticky="ew", padx=8)
        self.boton_agregar = self._boton(formulario, "Añadir", self._agregar, variante="acento")
        self.boton_agregar.grid(row=1, column=2)
        for campo in (self.patron, self.carpeta):
            campo.campo.bind("<Return>", lambda _evento: self._agregar())

        fila_prueba = ttk.Frame(contenido, style="Tarjeta.TFrame")
        fila_prueba.pack(fill="x", pady=(12, 0))
        ttk.Label(fila_prueba, text="PROBAR UN NOMBRE", style="Etiqueta.TLabel").pack(
            side="left", padx=(0, 10)
        )
        self.prueba = CampoRedondeado(fila_prueba, fondo_exterior=tema.FONDO_TARJETA)
        self.prueba.pack(side="left", fill="x", expand=True)
        self.prueba.campo.bind("<KeyRelease>", lambda _evento: self._probar())
        self.resultado_prueba = ttk.Label(
            contenido,
            text="",
            style="Etiqueta.TLabel",
            justify="left",
            wraplength=ANCHO_VENTANA - 2 * MARGEN,
        )
        self.resultado_prueba.pack(fill="x", pady=(4, 0))

        self.mensaje = ttk.Label(
            contenido,
            text="",
            style="Etiqueta.TLabel",
            justify="left",
            wraplength=ANCHO_VENTANA - 2 * MARGEN,
        )
        self.mensaje.pack(fill="x", pady=(8, 0))

        fila_final = ttk.Frame(contenido, style="Tarjeta.TFrame")
        fila_final.pack(fill="x", pady=(10, 0))
        # Primero el botón: si falta sitio, se recorta la nota y no él.
        self._boton(fila_final, "Cerrar", self.cerrar).pack(side="right")
        ttk.Label(
            fila_final, text="Se guardan en la propia carpeta.", style="Atenuado.TLabel"
        ).pack(side="left")

    def _boton(
        self,
        maestro: tk.Misc,
        texto: str,
        comando: Callable[[], object],
        variante: str = "secundario",
    ) -> BotonRedondeado:
        return BotonRedondeado(
            maestro,
            texto=texto,
            variante=variante,
            comando=comando,
            fondo_exterior=tema.FONDO_TARJETA,
            padx=14,
            pady=8,
        )

    def _centrar_sobre(self, padre: tk.Misc) -> None:
        """Coloca la ventana centrada sobre la principal y fija hasta dónde se puede cambiar.

        Su tamaño al abrirse es el mínimo, para que nunca quede nada cortado.
        """
        self.ventana.update_idletasks()
        linea = tkfont.Font(root=self.ventana, font=self.fuentes["pequena"]).metrics("linespace")
        ancho = ANCHO_VENTANA
        alto = self.ventana.winfo_reqheight() + LINEAS_RESERVADAS * linea
        self.ventana.minsize(ancho, alto)
        self.ventana.maxsize(ANCHO_MAXIMO, max(alto, self.ventana.winfo_screenheight()))
        x = padre.winfo_rootx() + max(0, (padre.winfo_width() - ancho) // 2)
        y = padre.winfo_rooty() + max(0, (padre.winfo_height() - alto) // 2)
        self.ventana.geometry(f"{ancho}x{alto}+{x}+{y}")

    def _al_cambiar_tamano(self, evento: tk.Event) -> None:
        """Reparte la explicación y los mensajes al nuevo ancho de la ventana.

        El evento llega también por cada widget de dentro: solo cuenta el de la ventana. Como
        su tamaño ya está fijado, cambiar el reparto no la vuelve a ensanchar.
        """
        if evento.widget is not self.ventana:
            return
        ancho_texto = max(evento.width - 2 * MARGEN, 200)
        for etiqueta in (self.explicacion, self.resultado_prueba, self.mensaje):
            etiqueta.configure(wraplength=ancho_texto)

    def _al_desplazar_lista(self, primero: float | str, ultimo: float | str) -> None:
        """Mueve la barra de la lista y la muestra solo si hay reglas que no caben."""
        self.barra_lista.set(primero, ultimo)
        if float(primero) <= 0.0 and float(ultimo) >= 1.0:
            self.barra_lista.pack_forget()
        elif not self.barra_lista.winfo_manager():  # aún no está colocada
            self.barra_lista.pack(side="right", fill="y", padx=(4, 0))

    # ---------------------------------------------------------------- acciones

    def _refrescar_lista(self, seleccionar: int | None = None) -> None:
        """Vuelve a mostrar las reglas (en orden) y selecciona la ``seleccionar`` (desde 1)."""
        self.lista.delete(0, "end")
        reglas = self.reglas.reglas
        if not reglas:
            self.lista.insert("end", f"  {SIN_REGLAS}")
            self.lista.itemconfigure(0, foreground=tema.TEXTO_ATENUADO)
        ancho = max((len(regla.patron) for regla in reglas), default=0)
        for numero, regla in enumerate(reglas, start=1):
            self.lista.insert("end", f" {numero}. {regla.patron:<{ancho}}  →  {regla.carpeta}")
        if seleccionar is not None and reglas:
            self.lista.selection_set(seleccionar - 1)
            self.lista.see(seleccionar - 1)
        self._probar()

    def _seleccionada(self) -> int | None:
        """Número (desde 1) de la regla elegida en la lista, o None (y lo avisa)."""
        # curselection no tiene tipos en los stubs de tkinter.
        seleccion = self.lista.curselection()  # type: ignore[no-untyped-call]
        if not seleccion or not self.reglas.reglas:
            self._avisar("Elige primero una regla de la lista.", error=True)
            return None
        return int(seleccion[0]) + 1

    def _agregar(self) -> None:
        """Añade al final la regla del formulario."""
        try:
            regla = Regla.crear(self.patron.obtener(), self.carpeta.obtener())
            self.reglas.agregar(regla)
        except (ErrorReglas, OSError) as error:
            self._avisar(str(error), error=True)
            return
        self.patron.establecer("")
        self.carpeta.establecer("")
        self.patron.campo.focus_set()
        self._refrescar_lista(seleccionar=len(self.reglas.reglas))
        self._avisar(f"Añadida: {regla.texto()}")

    def _quitar(self) -> None:
        """Quita la regla elegida."""
        numero = self._seleccionada()
        if numero is None:
            return
        try:
            quitada = self.reglas.quitar(numero)
        except (ErrorReglas, OSError) as error:
            self._avisar(str(error), error=True)
            return
        self._refrescar_lista(seleccionar=min(numero, len(self.reglas.reglas)) or None)
        self._avisar(f"Quitada: {quitada.texto()}")

    def _mover(self, desplazamiento: int) -> None:
        """Sube o baja la regla elegida (las de arriba ganan)."""
        numero = self._seleccionada()
        if numero is None:
            return
        try:
            nuevo = self.reglas.mover(numero, desplazamiento)
        except (ErrorReglas, OSError) as error:
            self._avisar(str(error), error=True)
            return
        self._refrescar_lista(seleccionar=nuevo)
        self._avisar("")

    def _probar(self) -> None:
        """Dice adónde iría un fichero con el nombre escrito en «Probar un nombre»."""
        nombre = self.prueba.obtener().strip()
        if not nombre:
            self.resultado_prueba.configure(text="")
            return
        try:
            regla = self.reglas.regla_para(nombre)
        except ErrorReglas as error:
            self.resultado_prueba.configure(text=str(error), foreground=tema.ROJO)
            return
        if regla is None:
            texto = "Ninguna regla coincide: se organiza como siempre."
            color = tema.TEXTO_ETIQUETA
        else:
            numero = self.reglas.reglas.index(regla) + 1
            fecha = "AAAA/MM/" if self.agrupa_por_fecha and not regla.lleva_fecha else ""
            texto = f"Va a {regla.carpeta}/{fecha} (regla {numero}: {regla.patron})"
            color = tema.CIAN
        self.resultado_prueba.configure(text=texto, foreground=color)

    def _avisar(self, texto: str, error: bool = False) -> None:
        """Mensaje bajo el formulario: en rojo si es un error."""
        self.mensaje.configure(text=texto, foreground=tema.ROJO if error else tema.VERDE)

    def cerrar(self) -> None:
        """Cierra la ventana (las reglas ya están guardadas)."""
        with contextlib.suppress(tk.TclError):
            self.ventana.grab_release()
        self.ventana.destroy()
