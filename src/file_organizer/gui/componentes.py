"""Componentes reutilizables de la interfaz: botones, campos y marcos redondeados, panel de
registro, barra de progreso, barra de pestañas y ayudas emergentes; y leer una ruta de un campo.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from functools import partial
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Any, ClassVar, TypedDict

from file_organizer.excepciones import ErrorRuta
from file_organizer.gui import tema
from file_organizer.rutas import preparar_ruta


def _rgb(maestro: tk.Misc, color: str) -> tuple[int, int, int]:
    """Componentes (0-255) de un color de Tk, sea ``#rrggbb`` o un nombre como ``white``."""
    r, g, b = maestro.winfo_rgb(color)
    return r >> 8, g >> 8, b >> 8


def _imagen_rectangulo_redondeado(
    maestro: tk.Misc,
    ancho: int,
    alto: int,
    radio: int,
    fondo: str,
    borde: str | None,
    exterior: str,
) -> tk.PhotoImage:
    """Imagen de un rectángulo redondeado con borde de 1 px y esquinas suavizadas.

    Se calcula píxel a píxel para que se vea igual en todos los sistemas: los lados son rectas
    exactas y en las esquinas cada píxel mezcla los colores según cuánto lo cubre la curva
    (antialiasing). Los polígonos de Tk no suavizan y, en Windows, perdían trozos del borde.
    ``exterior`` es el color de fuera de las esquinas (el fondo del contenedor).
    """
    c_fondo, c_exterior = _rgb(maestro, fondo), _rgb(maestro, exterior)
    c_borde = _rgb(maestro, borde) if borde else c_fondo
    grosor = 1 if borde else 0

    def hexa(c: tuple[float, float, float]) -> str:
        return "#{:02x}{:02x}{:02x}".format(*(min(255, max(0, round(v))) for v in c))

    imagen = tk.PhotoImage(master=maestro, width=ancho, height=alto)
    imagen.put(hexa(c_borde), to=(0, 0, ancho, alto))
    if grosor:
        imagen.put(hexa(c_fondo), to=(grosor, grosor, ancho - grosor, alto - grosor))

    radio = max(0, min(radio, ancho // 2, alto // 2))
    if not radio:
        return imagen

    def color_esquina(x: int, y: int) -> str:
        """Color del píxel (x, y) de la esquina superior izquierda."""
        distancia = ((radio - x - 0.5) ** 2 + (radio - y - 0.5) ** 2) ** 0.5
        cubierto = min(1.0, max(0.0, radio - distancia + 0.5))  # dentro de la forma
        relleno = min(1.0, max(0.0, radio - grosor - distancia + 0.5)) if grosor else cubierto
        return hexa(
            tuple(
                e * (1 - cubierto) + b * (cubierto - relleno) + f * relleno
                for e, b, f in zip(c_exterior, c_borde, c_fondo, strict=True)
            )
        )

    esquina = [[color_esquina(x, y) for x in range(radio)] for y in range(radio)]
    for derecha in (False, True):
        for abajo in (False, True):
            filas = esquina[::-1] if abajo else esquina
            datos = " ".join(
                "{" + " ".join(fila[::-1] if derecha else fila) + "}" for fila in filas
            )
            imagen.put(datos, to=(ancho - radio if derecha else 0, alto - radio if abajo else 0))
    return imagen


def _mezclar_colores(color_a: str, color_b: str, t: float) -> str:
    """Color intermedio entre dos colores hexadecimales (``t`` de 0 a 1)."""

    def a_rgb(c: str) -> tuple[int, int, int]:
        c = c.lstrip("#")
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)

    ra, ga, ba = a_rgb(color_a)
    rb, gb, bb = a_rgb(color_b)
    r = round(ra + (rb - ra) * t)
    g = round(ga + (gb - ga) * t)
    b = round(ba + (bb - ba) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def _medir_texto(maestro: tk.Misc, texto: str, fuente: tema.Fuente) -> tuple[int, int]:
    """Ancho y alto que ocupa el texto con esa fuente."""
    temporal = tk.Label(maestro, text=texto, font=fuente)
    temporal.update_idletasks()
    ancho, alto = temporal.winfo_reqwidth(), temporal.winfo_reqheight()
    temporal.destroy()
    return ancho, alto


class MarcoRedondeado(tk.Canvas):
    """Canvas con un fondo redondeado y un Frame interior (``interior``) donde colocar widgets."""

    def __init__(
        self,
        maestro: tk.Misc,
        fondo: str,
        radio: int = tema.RADIO_BOTON,
        color_borde: str | None = None,
        ancho_borde: int = 1,
        fondo_exterior: str | None = None,
        **kwargs: Any,
    ) -> None:
        fondo_exterior = fondo_exterior or (
            maestro["bg"]
            if isinstance(maestro, (tk.Tk, tk.Frame, tk.Canvas))
            else tema.FONDO_EXTERIOR
        )
        super().__init__(maestro, bg=fondo_exterior, highlightthickness=0, bd=0, **kwargs)

        self._fondo = fondo
        self._radio = radio
        self._color_borde = color_borde
        self._ancho_borde = ancho_borde
        self._id_forma: int | None = None
        self._imagen: tk.PhotoImage | None = None  # hay que guardarla: Tk no la retiene
        self.interior = tk.Frame(self, bg=fondo)
        self._id_ventana = self.create_window(0, 0, window=self.interior, anchor="nw")

        self.bind("<Configure>", self._al_redimensionar)

    def _al_redimensionar(self, evento: tk.Event) -> None:
        """Vuelve a dibujar el fondo con el tamaño nuevo."""
        ancho, alto = evento.width, evento.height
        if ancho < 4 or alto < 4:
            return

        if self._id_forma is not None:
            self.delete(self._id_forma)

        borde = self._ancho_borde if self._color_borde else 0
        # Solo el relleno: los marcos no llevan contorno.
        self._imagen = _imagen_rectangulo_redondeado(
            self, ancho, alto, self._radio, self._fondo, None, self["bg"]
        )
        self._id_forma = self.create_image(0, 0, image=self._imagen, anchor="nw")

        self.tag_lower(self._id_forma)
        self.coords(self._id_ventana, borde, borde)
        self.itemconfig(self._id_ventana, width=ancho - 2 * borde, height=alto - 2 * borde)


class _ColoresVariante(TypedDict):
    """Colores de una variante de botón (``borde`` None = sin borde)."""

    fondo: str
    fondo_hover: str
    borde: str | None
    texto: str
    # Deshabilitado: tiene que notarse que no se puede pulsar.
    fondo_deshabilitado: str
    texto_deshabilitado: str


class BotonRedondeado(tk.Canvas):
    """Botón redondeado con variantes de color y estados normal, resaltado y deshabilitado."""

    _VARIANTES: ClassVar[dict[str, _ColoresVariante]] = {
        "secundario": {
            "fondo": tema.FONDO_SUPERFICIE,
            "fondo_hover": tema.FONDO_RESALTADO,
            "borde": tema.BORDE_BOTON,
            "texto": tema.TEXTO_SECUNDARIO,
            "fondo_deshabilitado": tema.FONDO_SUPERFICIE,
            "texto_deshabilitado": tema.TEXTO_DESHABILITADO,
        },
        # Verde lleno; deshabilitado se apaga hacia el fondo de la tarjeta, porque solo oscurecer
        # el texto (ya oscuro sobre verde) no se notaba.
        "acento": {
            "fondo": tema.VERDE,
            "fondo_hover": "#4fe39c",
            "borde": None,
            "texto": tema.VERDE_SOBRE_FONDO,
            "fondo_deshabilitado": _mezclar_colores(tema.VERDE, tema.FONDO_TARJETA, 0.72),
            "texto_deshabilitado": _mezclar_colores(tema.VERDE, tema.FONDO_TARJETA, 0.4),
        },
        "peligro": {
            "fondo": tema.FONDO_SUPERFICIE,
            "fondo_hover": "#241417",
            "borde": tema.BORDE_PELIGRO,
            "texto": tema.ROJO,
            "fondo_deshabilitado": tema.FONDO_SUPERFICIE,
            "texto_deshabilitado": tema.TEXTO_DESHABILITADO,
        },
    }

    def __init__(
        self,
        maestro: tk.Misc,
        texto: str,
        comando: Callable[[], object] | None = None,
        variante: str = "secundario",
        radio: int = tema.RADIO_BOTON,
        fuente: tema.Fuente | None = None,
        padx: int = 24,
        pady: int = 13,
        fondo_exterior: str | None = None,
        **kwargs: Any,
    ) -> None:
        fondo_exterior = fondo_exterior or tema.FONDO_TARJETA
        super().__init__(
            maestro, bg=fondo_exterior, highlightthickness=0, bd=0, cursor="hand2", **kwargs
        )
        self._colores = self._VARIANTES[variante]
        self._radio = radio
        self._comando = comando
        self._habilitado = True
        self._texto = texto
        self._fuente = fuente or (
            tema.obtener_fuente_mono(self),
            tema.TAMANO_FUENTE_NORMAL,
            "bold" if variante == "acento" else "normal",
        )

        ancho_texto, alto_texto = _medir_texto(self, texto, self._fuente)
        self.configure(width=ancho_texto + padx * 2, height=alto_texto + pady * 2)

        self._id_forma: int | None = None
        self._id_texto: int | None = None
        self._resaltado = False
        # Imágenes del fondo ya dibujadas (normal y resaltado), para no recalcularlas al pasar
        # el ratón. Hay que guardarlas: Tk no retiene las imágenes que muestra.
        self._imagenes: dict[tuple[int, int, str, str | None], tk.PhotoImage] = {}

        self.bind("<Configure>", self._redibujar)
        self.bind("<Enter>", lambda e: self._establecer_resaltado(True))
        self.bind("<Leave>", lambda e: self._establecer_resaltado(False))
        self.bind("<Button-1>", self._al_hacer_click)

    def _establecer_resaltado(self, resaltado: bool) -> None:
        """Cambia el color al pasar el ratón por encima."""
        if not self._habilitado:
            return
        self._resaltado = resaltado
        self._redibujar()

    def _al_hacer_click(self, evento: tk.Event) -> None:
        """Ejecuta el comando del botón si está habilitado."""
        if self._habilitado and self._comando:
            self._comando()

    def _redibujar(self, evento: tk.Event | None = None) -> None:
        """Dibuja el fondo (reutilizando las imágenes ya calculadas) y el texto."""
        ancho = evento.width if evento else self.winfo_width()
        alto = evento.height if evento else self.winfo_height()
        if ancho < 4 or alto < 4:
            return

        if self._id_forma is not None:
            self.delete(self._id_forma)
        if self._id_texto is not None:
            self.delete(self._id_texto)

        colores = self._colores
        if not self._habilitado:
            fondo, color_texto = colores["fondo_deshabilitado"], colores["texto_deshabilitado"]
        else:
            fondo = colores["fondo_hover"] if self._resaltado else colores["fondo"]
            color_texto = colores["texto"]
        borde = colores["borde"]
        clave = (ancho, alto, fondo, borde)
        if clave not in self._imagenes:
            if self._imagenes and next(iter(self._imagenes))[:2] != (ancho, alto):
                self._imagenes.clear()  # cambió el tamaño: las de antes ya no sirven
            self._imagenes[clave] = _imagen_rectangulo_redondeado(
                self, ancho, alto, self._radio, fondo, borde, self["bg"]
            )
        self._id_forma = self.create_image(0, 0, image=self._imagenes[clave], anchor="nw")
        self._id_texto = self.create_text(
            ancho / 2,
            alto / 2,
            text=self._texto,
            fill=color_texto,
            font=self._fuente,
        )

    def establecer_habilitado(self, habilitado: bool) -> None:
        """Habilita o deshabilita el botón."""
        self._habilitado = habilitado
        self.configure(cursor="hand2" if habilitado else "arrow")
        self._redibujar()

    def establecer_texto(self, texto: str) -> None:
        """Cambia el texto del botón."""
        self._texto = texto
        self._redibujar()


# Un trozo de texto del panel de registro y la etiqueta (el color) con que se pinta.
Trozo = tuple[str, str]


class PanelRegistro:
    """Panel de registro de una pestaña: un texto de solo lectura dentro de un marco redondeado.

    Se escriben líneas ``[NIVEL] mensaje`` con el color de su nivel y líneas atenuadas; cada
    escritura lo abre, añade y lo vuelve a cerrar. ``texto`` es el widget de Tk, para lo demás
    (otros colores, desplazarlo...).
    """

    def __init__(self, maestro: tk.Misc, fuente: tema.Fuente, alto_lineas: int) -> None:
        self.marco = MarcoRedondeado(
            maestro,
            fondo=tema.FONDO_REGISTRO,
            radio=tema.RADIO_BOTON,
            color_borde=tema.BORDE_TARJETA,
            fondo_exterior=tema.FONDO_TARJETA,
        )
        self.texto = tk.Text(
            self.marco.interior,
            bg=tema.FONDO_REGISTRO,
            fg=tema.TEXTO_SECUNDARIO,
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=fuente,
            spacing1=4,
            spacing3=4,
            wrap="word",
            height=alto_lineas,
            state="disabled",
            cursor="arrow",
        )
        self.texto.pack(fill="both", expand=True, padx=16, pady=10)
        tema.configurar_etiquetas_registro(self.texto)

    def pack(self, **opciones: Any) -> None:
        """Coloca el panel (su marco) con ``pack``."""
        self.marco.pack(**opciones)

    def escribir(self, *trozos: Trozo, mostrar_final: bool = False) -> None:
        """Añade los trozos ``(texto, etiqueta)`` al final del panel.

        Con ``mostrar_final`` lo desplaza para que la última línea se vea entera.
        """
        self.texto.configure(state="normal")
        for contenido, etiqueta in trozos:
            self.texto.insert("end", contenido, etiqueta)
        if mostrar_final:
            tema.mostrar_final_registro(self.texto)
        self.texto.configure(state="disabled")

    def registrar(self, nivel: str, etiqueta: str, mensaje: str) -> None:
        """Añade una línea ``<etiqueta> <mensaje>`` con los colores de ``nivel``."""
        estilo = nivel + "_mensaje" if nivel in tema.COLORES_MENSAJE_NIVEL else "atenuado"
        self.escribir((f"{etiqueta} ", nivel), (mensaje + "\n", estilo), mostrar_final=True)

    def registrar_atenuado(self, mensaje: str) -> None:
        """Añade una línea atenuada (informativa)."""
        self.escribir((mensaje + "\n", "atenuado"), mostrar_final=True)

    def limpiar(self) -> None:
        """Vacía el panel."""
        self.texto.configure(state="normal")
        self.texto.delete("1.0", "end")
        self.texto.configure(state="disabled")


class BarraProgreso(ttk.Progressbar):
    """Barra de progreso fina para una operación que corre en un hilo de trabajo.

    El hilo de trabajo solo llama a ``apuntar`` tras cada paso: no toca Tk, que no es seguro
    entre hilos, ni avisa a la ventana por cada fichero, que con miles sería muy lento. La barra
    lee lo apuntado desde el hilo de la interfaz cada INTERVALO_MS mientras ``en_curso()`` sea
    cierto. Al terminar se queda llena un momento y se vacía.
    """

    INTERVALO_MS = 100
    MS_LLENA = 1500

    def __init__(self, maestro: tk.Misc, en_curso: Callable[[], bool]) -> None:
        super().__init__(maestro, mode="determinate", style=tema.ESTILO_PROGRESO)
        self._en_curso = en_curso
        # (hechos, total) que apunta el hilo de trabajo. Cada operación tiene su número, para que
        # sus refrescos no pisen los de la siguiente.
        self._apuntado: tuple[int, int] | None = None
        self._operacion = 0

    def apuntar(self, actual: int, total: int) -> None:
        """Por dónde va la operación; se puede llamar desde cualquier hilo."""
        self._apuntado = (actual, total)

    def empezar(self) -> None:
        """Vacía la barra y la va actualizando mientras dure la operación."""
        self._operacion += 1
        self._apuntado = None
        self.configure(maximum=1, value=0)
        self._refrescar(self._operacion)

    def terminar(self, completada: bool) -> None:
        """Llena la barra (o la vacía si no se hizo nada) y poco después la vacía."""
        self.mostrar_apuntado()
        self.configure(value=float(self.cget("maximum")) if completada else 0)
        self.after(self.MS_LLENA, partial(self._vaciar_si_sigue, self._operacion))

    def mostrar_apuntado(self) -> None:
        """Pone en la barra lo último que apuntó el hilo de trabajo."""
        if self._apuntado is not None:
            actual, total = self._apuntado
            self.configure(maximum=max(total, 1), value=actual)

    def _refrescar(self, operacion: int) -> None:
        """Muestra lo apuntado y se vuelve a programar mientras dure esa operación."""
        if operacion != self._operacion or not self._en_curso():
            return
        self.mostrar_apuntado()
        self.after(self.INTERVALO_MS, partial(self._refrescar, operacion))

    def _vaciar_si_sigue(self, operacion: int) -> None:
        """Vacía la barra si no ha empezado otra operación desde entonces."""
        if operacion == self._operacion and not self._en_curso():
            self.configure(value=0)


class CampoRedondeado(tk.Frame):
    """Campo de texto con esquinas redondeadas."""

    def __init__(
        self,
        maestro: tk.Misc,
        fuente: tema.Fuente | None = None,
        radio: int = tema.RADIO_BOTON,
        mostrar: str | None = None,
        fondo_exterior: str | None = None,
        **kwargs: Any,
    ) -> None:
        fondo_exterior = fondo_exterior or tema.FONDO_TARJETA
        super().__init__(maestro, bg=fondo_exterior, **kwargs)

        self._marco = MarcoRedondeado(
            self,
            fondo=tema.FONDO_CAMPO,
            radio=radio,
            color_borde=tema.BORDE_CAMPO,
            ancho_borde=1,
            fondo_exterior=fondo_exterior,
            height=44,
        )
        self._marco.pack(fill="x", expand=True)

        mono = fuente or (tema.obtener_fuente_mono(self), tema.TAMANO_FUENTE_NORMAL)
        self.campo = tk.Entry(
            self._marco.interior,
            font=mono,
            show=mostrar or "",
            bg=tema.FONDO_CAMPO,
            fg=tema.TEXTO_PRINCIPAL,
            insertbackground=tema.CIAN,
            relief="flat",
            bd=0,
            highlightthickness=0,
        )
        self.campo.pack(fill="both", expand=True, padx=16, pady=12)

    def obtener(self) -> str:
        """Devuelve el texto actual del campo."""
        return self.campo.get()

    def establecer(self, valor: str) -> None:
        """Reemplaza el texto del campo."""
        self.campo.delete(0, "end")
        self.campo.insert(0, valor)

    def limpiar(self) -> None:
        """Vacía el campo."""
        self.campo.delete(0, "end")

    def elegir_carpeta(self, titulo: str) -> None:
        """Abre el diálogo de elegir carpeta y, si se elige una, la escribe en el campo."""
        self._establecer_si_hay(filedialog.askdirectory(title=titulo))

    def elegir_fichero(self, titulo: str, **opciones: Any) -> None:
        """Abre el diálogo de abrir fichero y, si se elige uno, lo escribe en el campo."""
        self._establecer_si_hay(filedialog.askopenfilename(title=titulo, **opciones))

    def _establecer_si_hay(self, ruta: str) -> None:
        if ruta:  # vacío si se cancela el diálogo
            self.establecer(ruta)


# Cómo escribe una pestaña en su registro: nivel, etiqueta y texto («error», «[ERROR]», ...).
Registrar = Callable[[str, str, str], None]


def leer_ruta(
    campo: CampoRedondeado,
    comprobar: Callable[[Path], Path],
    si_falta: str,
    registrar: Registrar,
) -> Path | None:
    """La ruta escrita en el campo, preparada y comprobada igual que en la consola (rutas.py).

    ``comprobar`` es la comprobación del núcleo que toca (que sea una carpeta, que exista...).
    Si el campo está vacío avisa con ``si_falta``; si la ruta no sirve, registra el motivo. En
    los dos casos devuelve None.
    """
    ruta = preparar_ruta(campo.obtener())
    if ruta is None:
        registrar("aviso", "[AVISO]", si_falta)
        return None
    try:
        return comprobar(ruta)
    except ErrorRuta as e:
        registrar("error", "[ERROR]", str(e))
        return None


class CabeceraSeccion(tk.Canvas):
    """Título de sección con una línea degradada debajo."""

    def __init__(
        self,
        maestro: tk.Misc,
        texto: str,
        fuente: tema.Fuente | None = None,
        fondo_exterior: str | None = None,
        alto: int = 28,
        **kwargs: Any,
    ) -> None:
        fondo_exterior = fondo_exterior or tema.FONDO_TARJETA
        super().__init__(
            maestro, bg=fondo_exterior, highlightthickness=0, bd=0, height=alto, **kwargs
        )
        self._texto = texto.upper()
        self._fuente = fuente or (
            tema.obtener_fuente_mono(self),
            tema.TAMANO_FUENTE_CABECERA,
            "bold",
        )
        self.bind("<Configure>", self._redibujar)

    def _redibujar(self, evento: tk.Event) -> None:
        """Dibuja el texto del título y la línea degradada."""
        self.delete("all")

        self.create_text(
            0,
            6,
            text=self._texto,
            fill=tema.CIAN,
            font=self._fuente,
            anchor="nw",
        )

        y = 24
        pasos = 40
        ancho_linea = min(evento.width, 320)
        color_inicial = _mezclar_colores(tema.CIAN, tema.FONDO_TARJETA, 0.87)
        color_final = tema.FONDO_TARJETA
        for i in range(pasos):
            x0 = ancho_linea * i / pasos
            x1 = ancho_linea * (i + 1) / pasos
            t = i / (pasos - 1)
            color = _mezclar_colores(color_inicial, color_final, t)
            self.create_line(x0, y, x1, y, fill=color)


class PuntoEstado(tk.Canvas):
    """Punto de color que indica un estado (p.ej. vigilancia activa/inactiva)."""

    def __init__(
        self,
        maestro: tk.Misc,
        color: str = tema.CIAN,
        fondo_exterior: str | None = None,
        tamano: int = 18,
        **kwargs: Any,
    ) -> None:
        fondo_exterior = fondo_exterior or tema.FONDO_TARJETA
        super().__init__(
            maestro,
            bg=fondo_exterior,
            width=tamano,
            height=tamano,
            highlightthickness=0,
            bd=0,
            **kwargs,
        )
        self._color = color
        self._tamano = tamano
        self._dibujar()

    def _dibujar(self) -> None:
        """Dibuja el punto con su halo de color."""
        centro = self._tamano / 2
        halo = _mezclar_colores(self._color, self.cget("bg"), 0.6)
        self.create_oval(centro - 8, centro - 8, centro + 8, centro + 8, fill=halo, outline="")
        self.create_oval(
            centro - 3, centro - 3, centro + 3, centro + 3, fill=self._color, outline=""
        )

    def establecer_color(self, color: str) -> None:
        """Cambia el color del punto."""
        self._color = color
        self.delete("all")
        self._dibujar()


class BarraPestanas(tk.Frame):
    """Barra de pestañas con subrayado, que muestra una página distinta por pestaña."""

    def __init__(
        self,
        maestro: tk.Misc,
        etiquetas: list[str],
        fondo_exterior: str | None = None,
        **kwargs: Any,
    ) -> None:
        fondo_exterior = fondo_exterior or tema.FONDO_TARJETA
        super().__init__(maestro, bg=fondo_exterior, **kwargs)

        self._activa = etiquetas[0] if etiquetas else None
        self._resaltada: str | None = None
        self._lienzos: dict[str, tk.Canvas] = {}
        self._paginas: dict[str, tk.Widget] = {}
        self._barra = tk.Frame(self, bg=fondo_exterior)
        self._barra.pack(fill="x")

        fuente = (tema.obtener_fuente_mono(self), tema.TAMANO_FUENTE_NORMAL)
        for etiqueta in etiquetas:
            lienzo = tk.Canvas(
                self._barra,
                bg=fondo_exterior,
                highlightthickness=0,
                bd=0,
                height=38,
                cursor="hand2",
            )
            lienzo.pack(side="left")
            self._vincular_eventos(lienzo, etiqueta)
            self._lienzos[etiqueta] = lienzo

            ancho_texto, _ = _medir_texto(self, etiqueta, fuente)
            lienzo.configure(width=ancho_texto + 36)

        separador = tk.Frame(self, bg=tema.BORDE_TARJETA, height=1)
        separador.pack(fill="x", pady=(10, 0))

        self.contenido = tk.Frame(self, bg=fondo_exterior)
        self.contenido.pack(fill="both", expand=True)

    def _vincular_eventos(self, lienzo: tk.Canvas, etiqueta: str) -> None:
        """Conecta los eventos de ratón de una pestaña con sus acciones."""
        lienzo.bind("<Configure>", lambda _evento: self._dibujar_pestana(etiqueta))
        lienzo.bind("<Button-1>", lambda _evento: self.seleccionar(etiqueta))
        lienzo.bind("<Enter>", lambda _evento: self._al_resaltar(etiqueta, True))
        lienzo.bind("<Leave>", lambda _evento: self._al_resaltar(etiqueta, False))

    def _al_resaltar(self, etiqueta: str, resaltada: bool) -> None:
        """Resalta la pestaña al pasar el ratón por encima."""
        self._resaltada = etiqueta if resaltada else None
        self._dibujar_pestana(etiqueta)

    def _dibujar_pestana(self, etiqueta: str) -> None:
        """Dibuja una pestaña según esté activa, resaltada o normal."""
        lienzo = self._lienzos[etiqueta]
        lienzo.delete("all")
        ancho = lienzo.winfo_width() or int(lienzo["width"])
        alto = lienzo.winfo_height() or int(lienzo["height"])

        es_activa = etiqueta == self._activa
        es_resaltada = etiqueta == self._resaltada
        mono = tema.obtener_fuente_mono(lienzo)
        fuente: tema.Fuente

        if es_activa:
            color = tema.CIAN
            fuente = (mono, tema.TAMANO_FUENTE_NORMAL, "bold")
        elif es_resaltada:
            color = tema.TEXTO_SECUNDARIO
            fuente = (mono, tema.TAMANO_FUENTE_NORMAL)
        else:
            color = tema.TEXTO_ETIQUETA
            fuente = (mono, tema.TAMANO_FUENTE_NORMAL)

        lienzo.create_text(ancho / 2, alto / 2 - 3, text=etiqueta, fill=color, font=fuente)

        color_subrayado = tema.CIAN if es_activa else lienzo["bg"]
        lienzo.create_line(0, alto - 1, ancho, alto - 1, fill=color_subrayado, width=2)

    def agregar_pagina(self, etiqueta: str, widget: tk.Widget) -> None:
        """Asocia una página (widget) a una pestaña."""
        self._paginas[etiqueta] = widget
        if etiqueta == self._activa:
            widget.pack(fill="both", expand=True)
        else:
            widget.pack_forget()

    def seleccionar(self, etiqueta: str) -> None:
        """Cambia la pestaña activa y muestra su página."""
        if etiqueta not in self._lienzos:
            return
        anterior = self._activa
        self._activa = etiqueta

        if anterior and anterior in self._lienzos:
            self._dibujar_pestana(anterior)
        self._dibujar_pestana(etiqueta)

        for et, pagina in self._paginas.items():
            if et == etiqueta:
                pagina.pack(fill="both", expand=True)
            else:
                pagina.pack_forget()


class InfoEmergente:
    """Texto de ayuda que aparece junto a un widget al dejar el ratón encima (tooltip)."""

    RETARDO_MS = 400
    ANCHO_TEXTO = 380
    MARGEN = 6

    def __init__(self, widget: tk.Widget, texto: str, fuente: tema.Fuente) -> None:
        self.widget = widget
        self.texto = texto
        self.fuente = fuente
        self._ventana: tk.Toplevel | None = None
        self._pendiente: str | None = None
        widget.bind("<Enter>", self._programar, add="+")
        widget.bind("<Leave>", self._ocultar, add="+")
        widget.bind("<ButtonPress>", self._ocultar, add="+")

    @property
    def visible(self) -> bool:
        """Si la ayuda se está mostrando ahora mismo."""
        return self._ventana is not None

    def _programar(self, evento: tk.Event | None = None) -> None:
        """Espera un momento antes de mostrar la ayuda, para no molestar al pasar de largo."""
        self._cancelar()
        self._pendiente = self.widget.after(self.RETARDO_MS, self.mostrar)

    def _cancelar(self) -> None:
        if self._pendiente is not None:
            self.widget.after_cancel(self._pendiente)
            self._pendiente = None

    def mostrar(self) -> None:
        """Muestra la ayuda justo debajo del widget."""
        self._pendiente = None
        if self._ventana is not None or not self.widget.winfo_exists():
            return
        ventana = tk.Toplevel(self.widget)
        ventana.wm_overrideredirect(True)
        ventana.configure(bg=tema.BORDE_BOTON)
        tk.Label(
            ventana,
            text=self.texto,
            justify="left",
            wraplength=self.ANCHO_TEXTO,
            bg=tema.FONDO_SUPERFICIE,
            fg=tema.TEXTO_SECUNDARIO,
            font=self.fuente,
            padx=10,
            pady=8,
        ).pack(padx=1, pady=1)  # el margen de 1 px deja ver el color del borde
        ventana.update_idletasks()
        ventana.wm_geometry("+{}+{}".format(*self._posicion(ventana)))
        self._ventana = ventana

    def _posicion(self, ventana: tk.Toplevel) -> tuple[int, int]:
        """Debajo del widget, sin salirse de la ventana principal (así no la corta el borde
        de la pantalla, aunque haya varios monitores); si abajo no cabe, encima."""
        principal = self.widget.winfo_toplevel()
        izquierda, arriba = principal.winfo_rootx(), principal.winfo_rooty()
        derecha = izquierda + principal.winfo_width()
        abajo = arriba + principal.winfo_height()
        ancho, alto = ventana.winfo_reqwidth(), ventana.winfo_reqheight()

        x = max(izquierda, min(self.widget.winfo_rootx(), derecha - ancho - self.MARGEN))
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + self.MARGEN
        if y + alto > abajo:
            y = max(arriba, self.widget.winfo_rooty() - alto - self.MARGEN)
        return x, y

    def _ocultar(self, evento: tk.Event | None = None) -> None:
        self._cancelar()
        if self._ventana is not None:
            self._ventana.destroy()
            self._ventana = None
