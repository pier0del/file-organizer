"""Colores, fuentes, medidas y estilos ttk de toda la interfaz gráfica."""

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

# Fuente de tkinter: (familia, tamaño) o (familia, tamaño, estilo).
Fuente = tuple[str, int] | tuple[str, int, str]

FONDO_EXTERIOR = "#050607"
FONDO_TARJETA = "#0d0f12"
FONDO_SUPERFICIE = "#14171b"
FONDO_CAMPO = "#111418"
FONDO_REGISTRO = "#0a0c0f"
FONDO_RESALTADO = "#1a1e24"

BORDE_TARJETA = "#1c1f24"
BORDE_CAMPO = "#22262c"
BORDE_BOTON = "#262a30"
BORDE_PELIGRO = "#3a2226"

TEXTO_PRINCIPAL = "#e4e6e9"
TEXTO_SECUNDARIO = "#c7cbd1"
TEXTO_ETIQUETA = "#7a828c"
TEXTO_ATENUADO = "#4a4f57"
TEXTO_MENSAJE_INFO = "#9aa1ab"
TEXTO_DESHABILITADO = "#3a3d43"

CIAN = "#5fd0e0"
VERDE = "#39d98a"
AMARILLO = "#e0b23e"
ROJO = "#e8697d"
VERDE_SOBRE_FONDO = "#07130c"

# Niveles de los mensajes del panel de registro (los mismos que los avisos del vigilante).
COLORES_NIVEL = {
    "info": CIAN,
    "espera": TEXTO_ATENUADO,
    "exito": VERDE,
    "aviso": AMARILLO,
    "error": ROJO,
}

COLORES_MENSAJE_NIVEL = {
    "info": TEXTO_MENSAJE_INFO,
    "espera": TEXTO_ATENUADO,
    "exito": TEXTO_SECUNDARIO,
    "aviso": TEXTO_SECUNDARIO,
    "error": TEXTO_SECUNDARIO,
}

# Etiqueta que se muestra delante de cada mensaje según su nivel.
ETIQUETAS_NIVEL = {
    "info": "[INFO]",
    "exito": "[ÉXITO]",
    "aviso": "[AVISO]",
    "error": "[ERROR]",
}

_FUENTES_MONO_CANDIDATAS = [
    "JetBrains Mono",
    "Cascadia Mono",
    "Cascadia Code",
    "Consolas",
    "SF Mono",
    "Menlo",
    "Monaco",
    "DejaVu Sans Mono",
    "Liberation Mono",
    "Courier New",
    "Courier",
]

TAMANO_FUENTE_NORMAL = 11
TAMANO_FUENTE_CABECERA = 12
TAMANO_FUENTE_PEQUENA = 10
TAMANO_FUENTE_REGISTRO = 11

RADIO_TARJETA = 16
RADIO_BOTON = 10
# Margen lateral de los botones de las filas que se reparten el ancho (Organizar, Historial):
# con poco margen caben en la ventana mínima, y al estirarse se ven igual.
PADX_FILA_BOTONES = 12

# Estilo y alto (en píxeles) de la barra de progreso.
ESTILO_PROGRESO = "Progreso.Horizontal.TProgressbar"
ALTO_BARRA_PROGRESO = 6


def _primera_disponible(raiz: tk.Misc, candidatas: list[str]) -> str | None:
    """La primera fuente de la lista que esté instalada, o None."""
    disponibles = set(tkfont.families(raiz))
    for candidata in candidatas:
        if candidata in disponibles:
            return candidata
    return None


_fuente_mono: str | None = None


def obtener_fuente_mono(raiz: tk.Misc) -> str:
    """Una fuente monoespaciada instalada (o la de Tk por defecto).

    Se elige una vez por proceso: consultar las fuentes del sistema es caro con muchas
    instaladas.
    """
    global _fuente_mono
    if _fuente_mono is None:
        _fuente_mono = _primera_disponible(raiz, _FUENTES_MONO_CANDIDATAS) or "TkFixedFont"
    return _fuente_mono


def crear_icono(raiz: tk.Misc, tamano: int = 32) -> tk.PhotoImage:
    """El icono de la aplicación (una carpeta verde), dibujado píxel a píxel."""
    icono = tk.PhotoImage(master=raiz, width=tamano, height=tamano)
    escala = tamano / 32.0

    def _dentro(x: float, y: float, x0: float, y0: float, x1: float, y1: float) -> bool:
        return x0 <= x < x1 and y0 <= y < y1

    filas = []
    for y in range(tamano):
        fila = []
        for x in range(tamano):
            px, py = x / escala, y / escala
            if _dentro(px, py, 5, 6, 15, 10) or _dentro(px, py, 5, 9, 27, 25):
                color = VERDE
            else:
                color = FONDO_TARJETA
            fila.append(color)
        filas.append("{" + " ".join(fila) + "}")
    icono.put(" ".join(filas))
    return icono


