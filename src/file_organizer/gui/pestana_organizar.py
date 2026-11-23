"""Pestaña Organizar: elegir la carpeta, organizar, simular, deshacer y vigilar.

Las operaciones largas van en hilos aparte para no congelar la ventana; sus resultados vuelven
al hilo de la interfaz para mostrarse.
"""

from __future__ import annotations

import contextlib
import threading
import tkinter as tk
import tkinter.font as tkfont
from collections import defaultdict
from functools import partial
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Literal

from file_organizer.clasificador import (
    CRITERIO_DEFECTO,
    DESCRIPCIONES_AGRUPACION,
    EXPLICACION_FECHA,
    Clasificador,
    CriterioAgrupacion,
)
from file_organizer.config_usuario import ConfiguracionUsuario
from file_organizer.duplicados import CARPETA_DUPLICADOS, AccionDuplicados
from file_organizer.excepciones import (
    ErrorConfiguracionNoEncontrada,
    ErrorFormatoConfiguracion,
    ErrorOrganizador,
    ErrorReglas,
)
from file_organizer.gui import tema
from file_organizer.gui.componentes import (
    BarraProgreso,
    BotonRedondeado,
    CabeceraSeccion,
    CampoRedondeado,
    InfoEmergente,
    PanelRegistro,
    PuntoEstado,
    leer_ruta,
)
from file_organizer.gui.dialogo_reglas import DialogoReglas
from file_organizer.historial import RegistroMovimiento
from file_organizer.integridad import DESCRIPCIONES_INTEGRIDAD, ModoIntegridad
from file_organizer.mover_archivos import ResultadoDeshacer
from file_organizer.organizador import OrganizadorArchivos
from file_organizer.reglas import ReglasCarpeta
from file_organizer.rutas import comprobar_carpeta, comprobar_destino, limpiar_texto_ruta
from file_organizer.vigilante import VigilanteCarpeta

# Espacio horizontal entre el título y las opciones de integridad, y entre opción y opción.
SEPARACION_INTEGRIDAD = 18
# Lo mismo en la fila de «Agrupar por».
SEPARACION_AGRUPACION = 14
# Título de la fila de integridad y la versión corta que se usa si la ventana es estrecha.
TITULO_INTEGRIDAD = "INTEGRIDAD AL DESHACER"
TITULO_INTEGRIDAD_CORTO = "INTEGRIDAD"


# Los botones principales, algo más bajos que por defecto.
PADY_BOTONES_PRINCIPALES = 11
# En una simulación se muestran hasta estos nombres de fichero por categoría.
MAX_NOMBRES_SIMULACION = 4
# Duplicados que se listan en el registro tras organizar o simular.
MAX_DUPLICADOS_LISTADOS = 10
AYUDA_BACKUP = (
    "Antes de organizar, guarda los ficheros que se van a mover en un .zip dentro de "
    ".file-organizer-backups/, en la carpeta de destino."
)
AYUDA_DUPLICADOS = (
    "Duplicados: ficheros con el mismo contenido que otro ya organizado o que otro de esta "
    f"tanda. Marcada, se apartan en {CARPETA_DUPLICADOS}/; sin marcar, se organizan como los "
    "demás y solo se avisa. Nunca se borra ninguno, y «Deshacer» los devuelve a su sitio."
)


def _resumir_nombres(nombres: list[str]) -> str:
    """«a, b, c y 5 más»: los primeros nombres y cuántos quedan."""
    visibles = ", ".join(nombres[:MAX_NOMBRES_SIMULACION])
    resto = len(nombres) - MAX_NOMBRES_SIMULACION
    return f"{visibles} y {resto} más" if resto > 0 else visibles


