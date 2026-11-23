"""Ventana principal de la interfaz gráfica, con las pestañas Organizar, Historial y Cifrado.

Se abre con ``file-organizer-gui`` o ``gui.py``. Tema oscuro e icono dibujados en código, sin
ficheros de imagen.
"""

from __future__ import annotations

import tkinter as tk

from file_organizer.gui import tema
from file_organizer.gui.componentes import BarraPestanas, MarcoRedondeado
from file_organizer.gui.monitores import Monitor, monitor_de_trabajo
from file_organizer.gui.pestana_cifrado import PestanaCifrado
from file_organizer.gui.pestana_historial import PestanaHistorial
from file_organizer.gui.pestana_organizar import PestanaOrganizar

TITULO_VENTANA = "File Organizer"
# Tamaño con el que se abre la ventana si la pantalla es lo bastante grande (ancho, alto).
TAMANO_VENTANA = (900, 680)
# Por debajo de este tamaño ya no caben todos los controles de las pestañas.
TAMANO_MINIMO_VENTANA = (760, 620)
# En pantallas pequeñas la ventana se abre como mucho a esta fracción de la pantalla.
FRACCION_MAXIMA_PANTALLA = 0.8
# Alto aproximado de la barra de tareas (o del menú y el dock) más la barra de título:
# se descuenta al centrar en vertical para que la ventana no quede debajo de la barra.
RESERVA_VERTICAL = 80
MARGEN_EXTERIOR = 24

# Tkinter no retiene las imágenes: hay que guardarlas o desaparecen de la ventana.
_IMAGENES_EN_USO: list[tk.PhotoImage] = []


def calcular_geometria(monitor: Monitor) -> str:
    """Geometría inicial ("ANCHOxALTO+X+Y") de la ventana, centrada en ``monitor``.

    Usa el tamaño preferido; en pantallas pequeñas lo reduce para no ocuparlas enteras, sin
    bajar del mínimo.
    """
    ancho_preferido, alto_preferido = TAMANO_VENTANA
    ancho_minimo, alto_minimo = TAMANO_MINIMO_VENTANA
    ancho = max(ancho_minimo, min(ancho_preferido, int(monitor.ancho * FRACCION_MAXIMA_PANTALLA)))
    alto = max(alto_minimo, min(alto_preferido, int(monitor.alto * FRACCION_MAXIMA_PANTALLA)))
    x = monitor.x + max(0, (monitor.ancho - ancho) // 2)
    y = monitor.y + max(0, (monitor.alto - RESERVA_VERTICAL - alto) // 2)
    return f"{ancho}x{alto}+{x}+{y}"


def construir_aplicacion() -> tk.Tk:
    """Crea la ventana principal con sus tres pestañas y el tema aplicado."""
    raiz = tk.Tk()
    raiz.title(TITULO_VENTANA)
    # En Linux con varios monitores, Tk ve un único escritorio que los une a todos: se usa el
    # monitor donde está el ratón para que la ventana no quede partida entre dos.
    raiz.geometry(calcular_geometria(monitor_de_trabajo(raiz)))
    raiz.minsize(*TAMANO_MINIMO_VENTANA)

    fuentes = tema.aplicar_tema(raiz)

    try:
        icono = tema.crear_icono(raiz)
        raiz.iconphoto(True, icono)
        _IMAGENES_EN_USO.append(icono)
    except tk.TclError:
        pass

    exterior = tk.Frame(raiz, bg=tema.FONDO_EXTERIOR)
    exterior.pack(fill="both", expand=True, padx=MARGEN_EXTERIOR, pady=MARGEN_EXTERIOR)

    tarjeta = MarcoRedondeado(
        exterior,
        fondo=tema.FONDO_TARJETA,
        radio=tema.RADIO_TARJETA,
        color_borde=tema.BORDE_TARJETA,
        fondo_exterior=tema.FONDO_EXTERIOR,
    )
    tarjeta.pack(fill="both", expand=True)

    pestanas = BarraPestanas(
        tarjeta.interior,
        etiquetas=["Organizar", "Historial", "Cifrado"],
        fondo_exterior=tema.FONDO_TARJETA,
    )
    pestanas.pack(fill="both", expand=True, padx=20, pady=(10, 0))

    pestana_organizar = PestanaOrganizar(pestanas.contenido, fuentes)
    pestanas.agregar_pagina("Organizar", pestana_organizar.marco)

    pestana_historial = PestanaHistorial(
        pestanas.contenido,
        fuentes,
        carpeta_vigilada=pestana_organizar.carpeta_historial_vigilada,
    )
    pestanas.agregar_pagina("Historial", pestana_historial.marco)

    pestana_cifrado = PestanaCifrado(pestanas.contenido, fuentes)
    pestanas.agregar_pagina("Cifrado", pestana_cifrado.marco)

    def _al_cerrar() -> None:
        pestana_organizar.detener_vigilancia()
        raiz.destroy()

    raiz.protocol("WM_DELETE_WINDOW", _al_cerrar)

    # Atajos de teclado: Ctrl+O para examinar carpeta, Ctrl+Enter para organizar.
    def _atajo_examinar(evento: tk.Event) -> str:
        pestana_organizar.examinar()
        return "break"

    def _atajo_organizar(evento: tk.Event) -> str:
        pestana_organizar.organizar()
        return "break"

    for secuencia in ("<Control-o>", "<Control-O>"):
        raiz.bind_all(secuencia, _atajo_examinar)
    for secuencia in ("<Control-Return>", "<Control-KP_Enter>"):
        raiz.bind_all(secuencia, _atajo_organizar)

    return raiz


def main() -> None:
    """Abre la interfaz gráfica."""
    raiz = construir_aplicacion()
    raiz.mainloop()


if __name__ == "__main__":
    main()
