"""Pestaña Historial: ver los pasos guardados de una carpeta, sus estadísticas o borrarlos.

El historial está en la carpeta donde se organizaron los ficheros: si se organizó en otra
carpeta, se consulta indicando esa. Las operaciones van en hilos aparte para no congelar la
ventana.
"""

from __future__ import annotations

import threading
import tkinter as tk
from collections import Counter, defaultdict
from collections.abc import Callable
from functools import partial
from pathlib import Path
from tkinter import messagebox, ttk

from file_organizer.excepciones import ErrorHistorialNoEncontrado, ErrorOrganizador
from file_organizer.gui import tema
from file_organizer.gui.componentes import (
    BotonRedondeado,
    CabeceraSeccion,
    CampoRedondeado,
    InfoEmergente,
    PanelRegistro,
    Trozo,
    leer_ruta,
)
from file_organizer.historial import (
    Operacion,
    RegistroMovimiento,
    leer_carpeta_origen_historial,
)
from file_organizer.organizador import OrganizadorArchivos
from file_organizer.rutas import comprobar_carpeta

# Resultado de la carga cuando la carpeta no tiene historial (no es un error).
_SIN_HISTORIAL = "sin_historial"
# Ancho máximo de la barra de las estadísticas (en caracteres, para el 100 %).
ANCHO_BARRA_ESTADISTICAS = 20


def _organizador_para(carpeta: str) -> OrganizadorArchivos:
    """Organizador para consultar el historial guardado en ``carpeta``.

    Si esa carpeta fue el destino de otra, toma el origen que figura en el historial; si no, la
    usa como origen y destino.
    """
    origen = leer_carpeta_origen_historial(Path(carpeta)) or carpeta
    return OrganizadorArchivos(carpeta_origen=origen, carpeta_destino=carpeta)