class PestanaOrganizar:
    """Pestaña de organización: elegir carpeta, organizar, simular, deshacer y vigilar en tiempo
    real.
    """

    def __init__(self, maestro: tk.Misc, fuentes: dict[str, tema.Fuente]) -> None:
        self.fuentes = fuentes
        self._vigilante: VigilanteCarpeta | None = None
        self._hilo_vigilante: threading.Thread | None = None
        self._operacion_en_curso = False
        # Carpeta (la de destino) cuyo historial escribe el vigilante mientras vigila o se
        # detiene: mientras tanto, nada más debe escribir en él.
        self._carpeta_historial_vigilada: Path | None = None
        self._deteniendo_vigilancia = False
        self._config_categorias: str | None = None
        # (título, opciones en la misma fila): así queda al construir la interfaz.
        self._disposicion_integridad = (TITULO_INTEGRIDAD, True)

        self._config = ConfiguracionUsuario().cargar()
        self._var_backup = tk.BooleanVar(value=bool(self._config.obtener("backup", False)))
        self._var_duplicados = tk.BooleanVar(
            value=bool(self._config.obtener("mover_duplicados", False))
        )
        self._var_recursivo = tk.BooleanVar(
            value=bool(self._config.obtener("recursivo_watch", False))
        )
        self._var_integridad = tk.StringVar(value=self._config.modo_integridad().value)
        self._var_agrupar = tk.StringVar(value=self._config.criterio_agrupacion().value)
        self._var_usar_destino = tk.BooleanVar(
            value=bool(self._config.obtener("usar_destino", False))
        )

        self.marco = ttk.Frame(maestro, style="Tarjeta.TFrame")
        self._construir_ui()

        ultima = str(self._config.obtener("ultima_carpeta", ""))
        if ultima:
            self.ruta.establecer(ultima)
        ultimo_destino = str(self._config.obtener("ultimo_destino", ""))
        if ultimo_destino:
            self.destino.establecer(ultimo_destino)
        self._mostrar_destino()

    def _construir_ui(self) -> None:
        """Construye los widgets de la pestaña, sección a sección."""
        contenido = ttk.Frame(self.marco, style="Tarjeta.TFrame")
        contenido.pack(fill="both", expand=True, padx=20, pady=(14, 14))

        self._construir_origen(contenido)
        self._construir_destino(contenido)
        self._construir_botones(contenido)
        self._construir_opciones(contenido)
        self._construir_integridad(contenido)
        self._construir_agrupacion(contenido)
        self._construir_vigilancia_y_registro(contenido)

    def _construir_origen(self, contenido: ttk.Frame) -> None:
        """Cabecera, casilla de «otra carpeta» y campo de la carpeta de origen."""
        fila_cabecera = ttk.Frame(contenido, style="Tarjeta.TFrame")
        fila_cabecera.pack(fill="x", pady=(0, 8))
        self.casilla_destino = ttk.Checkbutton(
            fila_cabecera,
            text="Organizar en otra carpeta",
            variable=self._var_usar_destino,
            command=self._mostrar_destino,
            style="TCheckbutton",
        )
        self.casilla_destino.pack(side="right")
        CabeceraSeccion(
            fila_cabecera, texto="Carpeta de origen", fondo_exterior=tema.FONDO_TARJETA, width=200
        ).pack(side="left", fill="x", expand=True)

        self.ruta = CampoRedondeado(contenido, fondo_exterior=tema.FONDO_TARJETA)
        self.ruta.pack(fill="x")
        # Pegar rutas desde el portapapeles con Ctrl+V sobre el campo de ruta.
        self.ruta.campo.bind("<Control-v>", self._pegar_ruta)
        self.ruta.campo.bind("<Control-V>", self._pegar_ruta)

    def _construir_destino(self, contenido: ttk.Frame) -> None:
        """Fila de la carpeta de destino (oculta hasta marcar la casilla)."""
        self.fila_destino = ttk.Frame(contenido, style="Tarjeta.TFrame")
        ttk.Label(self.fila_destino, text="DESTINO", style="Etiqueta.TLabel").pack(
            side="left", padx=(0, 12)
        )
        self.boton_examinar_destino = BotonRedondeado(
            self.fila_destino,
            texto="Examinar...",
            variante="secundario",
            comando=self._elegir_destino,
            fondo_exterior=tema.FONDO_TARJETA,
            padx=14,
            pady=8,
        )
        self.boton_examinar_destino.pack(side="right", padx=(8, 0))
        self.destino = CampoRedondeado(self.fila_destino, fondo_exterior=tema.FONDO_TARJETA)
        self.destino.pack(side="left", fill="x", expand=True)
        self.destino.campo.bind("<Control-v>", self._pegar_destino)
        self.destino.campo.bind("<Control-V>", self._pegar_destino)

    def _construir_botones(self, contenido: ttk.Frame) -> None:
        """Fila de botones: Examinar, Pegar, Simular, Organizar y Deshacer."""
        self.fila_botones = fila_botones = ttk.Frame(contenido, style="Tarjeta.TFrame")
        fila_botones.pack(fill="x", pady=(12, 8))

        self.boton_examinar = BotonRedondeado(
            fila_botones,
            texto="Examinar...",
            variante="secundario",
            comando=self._elegir_carpeta,
            fondo_exterior=tema.FONDO_TARJETA,
            padx=tema.PADX_FILA_BOTONES,
            pady=PADY_BOTONES_PRINCIPALES,
        )
        self.boton_examinar.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.boton_pegar = BotonRedondeado(
            fila_botones,
            texto="Pegar",
            variante="secundario",
            comando=self._pegar_ruta,
            fondo_exterior=tema.FONDO_TARJETA,
            padx=tema.PADX_FILA_BOTONES,
            pady=PADY_BOTONES_PRINCIPALES,
        )
        self.boton_pegar.pack(side="left", fill="x", expand=True, padx=6)

        self.boton_simular = BotonRedondeado(
            fila_botones,
            texto="Simular",
            variante="secundario",
            comando=self._simular,
            fondo_exterior=tema.FONDO_TARJETA,
            padx=tema.PADX_FILA_BOTONES,
            pady=PADY_BOTONES_PRINCIPALES,
        )
        self.boton_simular.pack(side="left", fill="x", expand=True, padx=6)

        self.boton_organizar = BotonRedondeado(
            fila_botones,
            texto="Organizar",
            variante="acento",
            comando=self._organizar,
            fondo_exterior=tema.FONDO_TARJETA,
            padx=tema.PADX_FILA_BOTONES,
            pady=PADY_BOTONES_PRINCIPALES,
        )
        self.boton_organizar.pack(side="left", fill="x", expand=True, padx=6)

        self.boton_deshacer = BotonRedondeado(
            fila_botones,
            texto="Deshacer",
            variante="peligro",
            comando=self._deshacer,
            fondo_exterior=tema.FONDO_TARJETA,
            padx=tema.PADX_FILA_BOTONES,
            pady=PADY_BOTONES_PRINCIPALES,
        )
        self.boton_deshacer.pack(side="left", fill="x", expand=True, padx=(6, 0))

    def _construir_opciones(self, contenido: ttk.Frame) -> None:
        """Casillas de backup, de apartar duplicados y de vigilar subcarpetas.

        Lo que hace cada una sale al dejar el ratón encima.
        """
        fila_opciones = ttk.Frame(contenido, style="Tarjeta.TFrame")
        fila_opciones.pack(fill="x", pady=(0, 8))
        casilla_backup = ttk.Checkbutton(
            fila_opciones,
            text="Backup .zip",
            variable=self._var_backup,
            style="TCheckbutton",
        )
        casilla_backup.pack(side="left")
        casilla_duplicados = ttk.Checkbutton(
            fila_opciones,
            text="Apartar duplicados",
            variable=self._var_duplicados,
            style="TCheckbutton",
        )
        casilla_duplicados.pack(side="left", padx=(16, 0))
        ttk.Checkbutton(
            fila_opciones,
            text="Vigilar subcarpetas",
            variable=self._var_recursivo,
            style="TCheckbutton",
        ).pack(side="left", padx=(16, 0))
        self.ayuda_backup = InfoEmergente(
            casilla_backup, texto=AYUDA_BACKUP, fuente=self.fuentes["pequena"]
        )
        self.ayuda_duplicados = InfoEmergente(
            casilla_duplicados, texto=AYUDA_DUPLICADOS, fuente=self.fuentes["pequena"]
        )

    def _construir_integridad(self, contenido: ttk.Frame) -> None:
        """Fila de los modos de integridad y, debajo, el resumen del elegido.

        La descripción completa de cada modo sale al dejar el ratón encima, para compararlos sin
        elegirlos.
        """
        self.fila_integridad = ttk.Frame(contenido, style="Tarjeta.TFrame")
        self.fila_integridad.pack(fill="x", pady=(2, 0))
        self.titulo_integridad = ttk.Label(
            self.fila_integridad, text=TITULO_INTEGRIDAD, style="Etiqueta.TLabel"
        )
        fuente_titulo = tkfont.Font(root=self.marco, font=self.fuentes["pequena"])
        self._anchos_titulo = {
            texto: fuente_titulo.measure(texto)
            for texto in (TITULO_INTEGRIDAD, TITULO_INTEGRIDAD_CORTO)
        }
        self.titulo_integridad.grid(row=0, column=0, sticky="w", padx=(0, SEPARACION_INTEGRIDAD))
        self.opciones_integridad = ttk.Frame(self.fila_integridad, style="Tarjeta.TFrame")
        self.opciones_integridad.grid(row=0, column=1, sticky="w")
        self.ayudas_integridad: dict[ModoIntegridad, InfoEmergente] = {}
        for i, modo in enumerate(ModoIntegridad):
            descripcion = DESCRIPCIONES_INTEGRIDAD[modo]
            opcion = ttk.Radiobutton(
                self.opciones_integridad,
                text=descripcion.titulo,
                value=modo.value,
                variable=self._var_integridad,
                command=self._actualizar_descripcion_integridad,
            )
            opcion.pack(side="left", padx=(0 if i == 0 else SEPARACION_INTEGRIDAD, 0))
            self.ayudas_integridad[modo] = InfoEmergente(
                opcion,
                texto=f"{descripcion.resumen}\n\n{descripcion.detalle}",
                fuente=self.fuentes["pequena"],
            )
        # En ventanas estrechas se acorta el título y, si aun así no cabe, las opciones pasan
        # debajo de él.
        self.fila_integridad.bind("<Configure>", self._recolocar_integridad)

        self.descripcion_integridad = ttk.Label(
            contenido, style="Etiqueta.TLabel", justify="left", wraplength=780
        )
        self.descripcion_integridad.pack(fill="x", pady=(2, 10))
        # El texto se reparte en líneas según el ancho disponible.
        self.descripcion_integridad.bind(
            "<Configure>",
            lambda evento: self.descripcion_integridad.configure(wraplength=max(evento.width, 200)),
        )
        self._actualizar_descripcion_integridad()

    def _construir_agrupacion(self, contenido: ttk.Frame) -> None:
        """Fila de cómo agrupar (tipo, fecha o los dos) y botón «Reglas...».

        El ejemplo de cada forma de agrupar sale al dejar el ratón encima. Las reglas de la
        carpeta y exportar o importar categorías van en un menú, para que todo quepa en una fila
        con la ventana mínima.
        """
        fila = ttk.Frame(contenido, style="Tarjeta.TFrame")
        fila.pack(fill="x", pady=(0, 12))
        ttk.Label(fila, text="AGRUPAR POR", style="Etiqueta.TLabel").pack(
            side="left", padx=(0, SEPARACION_AGRUPACION)
        )
        self.ayudas_agrupacion: dict[CriterioAgrupacion, InfoEmergente] = {}
        for criterio in CriterioAgrupacion:
            descripcion = DESCRIPCIONES_AGRUPACION[criterio]
            opcion = ttk.Radiobutton(
                fila, text=descripcion.titulo, value=criterio.value, variable=self._var_agrupar
            )
            opcion.pack(side="left", padx=(0, SEPARACION_AGRUPACION))
            ayuda = f"Ejemplo: {descripcion.ejemplo}"
            if criterio is not CriterioAgrupacion.TIPO:
                ayuda += f"\n\n{EXPLICACION_FECHA}"
            self.ayudas_agrupacion[criterio] = InfoEmergente(
                opcion, texto=ayuda, fuente=self.fuentes["pequena"]
            )

        self.boton_categorias = BotonRedondeado(
            fila,
            texto="Reglas...",
            variante="secundario",
            comando=self._mostrar_menu_categorias,
            fondo_exterior=tema.FONDO_TARJETA,
            padx=14,
            pady=8,
        )
        self.boton_categorias.pack(side="right")
        self.menu_categorias = tk.Menu(
            self.marco,
            tearoff=False,
            background=tema.FONDO_SUPERFICIE,
            foreground=tema.TEXTO_PRINCIPAL,
            activebackground=tema.FONDO_RESALTADO,
            activeforeground=tema.TEXTO_PRINCIPAL,
            borderwidth=1,
            font=self.fuentes["normal"],
        )
        self.menu_categorias.add_command(
            label="Reglas de esta carpeta...", command=self._abrir_reglas
        )
        self.menu_categorias.add_separator()
        self.menu_categorias.add_command(
            label="Exportar categorías...", command=self._exportar_categorias
        )
        self.menu_categorias.add_command(
            label="Importar categorías...", command=self._importar_categorias
        )

    def _mostrar_menu_categorias(self) -> None:
        """Abre el menú de reglas y categorías justo debajo de su botón, alineado con su borde
        derecho.
        """
        boton = self.boton_categorias
        x = boton.winfo_rootx() + boton.winfo_width() - self.menu_categorias.winfo_reqwidth()
        self.menu_categorias.tk_popup(x, boton.winfo_rooty() + boton.winfo_height())

    def _construir_vigilancia_y_registro(self, contenido: ttk.Frame) -> None:
        """Barra de progreso, fila de la vigilancia y panel de registro."""
        self.progreso = BarraProgreso(contenido, en_curso=lambda: self._operacion_en_curso)
        self.progreso.pack(fill="x", pady=(0, 12))

        fila_estado = ttk.Frame(contenido, style="Tarjeta.TFrame")
        fila_estado.pack(fill="x")

        ttk.Label(fila_estado, text="VIGILANCIA EN TIEMPO REAL", style="Cabecera.TLabel").pack(
            side="left"
        )

        estado_derecha = ttk.Frame(fila_estado, style="Tarjeta.TFrame")
        estado_derecha.pack(side="right")

        self.boton_vigilar = BotonRedondeado(
            estado_derecha,
            texto="Vigilar",
            variante="secundario",
            comando=self._alternar_vigilante,
            fondo_exterior=tema.FONDO_TARJETA,
            padx=14,
            pady=6,
        )
        self.boton_vigilar.pack(side="right", padx=(10, 0))

        self.etiqueta_estado = ttk.Label(
            estado_derecha, text="esperando ficheros...", style="Etiqueta.TLabel"
        )
        self.etiqueta_estado.pack(side="right")

        self.punto_estado = PuntoEstado(
            estado_derecha, color=tema.CIAN, fondo_exterior=tema.FONDO_TARJETA
        )
        self.punto_estado.pack(side="right", padx=(0, 6))

        self.panel_registro = PanelRegistro(contenido, self.fuentes["registro"], alto_lineas=8)
        self.panel_registro.pack(fill="both", expand=True, pady=(10, 0))
        self.registro = self.panel_registro.texto

    def _modo_integridad(self) -> ModoIntegridad:
        """Modo de integridad elegido en la interfaz."""
        return ModoIntegridad(self._var_integridad.get())

    def _criterio_agrupacion(self) -> CriterioAgrupacion:
        """Forma de agrupar elegida en la interfaz."""
        return CriterioAgrupacion(self._var_agrupar.get())

    def _accion_duplicados(self) -> AccionDuplicados:
        """Qué hacer con los duplicados según la casilla «Apartar duplicados»."""
        return AccionDuplicados.MOVER if self._var_duplicados.get() else AccionDuplicados.AVISAR

    def _actualizar_descripcion_integridad(self) -> None:
        """Muestra el resumen del modo de integridad elegido."""
        descripcion = DESCRIPCIONES_INTEGRIDAD[self._modo_integridad()]
        self.descripcion_integridad.configure(text=descripcion.resumen)

    def _recolocar_integridad(self, evento: tk.Event | None = None) -> None:
        """Ajusta la fila de integridad al ancho disponible.

        Por preferencia: título completo y opciones en una fila; título corto en una fila;
        título completo y opciones debajo.
        """
        ancho = self.fila_integridad.winfo_width()
        opciones = SEPARACION_INTEGRIDAD + self.opciones_integridad.winfo_reqwidth()
        if ancho >= self._anchos_titulo[TITULO_INTEGRIDAD] + opciones:
            disposicion = (TITULO_INTEGRIDAD, True)
        elif ancho >= self._anchos_titulo[TITULO_INTEGRIDAD_CORTO] + opciones:
            disposicion = (TITULO_INTEGRIDAD_CORTO, True)
        else:
            disposicion = (TITULO_INTEGRIDAD, False)
        if disposicion == self._disposicion_integridad:
            return
        self._disposicion_integridad = disposicion
        titulo, en_una_fila = disposicion
        self.titulo_integridad.configure(text=titulo)
        if en_una_fila:
            self.opciones_integridad.grid_configure(row=0, column=1, columnspan=1, pady=0)
        else:
            self.opciones_integridad.grid_configure(row=1, column=0, columnspan=2, pady=(6, 0))

    def _registrar(self, nivel: str, etiqueta: str, mensaje: str) -> None:
        """Añade una línea coloreada al panel de registro."""
        self.panel_registro.registrar(nivel, etiqueta, mensaje)

    def _registrar_atenuado(self, mensaje: str) -> None:
        """Añade una línea atenuada (informativa) al panel de registro."""
        self.panel_registro.registrar_atenuado(mensaje)

    def _elegir_carpeta(self) -> None:
        """Abre el diálogo para elegir la carpeta a organizar."""
        if self._avisar_si_vigilando("cambiar de carpeta"):
            return
        self.ruta.elegir_carpeta("Selecciona la carpeta a organizar")

    def _elegir_destino(self) -> None:
        """Abre el diálogo para elegir la carpeta de destino."""
        if self._avisar_si_vigilando("cambiar de carpeta"):
            return
        self.destino.elegir_carpeta("Selecciona la carpeta de destino")

    def _mostrar_destino(self) -> None:
        """Muestra la fila de la carpeta de destino solo si está marcada la casilla."""
        if self._var_usar_destino.get():
            self.fila_destino.pack(fill="x", pady=(6, 0), before=self.fila_botones)
        else:
            self.fila_destino.pack_forget()

    def _leer_destino(self) -> tuple[bool, str | None]:
        """Carpeta de destino elegida: ``(válida, ruta)``, con ruta None si se organiza en la misma
        carpeta.

        Si no es válida, lo avisa en el registro.
        """
        if not self._var_usar_destino.get():
            return True, None
        destino = leer_ruta(
            self.destino,
            comprobar_destino,
            "Indica la carpeta de destino o desmarca «Organizar en otra carpeta»",
            self._registrar,
        )
        if destino is None:
            return False, None
        return True, str(destino)

    def _leer_carpeta(self, si_falta: str) -> str | None:
        """La carpeta elegida, si existe; si falta o no sirve, lo avisa y devuelve None."""
        carpeta = leer_ruta(self.ruta, comprobar_carpeta, si_falta, self._registrar)
        return None if carpeta is None else str(carpeta)

    def _organizar(self) -> None:
        """Organiza la carpeta en un hilo aparte."""
        self._lanzar_organizacion(simular=False)

    def _simular(self) -> None:
        """Muestra qué se movería y adónde, sin mover nada (en un hilo aparte)."""
        self._lanzar_organizacion(simular=True)

    def _lanzar_organizacion(self, simular: bool) -> None:
        """Organiza o simula en un hilo aparte, con las opciones elegidas en la interfaz."""
        if self._operacion_en_curso:
            return
        if not simular and self._avisar_si_vigilando("organizar a mano"):
            return

        accion = "simular" if simular else "organizar"
        carpeta = self._leer_carpeta(f"Selecciona una carpeta antes de {accion}")
        if carpeta is None:
            return
        valido, destino = self._leer_destino()
        if not valido:
            return

        self._establecer_operacion_en_curso(True)
        self.progreso.empezar()
        self._guardar_preferencias(carpeta)
        hacia = f" → {destino}" if destino else ""
        if simular:
            self._registrar(
                "aviso", "[SIMULACIÓN]", f"{carpeta}{hacia} (no se moverá ningún fichero)"
            )
        else:
            self._registrar("info", "[INFO]", f"Organizando: {carpeta}{hacia}")

        threading.Thread(
            target=self._trabajador_organizar,
            # Las variables de Tk se leen aquí, en el hilo de la interfaz: Tk no es seguro
            # entre hilos.
            args=(
                carpeta,
                destino,
                simular,
                self._var_backup.get(),
                self._modo_integridad(),
                self._criterio_agrupacion(),
                self._accion_duplicados(),
            ),
            daemon=True,
        ).start()

    def _trabajador_organizar(
        self,
        carpeta: str,
        destino: str | None,
        simular: bool,
        backup: bool,
        modo_integridad: ModoIntegridad,
        agrupar: CriterioAgrupacion = CRITERIO_DEFECTO,
        duplicados: AccionDuplicados = AccionDuplicados.AVISAR,
    ) -> None:
        """Hilo de trabajo: organiza (o simula) y pasa el resultado a la interfaz."""
        try:
            org = OrganizadorArchivos(
                carpeta_origen=carpeta,
                carpeta_destino=destino,
                modo_simulacion=simular,
                ruta_config=self._config_categorias,
                backup=backup,  # en una simulación no se hace backup
                modo_integridad=modo_integridad,
                agrupar=agrupar,
                duplicados=duplicados,
            )
            org.ejecutar(progreso=self.progreso.apuntar)
            movimientos = list(org.ultimos_movimientos)
            errores = list(org.ultimos_errores)
            avisos = list(org.avisos)
            resumen_fechas = org.resumen_fechas()
            resumen_reglas = org.resumen_reglas()
            textos_duplicados = [d.texto(org.carpeta_destino) for d in org.ultimos_duplicados]
            self.marco.after(
                0,
                lambda: self._organizacion_terminada(
                    movimientos,
                    errores,
                    None,
                    simular,
                    avisos,
                    resumen_fechas,
                    textos_duplicados,
                    duplicados is AccionDuplicados.MOVER,
                    resumen_reglas,
                ),
            )
        except ErrorOrganizador as e:
            self.marco.after(0, partial(self._organizacion_terminada, [], [], e, simular))
        except Exception as e:
            self.marco.after(0, partial(self._organizacion_terminada, [], [], e, simular))

    def _organizacion_terminada(
        self,
        movimientos: list[RegistroMovimiento],
        errores: list[tuple[Path, Exception]],
        error: Exception | None,
        simular: bool = False,
        avisos: list[str] | None = None,
        resumen_fechas: str | None = None,
        duplicados: list[str] | None = None,
        duplicados_apartados: bool = False,
        resumen_reglas: str | None = None,
    ) -> None:
        """Muestra en el registro el resultado de organizar o de simular, por carpeta y en orden
        (al agrupar por fecha, cronológico), los duplicados, las reglas aplicadas y de dónde
        salieron las fechas.
        """
        self._establecer_operacion_en_curso(False)
        self.progreso.terminar(completada=error is None and bool(movimientos or errores))

        if error is not None:
            self._registrar("error", "[ERROR]", str(error))
            return
        for aviso in avisos or []:
            self._registrar("aviso", "[AVISO]", aviso)

        if not movimientos and not errores:
            self._registrar_atenuado("No había ficheros para organizar.")
            return

        por_categoria: dict[str, list[str]] = defaultdict(list)
        for registro in movimientos:
            por_categoria[registro.categoria].append(registro.origen.name)

        for categoria, nombres in sorted(por_categoria.items()):
            if simular:
                self._registrar(
                    "aviso",
                    "[SIMULACIÓN]",
                    f"{len(nombres)} fichero(s) -> {categoria}/: {_resumir_nombres(nombres)}",
                )
            else:
                self._registrar("exito", "[ÉXITO]", f"{len(nombres)} fichero(s) -> {categoria}/")

        for elemento, err in errores:
            accion = "procesar" if simular else "mover"
            self._registrar("error", "[ERROR]", f"No se pudo {accion} {elemento.name}: {err}")

        self._mostrar_duplicados(duplicados or [], duplicados_apartados)
        if resumen_reglas:
            self._registrar_atenuado(resumen_reglas)
        if resumen_fechas:
            self._registrar_atenuado(resumen_fechas)

        if simular:
            self._registrar_atenuado(
                "Simulación terminada: no se ha movido nada. Pulsa Organizar para aplicarla."
            )

    def _mostrar_duplicados(self, duplicados: list[str], apartados: bool) -> None:
        """Lista los duplicados («copia.jpg = Imagenes/foto.jpg») y qué se hizo con ellos."""
        if not duplicados:
            return
        destino = (
            f"apartados en {CARPETA_DUPLICADOS}/" if apartados else "organizados como los demás"
        )
        self._registrar(
            "aviso",
            "[DUPLICADOS]",
            f"{len(duplicados)} con el mismo contenido que otro fichero, {destino}:",
        )
        for texto in duplicados[:MAX_DUPLICADOS_LISTADOS]:
            self._registrar_atenuado(f"  {texto}")
        resto = len(duplicados) - MAX_DUPLICADOS_LISTADOS
        if resto > 0:
            self._registrar_atenuado(f"  ... y {resto} más")
        if not apartados:
            self._registrar_atenuado(
                f"Marca «Apartar duplicados» para moverlos a {CARPETA_DUPLICADOS}/."
            )

    def _deshacer(self) -> None:
        """Deshace el último paso del historial en un hilo aparte."""
        if self._operacion_en_curso:
            return
        if self._avisar_si_vigilando("deshacer"):
            return

        carpeta = self._leer_carpeta("Selecciona una carpeta antes de deshacer")
        if carpeta is None:
            return
        # Hay que indicar el mismo destino que al organizar: el historial se guarda allí.
        valido, destino = self._leer_destino()
        if not valido:
            return

        self._establecer_operacion_en_curso(True)
        self.progreso.empezar()

        threading.Thread(
            target=self._trabajador_deshacer, args=(carpeta, destino), daemon=True
        ).start()

    def _trabajador_deshacer(self, carpeta: str, destino: str | None = None) -> None:
        """Hilo de trabajo: deshace el último paso y pasa el resultado a la interfaz."""
        try:
            org = OrganizadorArchivos(carpeta_origen=carpeta, carpeta_destino=destino)
            resultado = org.deshacer(progreso=self.progreso.apuntar)
            self.marco.after(0, lambda: self._deshacer_terminado(resultado, None))
        except ErrorOrganizador as e:
            self.marco.after(0, partial(self._deshacer_terminado, None, e))
        except Exception as e:
            self.marco.after(0, partial(self._deshacer_terminado, None, e))

    def _deshacer_terminado(
        self, resultado: ResultadoDeshacer | None, error: Exception | None
    ) -> None:
        """Muestra en el registro qué se restauró, con qué nombre y qué paso queda."""
        self._establecer_operacion_en_curso(False)
        self.progreso.terminar(
            completada=error is None
            and resultado is not None
            and bool(resultado.exitosos or resultado.fallidos)
        )

        if error is not None:
            self._registrar("error", "[ERROR]", str(error))
            return

        if resultado is None:
            self._registrar_atenuado("No hay historial guardado para deshacer.")
            if not self._var_usar_destino.get():
                self._registrar_atenuado(
                    "Si la organizaste en otra carpeta, marca «Organizar en otra carpeta» "
                    "e indica esa misma carpeta de destino."
                )
            return

        if resultado.exitosos == 0 and resultado.fallidos == 0:
            self._registrar_atenuado("No hay movimientos pendientes de deshacer.")
            return

        self._registrar("exito", "[ÉXITO]", f"Restaurados: {resultado.exitosos}")
        for registro, restaurado in resultado.renombrados:
            self._registrar(
                "aviso",
                "[AVISO]",
                f"{registro.origen.name} ya existía: restaurado como {restaurado.name}",
            )
        for _registro, err in resultado.errores:
            self._registrar("error", "[ERROR]", f"No se pudo restaurar: {err}")
        if resultado.siguiente is not None:
            self._registrar_atenuado(
                f"Quedan {resultado.pasos_restantes} paso(s) en el historial: el siguiente "
                f"«Deshacer» revertirá la {resultado.siguiente.descripcion()} "
                f"({len(resultado.siguiente.registros)} fichero(s))."
            )

    def _establecer_operacion_en_curso(self, en_curso: bool) -> None:
        """Marca si hay una operación en curso y actualiza los botones."""
        self._operacion_en_curso = en_curso
        self._actualizar_botones()

    @property
    def vigilando(self) -> bool:
        """Si el vigilante está en marcha o todavía deteniéndose (y guardando su historial)."""
        return self._carpeta_historial_vigilada is not None

    def carpeta_historial_vigilada(self) -> Path | None:
        """Carpeta cuyo historial está escribiendo el vigilante, o None si no se vigila."""
        return self._carpeta_historial_vigilada

    def _actualizar_botones(self) -> None:
        """Habilita cada botón según haya una operación en curso o se esté vigilando.

        Mientras se vigila, solo el vigilante escribe el historial: Organizar y Deshacer se
        desactivan (deshacer devolvería ficheros que el vigilante volvería a organizar). Simular
        no escribe nada y sigue disponible.
        """
        libre = not self._operacion_en_curso
        self.boton_simular.establecer_habilitado(libre)
        for boton in (
            self.boton_organizar,
            self.boton_deshacer,
            self.boton_examinar,
            self.boton_pegar,
        ):
            boton.establecer_habilitado(libre and not self.vigilando)
        # Siempre se puede detener; empezar a vigilar, solo sin operaciones en curso.
        self.boton_vigilar.establecer_habilitado(
            not self._deteniendo_vigilancia and (self._vigilante is not None or libre)
        )

    def _avisar_si_vigilando(self, accion: str) -> bool:
        """Si se está vigilando, avisa de que hay que detenerlo antes de ``accion``."""
        if not self.vigilando:
            return False
        self._registrar("aviso", "[AVISO]", f"Detén la vigilancia antes de {accion}.")
        return True

    def _alternar_vigilante(self) -> None:
        """Inicia o detiene la vigilancia en tiempo real."""
        if self._vigilante is None:
            self._iniciar_vigilante()
        else:
            self._detener_vigilante()

    def _iniciar_vigilante(self) -> None:
        """Crea el VigilanteCarpeta y lo arranca en un hilo aparte."""
        if self._operacion_en_curso or self._deteniendo_vigilancia:
            return
        carpeta = self._leer_carpeta("Selecciona una carpeta antes de vigilar")
        if carpeta is None:
            return
        valido, destino = self._leer_destino()
        if not valido:
            return

        self._guardar_preferencias(carpeta)
        try:
            self._vigilante = VigilanteCarpeta(
                carpeta=Path(carpeta),
                ruta_config=self._config_categorias,
                carpeta_destino=Path(destino) if destino else None,
                notificador=self._notificador_vigilante,
                recursivo=self._var_recursivo.get(),
                modo_integridad=self._modo_integridad(),
                agrupar=self._criterio_agrupacion(),
                duplicados=self._accion_duplicados(),
            )
        except (ErrorConfiguracionNoEncontrada, ErrorFormatoConfiguracion) as e:
            self._registrar("error", "[ERROR]", f"No se pueden usar esas categorías: {e}")
            return
        self._carpeta_historial_vigilada = Path(destino or carpeta).resolve()
        self._hilo_vigilante = threading.Thread(
            target=self._ejecutar_vigilante, args=(self._vigilante,), daemon=True
        )
        self._hilo_vigilante.start()

        self.punto_estado.establecer_color(tema.VERDE)
        self.etiqueta_estado.configure(text="vigilando...")
        self.boton_vigilar.establecer_texto("Detener")
        self._establecer_carpetas_editables(False)
        self._actualizar_botones()

    def _ejecutar_vigilante(self, vigilante: VigilanteCarpeta) -> None:
        """Hilo de la vigilancia; si no puede empezar o se para con un error, lo avisa.

        Por ejemplo, si otra ventana usa la carpeta (ErrorCarpetaEnUso) o la carpeta desaparece:
        así la interfaz no se queda mostrando «vigilando...».
        """
        try:
            vigilante.iniciar()
        except Exception as error:
            with contextlib.suppress(RuntimeError, tk.TclError):  # la ventana ya se cerró
                self.marco.after(0, partial(self._vigilancia_fallida, vigilante, error))

    def _vigilancia_fallida(self, vigilante: VigilanteCarpeta, error: Exception) -> None:
        """Muestra el error de la vigilancia y devuelve la interfaz a su estado normal."""
        self._registrar("error", "[ERROR]", f"No se puede vigilar: {error}")
        if self._vigilante is not vigilante:
            return  # ya se había detenido
        self._vigilante = None
        self.punto_estado.establecer_color(tema.CIAN)
        self.boton_vigilar.establecer_texto("Vigilar")
        self._vigilancia_detenida()

    def _detener_vigilante(self) -> None:
        """Detiene la vigilancia en un hilo aparte.

        Hasta que termina de guardar su historial, Organizar y Deshacer siguen desactivados para
        no leerlo a medias ni pisarlo.
        """
        vigilante = self._vigilante
        self._vigilante = None
        self.punto_estado.establecer_color(tema.CIAN)
        self.boton_vigilar.establecer_texto("Vigilar")
        if vigilante is None:
            self._vigilancia_detenida()
            return

        self._deteniendo_vigilancia = True
        self.etiqueta_estado.configure(text="deteniendo...")
        self._actualizar_botones()

        def detener() -> None:
            try:
                vigilante.detener()
            finally:
                with contextlib.suppress(RuntimeError, tk.TclError):  # la ventana ya se cerró
                    self.marco.after(0, self._vigilancia_detenida)

        threading.Thread(target=detener, daemon=True).start()

    def _vigilancia_detenida(self) -> None:
        """La vigilancia terminó y guardó su historial: se desbloquea todo."""
        self._deteniendo_vigilancia = False
        self._carpeta_historial_vigilada = None
        self.etiqueta_estado.configure(text="esperando ficheros...")
        self._establecer_carpetas_editables(True)
        self._actualizar_botones()

    def _establecer_carpetas_editables(self, editables: bool) -> None:
        """Bloquea el origen y el destino mientras se vigila."""
        estado: Literal["normal", "disabled"] = "normal" if editables else "disabled"
        self.ruta.campo.configure(state=estado)
        self.destino.campo.configure(state=estado)
        self.casilla_destino.state(["!disabled"] if editables else ["disabled"])
        self.boton_examinar_destino.establecer_habilitado(editables)

    def detener_vigilancia(self) -> None:
        """Detiene la vigilancia (si la hay) y espera a que termine; se usa al cerrar la ventana."""
        vigilante = self._vigilante
        self._vigilante = None
        self._carpeta_historial_vigilada = None
        if vigilante is not None:
            # Al cerrar la ventana no hay donde mostrar un error: se ignora para poder salir.
            with contextlib.suppress(Exception):
                vigilante.detener()

    def _notificador_vigilante(self, nivel: str, mensaje: str) -> None:
        """Muestra en el registro los avisos del vigilante, que llegan desde su hilo."""
        etiqueta = tema.ETIQUETAS_NIVEL.get(nivel, "")

        def actualizar() -> None:
            if etiqueta:
                self._registrar(nivel, etiqueta, mensaje)
            else:
                self._registrar_atenuado(mensaje)

        self.marco.after(0, actualizar)

    def _pegar_ruta(self, evento: tk.Event | None = None) -> str:
        """Pega la ruta del portapapeles en el campo de la carpeta de origen."""
        return self._pegar_en(self.ruta)

    def _pegar_destino(self, evento: tk.Event | None = None) -> str:
        """Pega la ruta del portapapeles en el campo de la carpeta de destino."""
        return self._pegar_en(self.destino)

    def _pegar_en(self, campo: CampoRedondeado) -> str:
        """Pega en el campo la ruta del portapapeles, sin comillas."""
        if self._avisar_si_vigilando("cambiar de carpeta"):
            return "break"
        try:
            texto = limpiar_texto_ruta(self.marco.clipboard_get())
        except tk.TclError:
            self._registrar("aviso", "[AVISO]", "El portapapeles está vacío")
            return "break"
        if texto:
            campo.establecer(texto)
        return "break"

    def _guardar_preferencias(self, carpeta: str) -> None:
        """Recuerda la carpeta y las opciones elegidas (las comparte con el menú de la consola)."""
        try:
            self._config.actualizar(
                ultima_carpeta=carpeta,
                usar_destino=self._var_usar_destino.get(),
                ultimo_destino=limpiar_texto_ruta(self.destino.obtener()),
                backup=self._var_backup.get(),
                recursivo_watch=self._var_recursivo.get(),
                modo_integridad=self._var_integridad.get(),
                agrupar=self._var_agrupar.get(),
                mover_duplicados=self._var_duplicados.get(),
            )
            self._config.guardar()
        except OSError:
            pass

    def _abrir_reglas(self) -> None:
        """Abre la ventana de reglas de la carpeta elegida."""
        carpeta = self._leer_carpeta("Selecciona una carpeta para ver sus reglas")
        if carpeta is None:
            return
        reglas = ReglasCarpeta(Path(carpeta))
        try:
            reglas.cargar()
        except ErrorReglas as e:
            self._registrar("error", "[ERROR]", str(e))
            messagebox.showerror("Reglas de la carpeta", f"{e}\n\nCorrige o borra el fichero.")
            return
        self.dialogo_reglas = DialogoReglas(
            self.marco,
            reglas,
            self.fuentes,
            agrupa_por_fecha=self._criterio_agrupacion() is not CriterioAgrupacion.TIPO,
        )

    def _exportar_categorias(self) -> None:
        """Guarda las categorías en uso en un JSON, para editarlas e importarlas después."""
        ruta = filedialog.asksaveasfilename(
            title="Exportar categorías",
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
        )
        if not ruta:
            return
        try:
            # El diálogo de guardar ya ha pedido confirmación si el fichero existía.
            escrita = Clasificador(ruta_config=self._config_categorias).exportar_categorias(
                ruta, sobrescribir=True
            )
            self._registrar("exito", "[ÉXITO]", f"Categorías exportadas a {escrita}")
        except (OSError, ErrorConfiguracionNoEncontrada, ErrorFormatoConfiguracion) as e:
            self._registrar("error", "[ERROR]", f"No se pudo exportar: {e}")

    def _importar_categorias(self) -> None:
        """Carga las categorías de un JSON y las deja en uso para organizar."""
        ruta = filedialog.askopenfilename(
            title="Importar categorías",
            filetypes=[("JSON", "*.json")],
        )
        if not ruta:
            return
        try:
            categorias = Clasificador(ruta_config=ruta).obtener_categorias()
        except (ErrorConfiguracionNoEncontrada, ErrorFormatoConfiguracion) as e:
            self._registrar("error", "[ERROR]", f"No se pueden usar esas categorías: {e}")
            messagebox.showerror("Importar categorías", str(e))
            return
        self._config_categorias = ruta
        self._registrar(
            "exito",
            "[ÉXITO]",
            f"{len(categorias)} categoría(s) importada(s) de {ruta}; se usarán al organizar "
            "y al vigilar.",
        )

    # Alias públicos usados por los atajos de teclado (Ctrl+O, Ctrl+Enter).
    def examinar(self) -> None:
        """Abre el diálogo para elegir carpeta (atajo Ctrl+O)."""
        self._elegir_carpeta()

    def organizar(self) -> None:
        """Lanza la organización (atajo Ctrl+Enter)."""
        self._organizar()
