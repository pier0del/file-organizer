"""Tests de la elección del monitor en el que abrir la ventana (Linux con varios monitores).

No necesitan tkinter: la consulta a X11 se hace con ctypes y la ventana se simula. El test que
lee los monitores reales solo se ejecuta si hay una pantalla X disponible.
"""

import os

import pytest

from file_organizer.gui import monitores
from file_organizer.gui.monitores import Monitor, elegir_monitor, monitor_de_trabajo

# Un portátil de 1366x768 con un monitor de 1920x1080 a la derecha, alineados por abajo: X11
# los presenta como un solo escritorio de 3286x1080.
PORTATIL = Monitor(0, 312, 1366, 768)
EXTERNO = Monitor(1366, 0, 1920, 1080)
ESCRITORIO = Monitor(0, 0, 3286, 1080)


class _VentanaFalsa:
    """Lo mínimo de una ventana de Tk que usa monitor_de_trabajo."""

    def __init__(self, sistema: str, puntero: tuple[int, int]) -> None:
        self.sistema = sistema
        self.puntero = puntero
        self.tk = self

    def call(self, *args: str) -> str:
        """Responde a ``tk windowingsystem`` con el sistema indicado."""
        assert args == ("tk", "windowingsystem")
        return self.sistema

    def winfo_screenwidth(self) -> int:
        """Ancho de todo el escritorio."""
        return ESCRITORIO.ancho

    def winfo_screenheight(self) -> int:
        """Alto de todo el escritorio."""
        return ESCRITORIO.alto

    def winfo_screen(self) -> str:
        """La pantalla X de la ventana."""
        return ":0.0"

    def winfo_pointerxy(self) -> tuple[int, int]:
        """Dónde está el ratón."""
        return self.puntero


class TestMonitor:
    """Qué puntos pertenecen a un monitor."""

    def test_contiene_sus_puntos_y_no_los_del_borde_exterior(self):
        """Incluye su primera fila y columna y no la siguiente a la última; tampoco el hueco que
        deja encima un monitor más bajo.
        """
        assert EXTERNO.contiene(1366, 0)
        assert EXTERNO.contiene(3285, 1079)
        assert not EXTERNO.contiene(1365, 500)
        assert not EXTERNO.contiene(3286, 500)
        assert not PORTATIL.contiene(100, 311)  # encima del portátil no hay monitor


class TestElegirMonitor:
    """En qué monitor de la lista abrir la ventana."""

    def test_el_del_raton(self):
        """El monitor donde está el ratón."""
        assert elegir_monitor([PORTATIL, EXTERNO], (2000, 500), ESCRITORIO) == EXTERNO
        assert elegir_monitor([PORTATIL, EXTERNO], (300, 600), ESCRITORIO) == PORTATIL

    def test_si_el_raton_no_esta_en_ninguno_el_primero(self):
        """Si el ratón está en un hueco sin monitor, el primero de la lista."""
        assert elegir_monitor([EXTERNO, PORTATIL], (100, 100), ESCRITORIO) == EXTERNO

    def test_sin_lista_la_pantalla_completa(self):
        """Sin monitores conocidos, todo el escritorio."""
        assert elegir_monitor([], (100, 100), ESCRITORIO) == ESCRITORIO


class TestMonitorDeTrabajo:
    """El monitor elegido según el sistema de ventanas."""

    def test_en_linux_usa_el_monitor_del_raton(self, monkeypatch: pytest.MonkeyPatch):
        """En X11 se consultan los monitores y se usa el del ratón."""
        monkeypatch.setattr(monitores, "monitores_x11", lambda pantalla: [PORTATIL, EXTERNO])
        assert monitor_de_trabajo(_VentanaFalsa("x11", (2000, 500))) == EXTERNO
        assert monitor_de_trabajo(_VentanaFalsa("x11", (300, 600))) == PORTATIL

    def test_en_linux_sin_xinerama_usa_la_pantalla_completa(self, monkeypatch: pytest.MonkeyPatch):
        """Si X11 no sabe dar los monitores, se usa todo el escritorio."""
        monkeypatch.setattr(monitores, "monitores_x11", lambda pantalla: [])
        assert monitor_de_trabajo(_VentanaFalsa("x11", (2000, 500))) == ESCRITORIO

    @pytest.mark.parametrize("sistema", ["win32", "aqua"])
    def test_en_windows_y_macos_no_consulta_x11(
        self, monkeypatch: pytest.MonkeyPatch, sistema: str
    ):
        """Fuera de Linux no se llama a X11: ya reparten bien la pantalla por su cuenta."""

        def no_deberia_llamarse(pantalla: str) -> list[Monitor]:
            raise AssertionError("no hay que consultar X11 fuera de Linux")

        monkeypatch.setattr(monitores, "monitores_x11", no_deberia_llamarse)
        assert monitor_de_trabajo(_VentanaFalsa(sistema, (2000, 500))) == ESCRITORIO


class TestMonitoresX11:
    """La consulta de los monitores a X11 con ctypes, cuando no se puede."""

    def test_sin_bibliotecas_de_x_devuelve_lista_vacia(self, monkeypatch: pytest.MonkeyPatch):
        """Sin las bibliotecas de X instaladas no falla: la lista sale vacía."""
        monkeypatch.setattr(monitores, "_cargar_biblioteca", lambda nombre, soname: None)
        assert monitores.monitores_x11(":0") == []

    def test_con_bibliotecas_sin_las_funciones_devuelve_lista_vacia(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Si a la biblioteca le faltan las funciones (versión antigua), la lista sale vacía."""

        class BibliotecaIncompleta:
            def __getattr__(self, nombre: str) -> object:
                raise AttributeError(nombre)

        monkeypatch.setattr(
            monitores, "_cargar_biblioteca", lambda nombre, soname: BibliotecaIncompleta()
        )
        assert monitores.monitores_x11(":0") == []

    def test_con_una_pantalla_inaccesible_devuelve_lista_vacia(self):
        """Si no se puede abrir la pantalla, la lista sale vacía en lugar de fallar."""
        if monitores._cargar_biblioteca("X11", "libX11.so.6") is None:
            pytest.skip("No están las bibliotecas de X11")
        assert monitores.monitores_x11(":987") == []

    @pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="No hay pantalla X")
    def test_lee_los_monitores_de_la_pantalla_actual(self):
        """Con una pantalla X de verdad, cada monitor tiene tamaño y posición válidos."""
        encontrados = monitores.monitores_x11()
        if not encontrados:
            pytest.skip("La pantalla no tiene Xinerama o faltan sus bibliotecas")
        for monitor in encontrados:
            assert monitor.ancho > 0 and monitor.alto > 0
            assert monitor.x >= 0 and monitor.y >= 0