class PestanaHistorial:
    """Pestaña de historial: ver los pasos guardados y sus estadísticas, o borrarlos."""

    def __init__(
        self,
        maestro: tk.Misc,
        fuentes: dict[str, tema.Fuente],
        carpeta_vigilada: Callable[[], Path | None] | None = None,
    ) -> None:
        self.fuentes = fuentes
        self._operacion_en_curso = False
        # Carpeta cuyo historial está escribiendo el vigilante (pestaña Organizar), si hay.
        self._carpeta_vigilada = carpeta_vigilada or (lambda: None)

        self.marco = ttk.Frame(maestro, style="Tarjeta.TFrame")
        self._construir_ui()

    def _construir_ui(self) -> None:
        """Construye los widgets de la pestaña."""
        contenido = ttk.Frame(self.marco, style="Tarjeta.TFrame")
        contenido.pack(fill="both", expand=True, padx=20, pady=(14, 14))

        CabeceraSeccion(contenido, texto="Ver historial", fondo_exterior=tema.FONDO_TARJETA).pack(
            fill="x"
        )

        ttk.Label(contenido, text="RUTA", style="Etiqueta.TLabel").pack(anchor="w", pady=(12, 6))

        self.ruta = CampoRedondeado(contenido, fondo_exterior=tema.FONDO_TARJETA)
        self.ruta.pack(fill="x")

        fila_botones = ttk.Frame(contenido, style="Tarjeta.TFrame")
        fila_botones.pack(fill="x", pady=(14, 18))

        self.boton_examinar = BotonRedondeado(
            fila_botones,
            texto="Examinar...",
            variante="secundario",
            comando=self._elegir_carpeta,
            fondo_exterior=tema.FONDO_TARJETA,
            padx=tema.PADX_FILA_BOTONES,
        )
        self.boton_examinar.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.boton_cargar = BotonRedondeado(
            fila_botones,
            texto="Ver historial",
            variante="acento",
            comando=self._cargar_historial,
            fondo_exterior=tema.FONDO_TARJETA,
            padx=tema.PADX_FILA_BOTONES,
        )
        self.boton_cargar.pack(side="left", fill="x", expand=True, padx=6)

        self.boton_estadisticas = BotonRedondeado(
            fila_botones,
            texto="Estadísticas",
            variante="secundario",
            comando=self._ver_estadisticas,
            fondo_exterior=tema.FONDO_TARJETA,
            padx=tema.PADX_FILA_BOTONES,
        )
        self.boton_estadisticas.pack(side="left", fill="x", expand=True, padx=6)

        # «Limpiar» a secas, para que los cuatro botones quepan en la ventana mínima con
        # cualquier fuente. El nombre completo sale en la ayuda y en la confirmación.
        self.boton_limpiar = BotonRedondeado(
            fila_botones,
            texto="Limpiar",
            variante="peligro",
            comando=self._limpiar_historial,
            fondo_exterior=tema.FONDO_TARJETA,
            padx=tema.PADX_FILA_BOTONES,
        )
        self.boton_limpiar.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self.ayuda_limpiar = InfoEmergente(
            self.boton_limpiar,
            texto="Limpiar historial: borra el historial guardado de la carpeta.\n"
            "Los ficheros no se mueven, pero ya no se podrá deshacer.",
            fuente=self.fuentes["pequena"],
        )
        fila_cabecera = ttk.Frame(contenido, style="Tarjeta.TFrame")
        fila_cabecera.pack(fill="x")
        ttk.Label(fila_cabecera, text="MOVIMIENTOS GUARDADOS", style="Cabecera.TLabel").pack(
            side="left"
        )

        self.panel_registro = PanelRegistro(contenido, self.fuentes["registro"], alto_lineas=8)
        self.panel_registro.pack(fill="both", expand=True, pady=(10, 0))
        self.registro = self.panel_registro.texto
        self.registro.tag_configure(
            "categoria", foreground=tema.CIAN, font=self.fuentes["cabecera"]
        )

    def _limpiar_panel(self) -> None:
        """Vacía el panel de registro."""
        self.panel_registro.limpiar()

    def _registrar_linea(self, nivel: str, etiqueta: str, mensaje: str) -> None:
        """Añade una línea coloreada al panel de registro."""
        self.panel_registro.registrar(nivel, etiqueta, mensaje)

    def _registrar_atenuado(self, mensaje: str) -> None:
        """Añade una línea atenuada al panel de registro."""
        self.panel_registro.registrar_atenuado(mensaje)

    def _registrar_categoria(self, categoria: str, cantidad: int) -> None:
        """Cabecera de una categoría con su número de ficheros."""
        self.panel_registro.escribir(
            (f"\n{categoria}/", "categoria"), (f" ({cantidad} fichero(s))\n", "atenuado")
        )

    def _registrar_movimiento(self, nombre: str, fecha: str) -> None:
        """Un fichero movido, con su fecha."""
        self.panel_registro.escribir(
            (f"  {nombre}\n", "exito_mensaje"), (f"  {fecha}\n", "atenuado")
        )

    def _elegir_carpeta(self) -> None:
        """Abre el diálogo para elegir la carpeta."""
        self.ruta.elegir_carpeta("Selecciona la carpeta")

    def _cargar_historial(self) -> None:
        """Muestra los pasos guardados, cada uno agrupado por categoría."""
        self._lanzar_carga(estadisticas=False)

    def _ver_estadisticas(self) -> None:
        """Muestra cuántos ficheros fueron a cada categoría en el último paso."""
        self._lanzar_carga(estadisticas=True)

    def _leer_carpeta(self) -> str | None:
        """La carpeta elegida, si existe; si falta o no sirve, lo avisa en el panel (vacío antes)
        y devuelve None.
        """
        carpeta = leer_ruta(
            self.ruta, comprobar_carpeta, "Selecciona una carpeta primero", self._avisar_solo
        )
        return None if carpeta is None else str(carpeta)

    def _avisar_solo(self, nivel: str, etiqueta: str, texto: str) -> None:
        """Vacía el panel y deja solo este aviso."""
        self._limpiar_panel()
        self._registrar_linea(nivel, etiqueta, texto)

    def _lanzar_carga(self, estadisticas: bool) -> None:
        """Carga el historial en un hilo aparte, para mostrarlo o para sus estadísticas."""
        if self._operacion_en_curso:
            return

        carpeta = self._leer_carpeta()
        if carpeta is None:
            return

        self._establecer_botones_habilitados(False)
        self._operacion_en_curso = True
        self._limpiar_panel()

        threading.Thread(
            target=self._trabajador_cargar_historial, args=(carpeta, estadisticas), daemon=True
        ).start()

    def _trabajador_cargar_historial(self, carpeta: str, estadisticas: bool = False) -> None:
        """Hilo de trabajo: carga el historial y pasa el resultado a la interfaz."""
        terminar = partial(self._carga_historial_terminada, estadisticas=estadisticas)
        try:
            org = _organizador_para(carpeta)
            operaciones = org.cargar_historial()
            origen = org.carpeta_origen if org.carpeta_origen != org.carpeta_destino else None
            self.marco.after(0, partial(terminar, operaciones, None, origen))
        except ErrorHistorialNoEncontrado:
            self.marco.after(0, partial(terminar, None, _SIN_HISTORIAL))
        except ErrorOrganizador as e:
            self.marco.after(0, partial(terminar, None, e))
        except Exception as e:
            self.marco.after(0, partial(terminar, None, e))

    def _carga_historial_terminada(
        self,
        operaciones: list[Operacion] | None,
        error: Exception | str | None,
        origen: Path | None = None,
        estadisticas: bool = False,
    ) -> None:
        """Muestra los pasos del historial, o las estadísticas del último.

        ``origen`` es la carpeta desde la que se organizó, si no es la consultada.
        """
        self._establecer_botones_habilitados(True)
        self._operacion_en_curso = False

        if error == _SIN_HISTORIAL:
            self._registrar_atenuado("No hay historial guardado en esta carpeta.")
            self._registrar_atenuado(
                "Si la organizaste en otra carpeta de destino, elige esa carpeta."
            )
            return

        if error is not None:
            self._registrar_linea("error", "[ERROR]", str(error))
            return

        if not operaciones:
            self._registrar_atenuado("El historial está vacío.")
            return

        if origen is not None:
            self._registrar_atenuado(f"Ficheros organizados desde {origen}")

        if estadisticas:
            self._mostrar_estadisticas(operaciones[-1])
            return

        # Del más reciente (el que se deshará primero) al más antiguo.
        for numero, operacion in enumerate(reversed(operaciones), start=1):
            self._registrar_paso(numero, operacion)
            por_categoria: dict[str, list[RegistroMovimiento]] = defaultdict(list)
            for registro in operacion.registros:
                por_categoria[registro.categoria].append(registro)
            for categoria, registros in sorted(por_categoria.items()):
                self._registrar_categoria(categoria, len(registros))
                for registro in registros:
                    self._registrar_movimiento(
                        registro.origen.name, registro.fecha.strftime("%d/%m/%Y %H:%M:%S")
                    )

        total = sum(len(o.registros) for o in operaciones)
        self._registrar_atenuado(
            f"\nTotal: {total} movimiento(s) en {len(operaciones)} paso(s). «Deshacer» "
            "(pestaña Organizar) los revierte de uno en uno, empezando por el paso 1."
        )
        # Se empieza a leer por arriba: el paso 1 es el que revertirá el siguiente «Deshacer».
        self.registro.yview_moveto(0)

    def _registrar_paso(self, numero: int, operacion: Operacion) -> None:
        """Cabecera de un paso: su número, qué fue y cuántos ficheros movió."""
        self.panel_registro.escribir(
            (f"\nPASO {numero}", "info"),
            (
                f" · {operacion.descripcion()} ({len(operacion.registros)} fichero(s))\n",
                "info_mensaje",
            ),
        )

    def _mostrar_estadisticas(self, operacion: Operacion) -> None:
        """Una barra por categoría del paso, con sus ficheros y su porcentaje."""
        historial = operacion.registros
        por_categoria = Counter(registro.categoria for registro in historial)
        total = len(historial)
        ancho_nombre = max(len(categoria) for categoria in por_categoria)

        trozos: list[Trozo] = [
            ("ESTADÍSTICAS DEL ÚLTIMO PASO\n", "categoria"),
            (f"{operacion.descripcion()}\n\n", "atenuado"),
        ]
        for categoria, cantidad in por_categoria.most_common():
            porcentaje = cantidad / total * 100
            barra = "█" * max(1, round(porcentaje / 100 * ANCHO_BARRA_ESTADISTICAS))
            trozos += [
                (f"{categoria:<{ancho_nombre}}  ", "info"),
                (f"{barra:<{ANCHO_BARRA_ESTADISTICAS}}  ", "exito"),
                (f"{cantidad:>4} fichero(s)  {porcentaje:5.1f} %\n", "exito_mensaje"),
            ]
        trozos.append(
            (
                f"\n{'TOTAL':<{ancho_nombre}}  {'':<{ANCHO_BARRA_ESTADISTICAS}}  {total:>4} fichero(s)\n",
                "atenuado",
            )
        )
        self.panel_registro.escribir(*trozos, mostrar_final=True)

    def _limpiar_historial(self) -> None:
        """Pide confirmación y borra el historial en un hilo aparte (bloqueado si se está
        vigilando).
        """
        if self._operacion_en_curso:
            return

        carpeta = self._leer_carpeta()
        if carpeta is None:
            return

        vigilada = self._carpeta_vigilada()
        if vigilada is not None and Path(carpeta).resolve() == vigilada:
            self._limpiar_panel()
            self._registrar_linea(
                "aviso",
                "[AVISO]",
                "Se está vigilando esta carpeta: detén la vigilancia antes de limpiar su "
                "historial (el vigilante lo volvería a escribir).",
            )
            return

        confirmado = messagebox.askyesno(
            "Limpiar historial",
            "Se borrará el historial de esta carpeta.\n"
            "Los ficheros se quedan donde están, pero ya no se podrá deshacer lo organizado.\n\n"
            "¿Deseas continuar?",
        )
        if not confirmado:
            return

        self._establecer_botones_habilitados(False)
        self._operacion_en_curso = True

        threading.Thread(
            target=self._trabajador_limpiar_historial, args=(carpeta,), daemon=True
        ).start()

    def _trabajador_limpiar_historial(self, carpeta: str) -> None:
        """Hilo de trabajo: borra el historial y pasa el resultado a la interfaz."""
        try:
            # Bloquea la carpeta: falla con un aviso si otra ventana la está vigilando.
            cantidad = _organizador_para(carpeta).limpiar_historial()
            self.marco.after(0, lambda: self._limpieza_historial_terminada(cantidad, None))
        except ErrorHistorialNoEncontrado:
            self.marco.after(0, lambda: self._limpieza_historial_terminada(0, _SIN_HISTORIAL))
        except ErrorOrganizador as e:
            self.marco.after(0, partial(self._limpieza_historial_terminada, 0, e))
        except Exception as e:
            self.marco.after(0, partial(self._limpieza_historial_terminada, 0, e))

    def _limpieza_historial_terminada(self, cantidad: int, error: Exception | str | None) -> None:
        """Muestra en el registro el resultado de borrar el historial."""
        self._establecer_botones_habilitados(True)
        self._operacion_en_curso = False

        self._limpiar_panel()

        if error == _SIN_HISTORIAL:
            self._registrar_atenuado("No había historial que limpiar.")
            return

        if error is not None:
            self._registrar_linea("error", "[ERROR]", f"No se pudo limpiar el historial: {error}")
            return

        self._registrar_linea(
            "exito",
            "[ÉXITO]",
            f"Historial borrado ({cantidad} movimiento(s)); los ficheros siguen donde estaban.",
        )

    def _establecer_botones_habilitados(self, habilitados: bool) -> None:
        """Habilita o deshabilita los botones mientras hay una operación en curso."""
        self.boton_examinar.establecer_habilitado(habilitados)
        self.boton_cargar.establecer_habilitado(habilitados)
        self.boton_estadisticas.establecer_habilitado(habilitados)
        self.boton_limpiar.establecer_habilitado(habilitados)