def configurar_etiquetas_registro(widget_texto: tk.Text) -> None:
    """Crea los colores de un panel de registro.

    Un tag por nivel (para la etiqueta ``[INFO]``...), otro ``<nivel>_mensaje`` para el texto y
    ``atenuado`` para las líneas secundarias.
    """
    for nivel, color in COLORES_NIVEL.items():
        widget_texto.tag_configure(nivel, foreground=color)
    for nivel, color in COLORES_MENSAJE_NIVEL.items():
        widget_texto.tag_configure(nivel + "_mensaje", foreground=color)
    widget_texto.tag_configure("atenuado", foreground=TEXTO_ATENUADO)


def mostrar_final_registro(widget_texto: tk.Text) -> None:
    """Desplaza un panel de registro para que su última línea se vea entera.

    Usa "end-2c" (el último carácter escrito) y no "end": "end" es la línea vacía que sigue al
    último salto de línea, y con ella la última línea real podía quedar cortada por abajo.
    """
    widget_texto.see("end-2c")


def aplicar_tema(raiz: tk.Tk) -> dict[str, Fuente]:
    """Aplica el tema oscuro a la ventana y a los estilos ttk; devuelve las fuentes a usar."""
    mono = obtener_fuente_mono(raiz)

    fuentes: dict[str, Fuente] = {
        "normal": (mono, TAMANO_FUENTE_NORMAL),
        "cabecera": (mono, TAMANO_FUENTE_CABECERA, "bold"),
        "pequena": (mono, TAMANO_FUENTE_PEQUENA),
        "registro": (mono, TAMANO_FUENTE_REGISTRO),
    }

    raiz.configure(bg=FONDO_EXTERIOR)
    raiz.option_add("*Font", fuentes["normal"])

    estilo = ttk.Style(raiz)
    estilo.theme_use("clam")

    estilo.configure("TFrame", background=FONDO_EXTERIOR)
    estilo.configure("Tarjeta.TFrame", background=FONDO_TARJETA)

    estilo.configure(
        "TLabel", background=FONDO_EXTERIOR, foreground=TEXTO_PRINCIPAL, font=fuentes["normal"]
    )
    estilo.configure(
        "Tarjeta.TLabel",
        background=FONDO_TARJETA,
        foreground=TEXTO_PRINCIPAL,
        font=fuentes["normal"],
    )
    estilo.configure(
        "Cabecera.TLabel", background=FONDO_TARJETA, foreground=CIAN, font=fuentes["cabecera"]
    )
    estilo.configure(
        "Etiqueta.TLabel",
        background=FONDO_TARJETA,
        foreground=TEXTO_ETIQUETA,
        font=fuentes["pequena"],
    )
    estilo.configure(
        "Atenuado.TLabel",
        background=FONDO_TARJETA,
        foreground=TEXTO_ATENUADO,
        font=fuentes["pequena"],
    )

    for clase in ("TCheckbutton", "TRadiobutton"):
        estilo.configure(
            clase,
            background=FONDO_TARJETA,
            foreground=TEXTO_PRINCIPAL,
            font=fuentes["normal"],
            focuscolor=CIAN,
        )
        estilo.map(clase, background=[("active", FONDO_TARJETA)])

    estilo.configure(
        "Vertical.TScrollbar",
        background=FONDO_SUPERFICIE,
        troughcolor=FONDO_TARJETA,
        bordercolor=FONDO_TARJETA,
        arrowcolor=TEXTO_ETIQUETA,
    )
    estilo.map("Vertical.TScrollbar", background=[("active", FONDO_RESALTADO)])

    # Barra de progreso fina y plana: fondo como el de los campos y relleno verde.
    estilo.configure(
        ESTILO_PROGRESO,
        troughcolor=FONDO_CAMPO,
        background=VERDE,
        bordercolor=FONDO_CAMPO,
        lightcolor=VERDE,
        darkcolor=VERDE,
        arrowsize=ALTO_BARRA_PROGRESO,  # en el tema clam, el grosor de la barra
    )

    return fuentes
