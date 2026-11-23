"""Monitor en el que abrir la ventana cuando hay varios (Linux).

En Linux con varios monitores, Tk ve un único escritorio que los une a todos: centrar la ventana
en él la dejaría partida entre dos. Aquí se pide a X11 la lista real de monitores (Xinerama, con
ctypes y las bibliotecas que ya usa Tk) y se elige el del ratón. En Windows y macOS, o si no se
puede saber, se usa la pantalla completa.
"""

from __future__ import annotations

import ctypes
import ctypes.util
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import tkinter as tk


@dataclass(frozen=True)
class Monitor:
    """Rectángulo que ocupa un monitor dentro del escritorio, en píxeles."""

    x: int
    y: int
    ancho: int
    alto: int

    def contiene(self, x: int, y: int) -> bool:
        """Si el punto (x, y) del escritorio está en este monitor."""
        return self.x <= x < self.x + self.ancho and self.y <= y < self.y + self.alto


class _InfoMonitorXinerama(ctypes.Structure):
    """``XineramaScreenInfo`` de ``<X11/extensions/Xinerama.h>``."""

    _fields_ = (
        ("screen_number", ctypes.c_int),
        ("x_org", ctypes.c_short),
        ("y_org", ctypes.c_short),
        ("width", ctypes.c_short),
        ("height", ctypes.c_short),
    )


def _cargar_biblioteca(nombre: str, soname: str) -> ctypes.CDLL | None:
    """Biblioteca del sistema por su soname o, si no, buscándola por nombre; None si no está."""
    for candidato in (soname, ctypes.util.find_library(nombre)):
        if candidato:
            try:
                return ctypes.CDLL(candidato)
            except OSError:
                continue
    return None


def monitores_x11(pantalla: str | None = None) -> list[Monitor]:
    """Monitores que conoce el servidor X, según Xinerama (el principal, primero).

    ``pantalla`` es la pantalla de X (por defecto, la de ``DISPLAY``). Lista vacía si no se
    puede saber: bibliotecas no instaladas, servidor sin Xinerama o pantalla inaccesible.
    """
    x11 = _cargar_biblioteca("X11", "libX11.so.6")
    xinerama = _cargar_biblioteca("Xinerama", "libXinerama.so.1")
    if x11 is None or xinerama is None:
        return []

    try:
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        x11.XFree.argtypes = [ctypes.c_void_p]
        xinerama.XineramaIsActive.argtypes = [ctypes.c_void_p]
        xinerama.XineramaQueryScreens.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
        xinerama.XineramaQueryScreens.restype = ctypes.POINTER(_InfoMonitorXinerama)
    except AttributeError:  # una versión de las bibliotecas sin estas funciones
        return []

    conexion = x11.XOpenDisplay(pantalla.encode() if pantalla else None)
    if not conexion:
        return []
    try:
        if not xinerama.XineramaIsActive(conexion):
            return []
        cantidad = ctypes.c_int(0)
        info = xinerama.XineramaQueryScreens(conexion, ctypes.byref(cantidad))
        if not info:
            return []
        try:
            return [
                Monitor(info[i].x_org, info[i].y_org, info[i].width, info[i].height)
                for i in range(cantidad.value)
            ]
        finally:
            x11.XFree(info)
    finally:
        x11.XCloseDisplay(conexion)


def elegir_monitor(
    monitores: list[Monitor], puntero: tuple[int, int], pantalla_completa: Monitor
) -> Monitor:
    """El monitor donde está el ratón; si no está en ninguno, el primero; sin monitores, la
    pantalla completa.
    """
    for monitor in monitores:
        if monitor.contiene(*puntero):
            return monitor
    return monitores[0] if monitores else pantalla_completa


def monitor_de_trabajo(raiz: tk.Misc) -> Monitor:
    """Monitor en el que abrir la ventana: el del ratón (Linux) o la pantalla completa."""
    pantalla_completa = Monitor(0, 0, raiz.winfo_screenwidth(), raiz.winfo_screenheight())
    if raiz.tk.call("tk", "windowingsystem") != "x11":
        return pantalla_completa  # en Windows y macOS, Tk ya da el monitor principal
    return elegir_monitor(
        monitores_x11(raiz.winfo_screen()), raiz.winfo_pointerxy(), pantalla_completa
    )
