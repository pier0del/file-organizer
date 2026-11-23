"""Tests de la interfaz gráfica: el tamaño de la ventana y que todo quepa, la barra de progreso,
cada pestaña, el diálogo de reglas, los botones y que no se toque Tk desde otro hilo.

Necesitan tkinter y una pantalla (en Linux sin escritorio, por ejemplo, con Xvfb); si no
están disponibles, se saltan.
"""

import gc
import os
import re
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

tk = pytest.importorskip("tkinter")
tkfont = pytest.importorskip("tkinter.font")
ttk = pytest.importorskip("tkinter.ttk")

from file_organizer.bloqueo import BloqueoCarpeta  # noqa: E402
from file_organizer.cifrador import Cifrador  # noqa: E402
from file_organizer.clasificador import (  # noqa: E402
    DESCRIPCIONES_AGRUPACION,
    EXPLICACION_FECHA,
    CriterioAgrupacion,
)
from file_organizer.config_usuario import ConfiguracionUsuario  # noqa: E402
from file_organizer.duplicados import AccionDuplicados  # noqa: E402
from file_organizer.gui import (  # noqa: E402
    aplicacion,
    componentes,
    dialogo_reglas,
    monitores,
    pestana_cifrado,
    pestana_historial,
    pestana_organizar,
    tema,
)
from file_organizer.gui.componentes import (  # noqa: E402
    BarraPestanas,
    BarraProgreso,
    BotonRedondeado,
    CampoRedondeado,
    InfoEmergente,
)
from file_organizer.gui.dialogo_reglas import DialogoReglas  # noqa: E402
from file_organizer.gui.monitores import Monitor, monitor_de_trabajo  # noqa: E402
from file_organizer.gui.pestana_cifrado import PestanaCifrado  # noqa: E402
from file_organizer.gui.pestana_historial import PestanaHistorial  # noqa: E402
from file_organizer.gui.pestana_organizar import (  # noqa: E402
    TITULO_INTEGRIDAD,
    TITULO_INTEGRIDAD_CORTO,
    PestanaOrganizar,
    _resumir_nombres,
)
from file_organizer.integridad import DESCRIPCIONES_INTEGRIDAD, ModoIntegridad  # noqa: E402
from file_organizer.mover_archivos import NOMBRE_ARCHIVO_HISTORIAL  # noqa: E402
from file_organizer.organizador import NOMBRE_CARPETA_BACKUPS, OrganizadorArchivos  # noqa: E402
from file_organizer.reglas import NOMBRE_ARCHIVO_REGLAS, Regla, ReglasCarpeta  # noqa: E402


@pytest.fixture(autouse=True)
def _recoger_objetos_de_tk() -> Iterator[None]:
    """Tras cada test, libera aquí (en el hilo principal) lo que quede de sus ventanas.

    Al cerrar una ventana, sus variables e imágenes de Tk siguen en referencias circulares. Si
    no se recogen aquí, el recolector las libera más tarde desde cualquier hilo, por ejemplo el
    de un vigilante en otro test, y Tk avisa de «main thread is not in main loop».
    """
    yield
    gc.collect()


@pytest.fixture
def raiz(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tk.Tk]:
    """La ventana entera del programa, con preferencias temporales; se salta el test si no hay
    pantalla.
    """
    monkeypatch.setenv("FILE_ORGANIZER_CONFIG", str(tmp_path / "uc.json"))
    monkeypatch.setattr(tema, "_fuente_mono", None)
    try:
        ventana = aplicacion.construir_aplicacion()
    except tk.TclError as error:
        pytest.skip(f"No hay pantalla disponible: {error}")
    yield ventana
    ventana.destroy()


def _pestana_organizar(ventana: tk.Tk) -> PestanaOrganizar:
    """La pestaña Organizar de esta ventana (gc puede guardar las de ventanas ya cerradas)."""
    return next(
        o for o in gc.get_objects() if isinstance(o, PestanaOrganizar) and o.marco.tk is ventana.tk
    )


def test_la_lista_de_fuentes_se_consulta_una_sola_vez(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Pedir la lista de fuentes del sistema es lento: se hace una vez aunque se resalten las
    pestañas muchas veces.
    """
    llamadas = []
    original = tkfont.families
    monkeypatch.setattr(tkfont, "families", lambda *a, **k: llamadas.append(1) or original(*a, **k))
    monkeypatch.setenv("FILE_ORGANIZER_CONFIG", str(tmp_path / "uc.json"))
    monkeypatch.setattr(tema, "_fuente_mono", None)
    try:
        ventana = aplicacion.construir_aplicacion()
    except tk.TclError as error:
        pytest.skip(f"No hay pantalla disponible: {error}")
    try:
        barra = next(
            o for o in gc.get_objects() if isinstance(o, BarraPestanas) and o.tk is ventana.tk
        )
        for _ in range(10):
            barra._al_resaltar("Historial", True)
            barra._al_resaltar("Historial", False)
        assert len(llamadas) == 1
    finally:
        ventana.destroy()


def test_el_hilo_de_trabajo_no_toca_tk_al_avanzar(raiz: tk.Tk, monkeypatch: pytest.MonkeyPatch):
    """El progreso de cada paso solo se apunta: ni after() ni la barra desde otro hilo."""
    barra = _pestana_organizar(raiz).progreso
    llamadas = []
    monkeypatch.setattr(barra, "after", lambda ms, f=None, *a: llamadas.append(f))

    for i in range(1, 5001):
        barra.apuntar(i, 5000)

    assert llamadas == []
    assert float(barra.cget("value")) == 0
    barra.mostrar_apuntado()
    assert float(barra.cget("value")) == 5000


def test_la_interfaz_refresca_la_barra_mientras_dura(raiz: tk.Tk, monkeypatch: pytest.MonkeyPatch):
    """Mientras la operación sigue, la interfaz copia a la barra lo apuntado; al acabar, deja de
    refrescarla.
    """
    en_curso = [True]
    barra = BarraProgreso(raiz, en_curso=lambda: en_curso[0])
    programadas: list = []
    monkeypatch.setattr(barra, "after", lambda ms, f=None, *a: programadas.append(f))
    barra.empezar()

    barra.apuntar(30, 120)  # desde el hilo de trabajo
    programadas.pop(0)()  # el siguiente refresco, en el hilo de la interfaz
    assert float(barra.cget("value")) == 30
    assert float(barra.cget("maximum")) == 120

    en_curso[0] = False
    programadas.pop(0)()
    assert programadas == []  # al acabar la operación deja de refrescarse


def test_al_terminar_se_llena_y_poco_despues_se_vacia(
    raiz: tk.Tk, tmp_path: Path, sincrono: None, monkeypatch: pytest.MonkeyPatch
):
    """Al acabar de organizar la barra se ve llena un momento y luego vuelve a cero.

    La barra se mira al terminar y luego se espera a que se vacíe: mirarla tras un ``update()``
    fallaba en Windows, donde redibujar la ventana tardaba más que ese momento y, al mirarla, ya
    se había vaciado.
    """
    monkeypatch.setattr(BarraProgreso, "MS_LLENA", 200)
    carpeta = _ficheros(tmp_path / "origen", "a.jpg", "b.pdf", "c.txt")
    pestana = _pestana_organizar(raiz)
    pestana.ruta.establecer(str(carpeta))
    barra = pestana.progreso
    al_terminar: list[tuple[float, float, float]] = []  # (valor, máximo, cuándo)
    terminar = barra.terminar

    def terminar_y_mirar(completada: bool) -> None:
        terminar(completada)
        al_terminar.append(
            (float(barra.cget("value")), float(barra.cget("maximum")), time.monotonic())
        )

    monkeypatch.setattr(barra, "terminar", terminar_y_mirar)

    pestana._organizar()
    raiz.update()
    assert [(valor, maximo) for valor, maximo, _ in al_terminar] == [(3, 3)]

    limite = time.monotonic() + 10
    while float(barra.cget("value")) != 0 and time.monotonic() < limite:
        time.sleep(0.01)
        raiz.update()
    assert float(barra.cget("value")) == 0
    # Siguió llena hasta el momento previsto (con margen por la precisión del reloj).
    assert time.monotonic() - al_terminar[0][2] >= 0.8 * BarraProgreso.MS_LLENA / 1000


def test_sin_ficheros_la_barra_no_se_llena(raiz: tk.Tk, tmp_path: Path, sincrono: None):
    """Si no había nada que organizar, la barra no se llena como si hubiera terminado algo."""
    carpeta = tmp_path / "vacia"
    carpeta.mkdir()
    pestana = _pestana_organizar(raiz)
    pestana.ruta.establecer(str(carpeta))

    pestana._organizar()
    raiz.update()

    assert float(pestana.progreso.cget("value")) == 0


def test_vaciar_la_barra_no_pisa_la_operacion_siguiente(
    raiz: tk.Tk, monkeypatch: pytest.MonkeyPatch
):
    """Si empieza otra operación antes de vaciarse la barra, el vaciado pendiente no borra su
    progreso.
    """
    en_curso = [False]
    barra = BarraProgreso(raiz, en_curso=lambda: en_curso[0])
    programadas: list = []
    monkeypatch.setattr(barra, "after", lambda ms, f=None, *a: programadas.append(f))
    barra.apuntar(10, 10)
    barra.terminar(completada=True)
    vaciar_la_anterior = programadas.pop()

    en_curso[0] = True
    barra.empezar()
    barra.apuntar(4, 10)
    barra.mostrar_apuntado()
    vaciar_la_anterior()

    assert float(barra.cget("value")) == 4


def test_deshacer_tambien_mueve_la_barra(
    raiz: tk.Tk, tmp_path: Path, sincrono: None, monkeypatch: pytest.MonkeyPatch
):
    """Deshacer también avanza la barra, fichero a fichero."""
    carpeta = _ficheros(tmp_path / "origen", "a.jpg", "b.pdf")
    OrganizadorArchivos(carpeta_origen=carpeta).ejecutar()
    pestana = _pestana_organizar(raiz)
    pestana.ruta.establecer(str(carpeta))
    apuntados: list[tuple[int, int]] = []
    original = pestana.progreso.apuntar

    def apuntar(actual: int, total: int) -> None:
        apuntados.append((actual, total))
        original(actual, total)

    monkeypatch.setattr(pestana.progreso, "apuntar", apuntar)

    pestana._deshacer()
    raiz.update()

    assert apuntados == [(1, 2), (2, 2)]
    assert float(pestana.progreso.cget("value")) == 2


def test_la_barra_es_fina_y_con_el_tema_oscuro(raiz: tk.Tk):
    """La barra usa el estilo del tema (fondo oscuro, verde) y su alto fino."""
    pestana = _pestana_organizar(raiz)
    estilo = ttk.Style(raiz)
    assert pestana.progreso.cget("style") == tema.ESTILO_PROGRESO
    assert estilo.lookup(tema.ESTILO_PROGRESO, "troughcolor") == tema.FONDO_CAMPO
    assert estilo.lookup(tema.ESTILO_PROGRESO, "background") == tema.VERDE
    raiz.update()
    assert pestana.progreso.winfo_reqheight() <= tema.ALTO_BARRA_PROGRESO + 4


POR_DEFECTO = "{}x{}".format(*aplicacion.TAMANO_VENTANA)
MINIMO = "{}x{}".format(*aplicacion.TAMANO_MINIMO_VENTANA)


def _redimensionar(ventana: tk.Tk, geometria: str) -> None:
    """Cambia el tamaño de la ventana y deja que se recoloque todo."""
    ventana.geometry(geometria)
    ventana.update()
    ventana.update()  # la segunda pasada aplica lo que cambió el <Configure>


def _saltar_si_la_pantalla_no_deja(ventana: tk.Misc, geometria: str) -> None:
    """Salta el test si la ventana se quedó más pequeña que ``geometria``.

    macOS no deja que una ventana sea más alta que la pantalla menos la barra de menús y el
    Dock. En una pantalla pequeña, como la de las máquinas de macOS de GitHub Actions, la
    ventana no llega al tamaño pedido, y lo que el test comprueba a ese tamaño deja de tener
    sentido: no es un fallo del programa.
    """
    ancho, alto = (int(n) for n in geometria.split("x"))
    if ventana.winfo_width() < ancho or ventana.winfo_height() < alto:
        pytest.skip(
            f"La pantalla no deja la ventana a {geometria}: se queda en "
            f"{ventana.winfo_width()}x{ventana.winfo_height()}"
        )


class TestSeccionIntegridad:
    """La sección «Integridad» de la pestaña Organizar y cómo se reparte según el ancho."""

    def test_muestra_solo_el_resumen_del_modo_elegido(self, raiz: tk.Tk):
        """Debajo de las opciones solo se ve el resumen del modo marcado."""
        pestana = _pestana_organizar(raiz)
        for modo in ModoIntegridad:
            pestana._var_integridad.set(modo.value)
            pestana._actualizar_descripcion_integridad()
            assert pestana.descripcion_integridad.cget("text") == (
                DESCRIPCIONES_INTEGRIDAD[modo].resumen
            )

    def test_cada_opcion_tiene_su_descripcion_completa_al_pasar_el_raton(self, raiz: tk.Tk):
        """Al dejar el ratón sobre una opción se ve su explicación completa."""
        pestana = _pestana_organizar(raiz)
        assert set(pestana.ayudas_integridad) == set(ModoIntegridad)
        for modo, ayuda in pestana.ayudas_integridad.items():
            assert DESCRIPCIONES_INTEGRIDAD[modo].resumen in ayuda.texto
            assert DESCRIPCIONES_INTEGRIDAD[modo].detalle in ayuda.texto

    def test_ocupa_una_sola_fila_con_la_ventana_por_defecto(self, raiz: tk.Tk):
        """Con el tamaño por defecto, el título completo y las opciones van en una fila."""
        pestana = _pestana_organizar(raiz)
        _redimensionar(raiz, POR_DEFECTO)
        assert pestana.titulo_integridad.cget("text") == TITULO_INTEGRIDAD
        assert pestana.opciones_integridad.grid_info()["row"] == 0
        assert pestana.fila_integridad.winfo_height() == pestana.opciones_integridad.winfo_height()

    @staticmethod
    def _umbrales(pestana: PestanaOrganizar) -> tuple[int, int]:
        """Anchos de la fila a partir de los que caben, en una fila, el título completo y el
        corto. Se calculan con lo que miden los controles, que cambia con la fuente y con la
        versión de Tk (con Tk 9.0 miden menos que con Tk 8.6)."""
        opciones = pestana_organizar.SEPARACION_INTEGRIDAD + (
            pestana.opciones_integridad.winfo_reqwidth()
        )
        return (
            pestana._anchos_titulo[TITULO_INTEGRIDAD] + opciones,
            pestana._anchos_titulo[TITULO_INTEGRIDAD_CORTO] + opciones,
        )

    @staticmethod
    def _con_fila_de(raiz: tk.Tk, pestana: PestanaOrganizar, ancho_fila: int) -> None:
        """Redimensiona la ventana para que la fila de integridad mida ``ancho_fila``."""
        _redimensionar(raiz, POR_DEFECTO)
        margen = raiz.winfo_width() - pestana.fila_integridad.winfo_width()
        _redimensionar(raiz, f"{margen + ancho_fila}x{raiz.winfo_height()}")
        assert pestana.fila_integridad.winfo_width() == ancho_fila

    @staticmethod
    def _opciones_enteras(pestana: PestanaOrganizar) -> bool:
        """Si las opciones tienen todo el ancho que piden."""
        return pestana.opciones_integridad.winfo_width() >= (
            pestana.opciones_integridad.winfo_reqwidth()
        )

    def test_con_el_ancho_minimo_las_opciones_caben_en_una_fila(self, raiz: tk.Tk):
        """Con la ventana al mínimo, las opciones siguen en una fila y sin cortarse."""
        pestana = _pestana_organizar(raiz)
        _redimensionar(raiz, MINIMO)
        # Con Tk 8.6 lo consigue acortando el título; con Tk 9.0 cabe el completo.
        assert pestana.opciones_integridad.grid_info()["row"] == 0
        assert self._opciones_enteras(pestana)

    def test_si_no_cabe_el_titulo_completo_se_acorta_y_sigue_en_una_fila(self, raiz: tk.Tk):
        """Justo por debajo del ancho del título completo se usa el corto, y las opciones siguen al
        lado.
        """
        pestana = _pestana_organizar(raiz)
        raiz.minsize(1, 1)  # para poder estrecharla más que el mínimo de la aplicación
        completo, corto = self._umbrales(pestana)

        self._con_fila_de(raiz, pestana, completo)  # justo cabe el completo
        assert pestana.titulo_integridad.cget("text") == TITULO_INTEGRIDAD
        assert pestana.opciones_integridad.grid_info()["row"] == 0

        self._con_fila_de(raiz, pestana, completo - 1)  # ya no: se acorta
        assert pestana.titulo_integridad.cget("text") == TITULO_INTEGRIDAD_CORTO
        assert pestana.opciones_integridad.grid_info()["row"] == 0
        assert self._opciones_enteras(pestana)

        self._con_fila_de(raiz, pestana, corto)  # justo cabe el corto
        assert pestana.titulo_integridad.cget("text") == TITULO_INTEGRIDAD_CORTO
        assert pestana.opciones_integridad.grid_info()["row"] == 0

    def test_si_aun_asi_no_cabe_las_opciones_pasan_debajo(self, raiz: tk.Tk):
        """Si ni con el título corto caben, las opciones pasan a la fila de abajo; al ensanchar,
        vuelven.
        """
        pestana = _pestana_organizar(raiz)
        raiz.minsize(1, 1)  # para poder estrecharla más que el mínimo de la aplicación
        _completo, corto = self._umbrales(pestana)

        self._con_fila_de(raiz, pestana, corto - 1)
        assert pestana.titulo_integridad.cget("text") == TITULO_INTEGRIDAD
        assert pestana.opciones_integridad.grid_info()["row"] == 1
        assert self._opciones_enteras(pestana)

        _redimensionar(raiz, POR_DEFECTO)
        assert pestana.titulo_integridad.cget("text") == TITULO_INTEGRIDAD
        assert pestana.opciones_integridad.grid_info()["row"] == 0


class TestInfoEmergente:
    """Las ayudas que aparecen al dejar el ratón encima."""

    def test_aparece_al_dejar_el_raton_y_desaparece_al_salir(
        self, raiz: tk.Tk, monkeypatch: pytest.MonkeyPatch
    ):
        """Aparece tras un momento con el ratón encima y se va al salir."""
        monkeypatch.setattr(InfoEmergente, "RETARDO_MS", 1)
        ayuda = _pestana_organizar(raiz).ayudas_integridad[ModoIntegridad.COMPLETA]
        raiz.update()

        ayuda.widget.event_generate("<Enter>")
        fin = time.monotonic() + 2
        while not ayuda.visible and time.monotonic() < fin:
            raiz.update()
        assert ayuda.visible

        ayuda.widget.event_generate("<Leave>")
        assert not ayuda.visible

    def test_salir_antes_del_retardo_no_la_muestra(self, raiz: tk.Tk):
        """Pasar el ratón por encima sin pararse no la muestra."""
        ayuda = _pestana_organizar(raiz).ayudas_integridad[ModoIntegridad.COMPLETA]
        raiz.update()
        ayuda.widget.event_generate("<Enter>")
        ayuda.widget.event_generate("<Leave>")
        time.sleep(InfoEmergente.RETARDO_MS / 1000 + 0.1)
        raiz.update()
        assert not ayuda.visible

    @pytest.mark.parametrize("geometria", [POR_DEFECTO, MINIMO])
    def test_no_se_sale_de_la_ventana(self, raiz: tk.Tk, geometria: str):
        """La ayuda de la opción de más a la derecha queda dentro de la ventana, con el tamaño por
        defecto y con el mínimo.
        """
        pestana = _pestana_organizar(raiz)
        _redimensionar(raiz, geometria)
        # La última opción es la que queda más a la derecha: la que antes se cortaba.
        ayuda = pestana.ayudas_integridad[list(ModoIntegridad)[-1]]
        ayuda.mostrar()
        raiz.update()
        try:
            assert ayuda._ventana is not None
            ventana_ayuda = ayuda._ventana
            assert ventana_ayuda.winfo_rootx() >= raiz.winfo_rootx()
            assert ventana_ayuda.winfo_rootx() + ventana_ayuda.winfo_width() <= (
                raiz.winfo_rootx() + raiz.winfo_width()
            )
            assert ventana_ayuda.winfo_rooty() + ventana_ayuda.winfo_height() <= (
                raiz.winfo_rooty() + raiz.winfo_height()
            )
        finally:
            ayuda._ocultar()


# Monitores habituales en píxeles lógicos, tal como los ve Tk con el escalado aplicado.
MONITORES_HABITUALES = {
    "monitor 1920x1080": Monitor(0, 0, 1920, 1080),
    "portátil 1920x1080 al 125 %": Monitor(0, 0, 1536, 864),
    "portátil 1366x768": Monitor(0, 0, 1366, 768),
    "portátil 1920x1080 al 150 %": Monitor(0, 0, 1280, 720),
    # Linux con dos monitores: Tk ve un escritorio de 3286x1080 que los une.
    "portátil a la izquierda de otro monitor": Monitor(0, 312, 1366, 768),
    "monitor a la derecha de un portátil": Monitor(1366, 0, 1920, 1080),
}


def _partes(geometria: str) -> tuple[int, int, int, int]:
    """Ancho, alto, x e y de una geometría de Tk como ``800x600+10+20``."""
    tamano, x, y = geometria.split("+")
    ancho, alto = tamano.split("x")
    return int(ancho), int(alto), int(x), int(y)


class TestTamanoVentana:
    """El tamaño y la posición con que se abre la ventana."""

    def test_en_pantallas_grandes_usa_el_tamano_preferido_centrado(self):
        """En un monitor grande, el tamaño preferido, centrado."""
        ancho, alto, x, y = _partes(aplicacion.calcular_geometria(Monitor(0, 0, 1920, 1080)))
        assert (ancho, alto) == aplicacion.TAMANO_VENTANA
        assert x == (1920 - ancho) // 2
        assert y > 0

    @pytest.mark.parametrize("nombre", list(MONITORES_HABITUALES))
    def test_cabe_entera_en_su_monitor_sin_ocuparlo_entero(self, nombre: str):
        """En los monitores habituales (también con escalado o con dos monitores) cabe entera, sin
        quedar bajo las barras del sistema.
        """
        monitor = MONITORES_HABITUALES[nombre]
        ancho, alto, x, y = _partes(aplicacion.calcular_geometria(monitor))
        ancho_minimo, alto_minimo = aplicacion.TAMANO_MINIMO_VENTANA
        assert ancho_minimo <= ancho <= aplicacion.TAMANO_VENTANA[0]
        assert alto_minimo <= alto <= aplicacion.TAMANO_VENTANA[1]
        assert ancho < monitor.ancho
        # Dentro del monitor, sin quedar debajo de la barra de tareas ni de la de título.
        assert monitor.x <= x and x + ancho <= monitor.x + monitor.ancho
        assert monitor.y <= y and y + alto + aplicacion.RESERVA_VERTICAL <= (
            monitor.y + monitor.alto
        )

    def test_en_pantallas_pequenas_se_reduce(self):
        """En un portátil de 1366x768 se abre más baja."""
        _, alto, _, _ = _partes(aplicacion.calcular_geometria(Monitor(0, 0, 1366, 768)))
        assert alto < aplicacion.TAMANO_VENTANA[1]

    def test_en_pantallas_diminutas_no_baja_del_minimo(self):
        """En una pantalla más pequeña que el mínimo, se abre con el mínimo."""
        ancho, alto, x, y = _partes(aplicacion.calcular_geometria(Monitor(0, 0, 800, 600)))
        assert (ancho, alto) == aplicacion.TAMANO_MINIMO_VENTANA
        assert x >= 0 and y >= 0

    def test_la_ventana_se_abre_con_ese_tamano(self, raiz: tk.Tk):
        """La ventana real se abre con el tamaño calculado para su monitor."""
        raiz.update()
        esperado = aplicacion.calcular_geometria(monitor_de_trabajo(raiz))
        assert (raiz.winfo_width(), raiz.winfo_height()) == _partes(esperado)[:2]

    @pytest.mark.parametrize(
        ("raton", "monitor"),
        [
            ((300, 600), "portátil a la izquierda de otro monitor"),
            ((2300, 500), "monitor a la derecha de un portátil"),
        ],
        ids=["raton-en-el-portatil", "raton-en-el-monitor"],
    )
    def test_con_dos_monitores_se_abre_entera_en_el_del_raton(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raton: tuple[int, int], monitor: str
    ):
        """Linux con un portátil y un monitor al lado: antes quedaba partida entre los dos."""
        portatil = MONITORES_HABITUALES["portátil a la izquierda de otro monitor"]
        externo = MONITORES_HABITUALES["monitor a la derecha de un portátil"]
        monkeypatch.setattr(monitores, "monitores_x11", lambda pantalla: [portatil, externo])
        monkeypatch.setattr(tk.Misc, "winfo_pointerxy", lambda self: raton)
        monkeypatch.setenv("FILE_ORGANIZER_CONFIG", str(tmp_path / "uc.json"))
        try:
            ventana = aplicacion.construir_aplicacion()
        except tk.TclError as error:
            pytest.skip(f"No hay pantalla disponible: {error}")
        try:
            if ventana.tk.call("tk", "windowingsystem") != "x11":
                pytest.skip("Solo afecta a X11")
            ventana.update()
            esperado = MONITORES_HABITUALES[monitor]
            x, y = ventana.winfo_x(), ventana.winfo_y()
            assert esperado.x <= x and x + ventana.winfo_width() <= esperado.x + esperado.ancho
            assert esperado.y <= y and y + ventana.winfo_height() <= esperado.y + esperado.alto
        finally:
            ventana.destroy()


CONTROLES = (BotonRedondeado, CampoRedondeado, ttk.Radiobutton, ttk.Checkbutton, ttk.Label)


def _descendientes(widget: tk.Misc) -> Iterator[tk.Misc]:
    """Todos los widgets que hay dentro de ``widget``, a cualquier nivel."""
    for hijo in widget.winfo_children():
        yield hijo
        yield from _descendientes(hijo)


def _colocado(widget: tk.Misc, hasta: tk.Misc) -> bool:
    """Si el widget y sus contenedores están colocados (no ocultos a propósito)."""
    actual: tk.Misc | None = widget
    while actual is not None and actual is not hasta:
        if not actual.winfo_manager():
            return False
        actual = actual.master
    return True


class TestTodoCabe:
    """Con el tamaño por defecto y con el mínimo no se corta ningún control en ninguna pestaña
    y queda sitio para el registro."""

    @pytest.mark.parametrize(
        ("geometria", "lineas_minimas"), [(POR_DEFECTO, 3), (MINIMO, 1)], ids=["defecto", "minimo"]
    )
    @pytest.mark.parametrize(
        "pestana",
        [
            "Organizar",
            "Organizar:destino",
            "Historial",
            "Cifrado:contrasena",
            "Cifrado:archivo_clave",
        ],
    )
    def test_ningun_control_queda_cortado(
        self, raiz: tk.Tk, geometria: str, lineas_minimas: int, pestana: str
    ):
        """En cada pestaña (y con cada método de clave en Cifrado), ningún control queda cortado ni
        de alto ni de ancho, y el registro conserva sus líneas mínimas.
        """
        barra = next(
            o for o in gc.get_objects() if isinstance(o, BarraPestanas) and o.tk is raiz.tk
        )
        etiqueta, _, metodo = pestana.partition(":")
        barra.seleccionar(etiqueta)
        if metodo == "destino":
            organizar = _pestana_organizar(raiz)
            organizar._var_usar_destino.set(True)
            organizar._mostrar_destino()
        elif metodo:
            cifrado = next(
                o
                for o in gc.get_objects()
                if isinstance(o, PestanaCifrado) and o.marco.tk is raiz.tk
            )
            cifrado._metodo.set(metodo)
            cifrado._actualizar_metodo()
        _redimensionar(raiz, geometria)
        _saltar_si_la_pantalla_no_deja(raiz, geometria)

        pagina = barra.contenido
        limite = pagina.winfo_rooty() + pagina.winfo_height()
        visibles = [w for w in _descendientes(pagina) if _colocado(w, pagina)]
        cortados = [
            w
            for w in visibles
            if isinstance(w, CONTROLES)
            and (
                not w.winfo_ismapped()
                or w.winfo_height() < w.winfo_reqheight()
                or w.winfo_rooty() + w.winfo_height() > limite
            )
        ]
        assert cortados == []
        # Tampoco a lo ancho: un botón o una casilla más estrechos que su texto lo cortan.
        estrechos = [
            w
            for w in visibles
            if isinstance(w, (BotonRedondeado, ttk.Checkbutton, ttk.Radiobutton))
            and w.winfo_width() < w.winfo_reqwidth()
        ]
        assert estrechos == []

        registro = next(w for w in visibles if isinstance(w, tk.Text))
        alto_linea = registro.winfo_reqheight() / int(registro.cget("height"))
        assert registro.winfo_ismapped()
        assert registro.winfo_height() >= lineas_minimas * alto_linea

    @pytest.mark.parametrize("pestana", ["Organizar", "Historial", "Cifrado"])
    def test_las_filas_de_botones_tienen_margen_a_lo_ancho(self, raiz: tk.Tk, pestana: str):
        """A tamaño mínimo, cada fila de botones deja libre al menos un 10 % de lo que piden.

        Las fuentes de Windows son algo más anchas que las de Linux: con «Limpiar historial» la
        fila del Historial dejaba aquí 17 px libres (un 3 %) y en Windows su último botón salía
        cortado, aunque en Linux cupiera.
        """
        barra = next(
            o for o in gc.get_objects() if isinstance(o, BarraPestanas) and o.tk is raiz.tk
        )
        barra.seleccionar(pestana)
        _redimensionar(raiz, MINIMO)

        filas: dict[tk.Misc, list[BotonRedondeado]] = {}
        for w in _descendientes(barra.contenido):
            if (
                isinstance(w, BotonRedondeado)
                and w.winfo_ismapped()
                and w.winfo_manager() == "pack"
            ):
                filas.setdefault(w.master, []).append(w)
        filas_de_varios = [botones for botones in filas.values() if len(botones) > 1]
        assert filas_de_varios
        for botones in filas_de_varios:
            separacion = sum(
                int(n) for b in botones for n in re.findall(r"\d+", str(b.pack_info()["padx"]))
            )
            pedido = sum(b.winfo_reqwidth() for b in botones)
            libre = botones[0].master.winfo_width() - pedido - separacion
            assert libre >= 0.10 * pedido, [b._texto for b in botones]


class _HiloSincrono:
    """Sustituye a threading.Thread: ejecuta el trabajo en el acto, en el hilo de la interfaz.

    Así los tests no necesitan el bucle principal de Tk para recibir el resultado.
    """

    def __init__(self, target, args=(), daemon=None) -> None:
        self._trabajo = target
        self._argumentos = args

    def start(self) -> None:
        """Ejecuta el trabajo en el acto."""
        self._trabajo(*self._argumentos)


# Sustituye el módulo threading solo dentro de la pestaña: el resto del programa (watchdog,
# filelock...) sigue creando hilos de verdad.
_HILOS_SINCRONOS = SimpleNamespace(Thread=_HiloSincrono)


@pytest.fixture
def sincrono(monkeypatch: pytest.MonkeyPatch) -> None:
    """Las operaciones de la pestaña Organizar se ejecutan en el hilo del test."""
    monkeypatch.setattr(pestana_organizar, "threading", _HILOS_SINCRONOS)


def _texto_registro(registro: tk.Text) -> str:
    """Todo el texto del panel de registro."""
    return registro.get("1.0", "end")


def _ficheros(carpeta: Path, *nombres: str) -> Path:
    """Crea la carpeta con esos ficheros."""
    carpeta.mkdir(parents=True, exist_ok=True)
    for nombre in nombres:
        (carpeta / nombre).write_text("x")
    return carpeta


def _pestana_historial(ventana: tk.Tk) -> PestanaHistorial:
    """La pestaña Historial de esta ventana (gc puede guardar las de ventanas ya cerradas)."""
    return next(
        o for o in gc.get_objects() if isinstance(o, PestanaHistorial) and o.marco.tk is ventana.tk
    )


class TestSimular:
    """El botón «Simular» de la pestaña Organizar."""

    def test_muestra_adonde_iria_cada_fichero_sin_mover_nada(
        self, raiz: tk.Tk, tmp_path: Path, sincrono: None
    ):
        """Lista los ficheros por carpeta de destino, sin mover nada ni crear historial."""
        carpeta = _ficheros(tmp_path / "origen", "a.jpg", "b.png", "informe.pdf")
        pestana = _pestana_organizar(raiz)
        pestana.ruta.establecer(str(carpeta))

        pestana._simular()
        raiz.update()

        assert sorted(p.name for p in carpeta.iterdir()) == ["a.jpg", "b.png", "informe.pdf"]
        assert not (carpeta / NOMBRE_ARCHIVO_HISTORIAL).exists()
        texto = _texto_registro(pestana.registro)
        assert "[SIMULACIÓN] 2 fichero(s) -> Imagenes/: a.jpg, b.png" in texto
        assert "[SIMULACIÓN] 1 fichero(s) -> Documentos/: informe.pdf" in texto
        assert "no se ha movido nada" in texto

    def test_no_hace_backup_aunque_este_marcado(self, raiz: tk.Tk, tmp_path: Path, sincrono: None):
        """Simular no crea el zip aunque la casilla de backup esté marcada."""
        carpeta = _ficheros(tmp_path / "origen", "a.jpg")
        pestana = _pestana_organizar(raiz)
        pestana.ruta.establecer(str(carpeta))
        pestana._var_backup.set(True)

        pestana._simular()
        raiz.update()

        assert not (carpeta / NOMBRE_CARPETA_BACKUPS).exists()

    def test_resume_las_listas_largas_de_nombres(self):
        """Con muchos nombres enseña los primeros y cuántos más hay."""
        assert _resumir_nombres(["a", "b"]) == "a, b"
        assert _resumir_nombres(list("abcdef")) == "a, b, c, d y 2 más"


class TestDestino:
    """Organizar en otra carpeta de destino desde la pestaña Organizar."""

    def test_la_fila_de_destino_solo_se_ve_con_la_casilla_marcada(self, raiz: tk.Tk):
        """La fila para elegir el destino aparece y desaparece con su casilla."""
        pestana = _pestana_organizar(raiz)
        assert not pestana.fila_destino.winfo_manager()

        pestana._var_usar_destino.set(True)
        pestana._mostrar_destino()
        assert pestana.fila_destino.winfo_manager() == "pack"

        pestana._var_usar_destino.set(False)
        pestana._mostrar_destino()
        assert not pestana.fila_destino.winfo_manager()

    def test_organiza_y_deshace_en_otra_carpeta(self, raiz: tk.Tk, tmp_path: Path, sincrono: None):
        """Organiza en el destino (y guarda allí el historial) y deshacer devuelve los ficheros al
        origen.
        """
        origen = _ficheros(tmp_path / "origen", "a.jpg")
        destino = tmp_path / "ordenado"
        pestana = _pestana_organizar(raiz)
        pestana.ruta.establecer(str(origen))
        pestana._var_usar_destino.set(True)
        pestana.destino.establecer(str(destino))

        pestana._organizar()
        raiz.update()
        assert (destino / "Imagenes" / "a.jpg").exists()
        assert not (origen / "a.jpg").exists()
        assert (destino / NOMBRE_ARCHIVO_HISTORIAL).exists()

        pestana._deshacer()
        raiz.update()
        assert (origen / "a.jpg").exists()
        assert not (destino / "Imagenes").exists()

    def test_casilla_marcada_sin_destino_avisa_y_no_hace_nada(
        self, raiz: tk.Tk, tmp_path: Path, sincrono: None
    ):
        """Con la casilla marcada y el destino vacío, avisa y no mueve nada."""
        origen = _ficheros(tmp_path / "origen", "a.jpg")
        pestana = _pestana_organizar(raiz)
        pestana.ruta.establecer(str(origen))
        pestana._var_usar_destino.set(True)
        pestana.destino.establecer("")

        pestana._organizar()
        raiz.update()

        assert (origen / "a.jpg").exists()
        assert "Indica la carpeta de destino" in _texto_registro(pestana.registro)

    def test_un_destino_que_es_un_fichero_da_error(
        self, raiz: tk.Tk, tmp_path: Path, sincrono: None
    ):
        """Si el destino es un fichero, da error y no mueve nada."""
        origen = _ficheros(tmp_path / "origen", "a.jpg")
        fichero = tmp_path / "no_es_carpeta.txt"
        fichero.write_text("x")
        pestana = _pestana_organizar(raiz)
        pestana.ruta.establecer(str(origen))
        pestana._var_usar_destino.set(True)
        pestana.destino.establecer(str(fichero))

        pestana._organizar()
        raiz.update()

        assert (origen / "a.jpg").exists()
        assert "El destino es un fichero, no una carpeta" in _texto_registro(pestana.registro)

    def test_la_vigilancia_usa_el_destino_y_bloquea_las_carpetas(
        self, raiz: tk.Tk, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sincrono: None
    ):
        """El vigilante recibe el destino, y mientras vigila no se puede cambiar; al detener, sí."""
        creados: list[dict] = []

        class VigilanteFalso:
            def __init__(self, **opciones) -> None:
                creados.append(opciones)

            def iniciar(self) -> None:
                pass

            def detener(self) -> None:
                pass

        monkeypatch.setattr(pestana_organizar, "VigilanteCarpeta", VigilanteFalso)
        origen = _ficheros(tmp_path / "origen")
        destino = tmp_path / "ordenado"
        pestana = _pestana_organizar(raiz)
        pestana.ruta.establecer(str(origen))
        pestana._var_usar_destino.set(True)
        pestana.destino.establecer(str(destino))

        pestana._iniciar_vigilante()
        assert creados[0]["carpeta_destino"] == destino
        assert str(pestana.destino.campo.cget("state")) == "disabled"
        assert pestana.casilla_destino.instate(["disabled"])

        pestana._detener_vigilante()
        raiz.update()  # el aviso de que el vigilante ya se ha detenido llega por after()
        assert str(pestana.destino.campo.cget("state")) == "normal"
        assert not pestana.casilla_destino.instate(["disabled"])

    def test_recuerda_el_destino_entre_sesiones(self, raiz: tk.Tk, tmp_path: Path, sincrono: None):
        """Al abrir otra vez la ventana, la casilla y el destino siguen como se dejaron."""
        origen = _ficheros(tmp_path / "origen", "a.jpg")
        destino = tmp_path / "ordenado"
        pestana = _pestana_organizar(raiz)
        pestana.ruta.establecer(str(origen))
        pestana._var_usar_destino.set(True)
        pestana.destino.establecer(str(destino))
        pestana._organizar()
        raiz.update()

        otra = aplicacion.construir_aplicacion()
        try:
            nueva = _pestana_organizar(otra)
            assert nueva._var_usar_destino.get() is True
            assert nueva.destino.obtener() == str(destino)
            assert nueva.fila_destino.winfo_manager() == "pack"
        finally:
            otra.destroy()


class TestHistorialYEstadisticas:
    """La pestaña Historial: ver el historial, estadísticas y limpiar."""

    @pytest.fixture
    def historial_sincrono(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Las operaciones de la pestaña Historial se ejecutan en el hilo del test."""
        monkeypatch.setattr(pestana_historial, "threading", _HILOS_SINCRONOS)

    def test_estadisticas_por_categoria_con_porcentaje(
        self, raiz: tk.Tk, tmp_path: Path, historial_sincrono: None
    ):
        """Cada categoría con su número de ficheros, su porcentaje y una barra proporcional, más el
        total.
        """
        carpeta = _ficheros(tmp_path / "origen", "a.jpg", "b.png", "informe.pdf")
        OrganizadorArchivos(carpeta_origen=carpeta).ejecutar()
        pestana = _pestana_historial(raiz)
        pestana.ruta.establecer(str(carpeta))

        pestana._ver_estadisticas()
        raiz.update()

        lineas = _texto_registro(pestana.registro).splitlines()
        imagenes = next(linea for linea in lineas if linea.startswith("Imagenes"))
        documentos = next(linea for linea in lineas if linea.startswith("Documentos"))
        assert "2 fichero(s)" in imagenes and "66.7 %" in imagenes
        assert "1 fichero(s)" in documentos and "33.3 %" in documentos
        assert imagenes.count("█") > documentos.count("█")
        assert any(linea.startswith("TOTAL") and "3 fichero(s)" in linea for linea in lineas)

    def test_el_historial_de_otra_carpeta_se_ve_desde_el_destino(
        self, raiz: tk.Tk, tmp_path: Path, historial_sincrono: None
    ):
        """Desde el destino se ve de qué carpeta vienen los ficheros; desde el origen, se explica
        que hay que elegir el destino.
        """
        origen = _ficheros(tmp_path / "origen", "a.jpg")
        destino = tmp_path / "ordenado"
        OrganizadorArchivos(carpeta_origen=origen, carpeta_destino=destino).ejecutar()
        pestana = _pestana_historial(raiz)

        pestana.ruta.establecer(str(destino))
        pestana._cargar_historial()
        raiz.update()
        texto = _texto_registro(pestana.registro)
        assert f"Ficheros organizados desde {origen.resolve()}" in texto
        assert "a.jpg" in texto

        pestana.ruta.establecer(str(origen))
        pestana._cargar_historial()
        raiz.update()
        assert "elige esa carpeta" in _texto_registro(pestana.registro)

    def test_limpiar_desde_el_destino(
        self, raiz: tk.Tk, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, historial_sincrono: None
    ):
        """Limpiar desde el destino borra el historial sin mover ningún fichero."""
        origen = _ficheros(tmp_path / "origen", "a.jpg")
        destino = tmp_path / "ordenado"
        OrganizadorArchivos(carpeta_origen=origen, carpeta_destino=destino).ejecutar()
        monkeypatch.setattr(pestana_historial.messagebox, "askyesno", lambda *a, **k: True)
        pestana = _pestana_historial(raiz)
        pestana.ruta.establecer(str(destino))

        pestana._limpiar_historial()
        raiz.update()

        assert "Historial borrado (1 movimiento(s))" in _texto_registro(pestana.registro)
        assert (destino / "Imagenes" / "a.jpg").exists()  # limpiar no mueve nada


class TestRegistroAlFinal:
    """El registro de cada pestaña siempre deja ver su última línea."""

    @pytest.mark.parametrize("pestana", ["Organizar", "Historial", "Cifrado"])
    @pytest.mark.parametrize("geometria", [POR_DEFECTO, MINIMO], ids=["defecto", "minimo"])
    def test_la_ultima_linea_escrita_se_ve_entera(self, raiz: tk.Tk, pestana: str, geometria: str):
        """Antes se veía cortada si el alto del panel no era múltiplo del de una línea."""
        barra = next(
            o for o in gc.get_objects() if isinstance(o, BarraPestanas) and o.tk is raiz.tk
        )
        barra.seleccionar(pestana)
        if pestana == "Organizar":  # con la fila de destino el panel queda en 93 px
            organizar = _pestana_organizar(raiz)
            organizar._var_usar_destino.set(True)
            organizar._mostrar_destino()
        _redimensionar(raiz, geometria)
        tipo = {"Organizar": PestanaOrganizar, "Historial": PestanaHistorial}.get(
            pestana, PestanaCifrado
        )
        objeto = next(o for o in gc.get_objects() if isinstance(o, tipo) and o.marco.tk is raiz.tk)
        escribir = getattr(objeto, "_registrar", None) or objeto._registrar_linea

        for i in range(1, 13):
            escribir("info", "[INFO]", f"mensaje {i}")
        raiz.update()

        registro = objeto.registro
        ultima = registro.search("mensaje 12", "1.0")
        _x, y, _ancho, alto, _base = registro.dlineinfo(ultima)
        assert y >= 0 and y + alto <= registro.winfo_height()


class _VigilanteFalso:
    """Vigilante que no vigila nada: solo registra cómo se creó y si se detuvo."""

    creados: ClassVar[list["_VigilanteFalso"]] = []

    def __init__(self, **opciones) -> None:
        self.opciones = opciones
        self.detenido = False
        _VigilanteFalso.creados.append(self)

    def iniciar(self) -> None:
        """Vuelve enseguida, sin vigilar."""

    def detener(self) -> None:
        """Anota que se detuvo."""
        self.detenido = True


@pytest.fixture
def vigilante_falso(monkeypatch: pytest.MonkeyPatch, sincrono: None) -> type[_VigilanteFalso]:
    """La pestaña Organizar usa el vigilante falso, con su lista de creados vacía."""
    monkeypatch.setattr(_VigilanteFalso, "creados", [])
    monkeypatch.setattr(pestana_organizar, "VigilanteCarpeta", _VigilanteFalso)
    return _VigilanteFalso


def _habilitado(boton: BotonRedondeado) -> bool:
    """Si el botón está activo."""
    return bool(boton._habilitado)


class TestUnSoloEscritorMientrasSeVigila:
    """Mientras se vigila solo el vigilante escribe el historial de la carpeta."""

    def _vigilar(self, raiz: tk.Tk, carpeta: Path) -> PestanaOrganizar:
        """Empieza a vigilar ``carpeta`` desde la pestaña Organizar."""
        pestana = _pestana_organizar(raiz)
        pestana.ruta.establecer(str(carpeta))
        pestana._iniciar_vigilante()
        return pestana

    def test_organizar_y_deshacer_se_desactivan_y_simular_no(
        self, raiz: tk.Tk, tmp_path: Path, vigilante_falso: type[_VigilanteFalso]
    ):
        """Vigilando, se desactiva lo que escribe el historial o cambia la carpeta; simular y
        detener siguen activos.
        """
        pestana = self._vigilar(raiz, _ficheros(tmp_path / "carpeta"))

        assert not _habilitado(pestana.boton_organizar)
        assert not _habilitado(pestana.boton_deshacer)
        assert not _habilitado(pestana.boton_examinar)
        assert not _habilitado(pestana.boton_pegar)
        assert _habilitado(pestana.boton_simular)
        assert _habilitado(pestana.boton_vigilar)  # para poder detener

        pestana._detener_vigilante()
        raiz.update()
        assert _habilitado(pestana.boton_organizar) and _habilitado(pestana.boton_deshacer)

    def test_siguen_desactivados_hasta_que_el_vigilante_termina_de_detenerse(
        self, raiz: tk.Tk, tmp_path: Path, vigilante_falso: type[_VigilanteFalso]
    ):
        """Al detenerse guarda su historial: deshacer antes leería uno a medias."""
        pestana = self._vigilar(raiz, _ficheros(tmp_path / "carpeta"))

        pestana._detener_vigilante()  # el aviso de que terminó aún no se ha procesado
        assert vigilante_falso.creados[0].detenido
        assert not _habilitado(pestana.boton_deshacer)
        assert not _habilitado(pestana.boton_vigilar)

        raiz.update()
        assert _habilitado(pestana.boton_deshacer) and _habilitado(pestana.boton_vigilar)

    def test_simular_mientras_se_vigila_no_reactiva_organizar(
        self, raiz: tk.Tk, tmp_path: Path, vigilante_falso: type[_VigilanteFalso]
    ):
        """Al acabar una simulación no se vuelven a activar organizar ni deshacer."""
        carpeta = _ficheros(tmp_path / "carpeta", "a.jpg")
        pestana = self._vigilar(raiz, carpeta)

        pestana._simular()
        raiz.update()

        assert "[SIMULACIÓN]" in _texto_registro(pestana.registro)
        assert not _habilitado(pestana.boton_organizar)
        assert not _habilitado(pestana.boton_deshacer)

    @pytest.mark.parametrize("accion", ["organizar", "_deshacer"])
    def test_ni_por_atajo_de_teclado_se_organiza_o_deshace(
        self, raiz: tk.Tk, tmp_path: Path, vigilante_falso: type[_VigilanteFalso], accion: str
    ):
        """Los atajos de teclado tampoco organizan ni deshacen mientras se vigila: se avisa."""
        carpeta = _ficheros(tmp_path / "carpeta", "a.jpg")
        OrganizadorArchivos(carpeta_origen=carpeta).ejecutar()
        (carpeta / "b.pdf").write_text("x")
        pestana = self._vigilar(raiz, carpeta)

        getattr(pestana, accion)()  # Ctrl+Enter llama a organizar() aunque el botón esté gris
        raiz.update()

        assert (carpeta / "Imagenes" / "a.jpg").exists()  # no se ha deshecho
        assert (carpeta / "b.pdf").exists()  # ni se ha organizado a mano
        assert "Detén la vigilancia antes de" in _texto_registro(pestana.registro)

    def test_no_se_empieza_a_vigilar_con_una_operacion_en_curso(
        self, raiz: tk.Tk, tmp_path: Path, vigilante_falso: type[_VigilanteFalso]
    ):
        """Con una operación en marcha, vigilar no hace nada."""
        pestana = _pestana_organizar(raiz)
        pestana.ruta.establecer(str(_ficheros(tmp_path / "carpeta")))
        pestana._establecer_operacion_en_curso(True)

        pestana._iniciar_vigilante()

        assert vigilante_falso.creados == []
        assert not _habilitado(pestana.boton_vigilar)

    def test_no_se_limpia_el_historial_de_la_carpeta_vigilada(
        self,
        raiz: tk.Tk,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        vigilante_falso: type[_VigilanteFalso],
    ):
        """La pestaña Historial no limpia el de la carpeta vigilada ni pregunta; al detener, ya se
        puede.
        """
        monkeypatch.setattr(pestana_historial, "threading", _HILOS_SINCRONOS)
        carpeta = _ficheros(tmp_path / "carpeta", "a.jpg")
        OrganizadorArchivos(carpeta_origen=carpeta).ejecutar()
        organizar = self._vigilar(raiz, carpeta)
        historial = _pestana_historial(raiz)
        historial.ruta.establecer(str(carpeta))

        def no_deberia_preguntar(*args, **kwargs):
            raise AssertionError("no debería llegar a pedir confirmación")

        monkeypatch.setattr(pestana_historial.messagebox, "askyesno", no_deberia_preguntar)
        historial._limpiar_historial()
        raiz.update()
        assert "detén la vigilancia" in _texto_registro(historial.registro)
        assert (carpeta / NOMBRE_ARCHIVO_HISTORIAL).exists()

        organizar._detener_vigilante()
        raiz.update()
        monkeypatch.setattr(pestana_historial.messagebox, "askyesno", lambda *a, **k: True)
        historial._limpiar_historial()
        raiz.update()
        assert "Historial borrado" in _texto_registro(historial.registro)


class TestCarpetaEnUsoPorOtraVentana:
    """Otra ventana (otro proceso) tiene la carpeta: aquí se simula con un bloqueo propio."""

    @pytest.fixture
    def carpeta_en_uso(self, tmp_path: Path) -> Iterator[Path]:
        """Una carpeta con una foto, bloqueada mientras dura el test."""
        carpeta = _ficheros(tmp_path / "carpeta", "a.jpg")
        otra_ventana = BloqueoCarpeta(carpeta)
        otra_ventana.adquirir()
        yield carpeta
        otra_ventana.liberar()

    def test_vigilar_avisa_y_no_se_queda_como_vigilando(
        self, raiz: tk.Tk, carpeta_en_uso: Path, sincrono: None
    ):
        """Vigilar avisa de que está en uso y la pestaña vuelve a su estado normal."""
        pestana = _pestana_organizar(raiz)
        pestana.ruta.establecer(str(carpeta_en_uso))

        pestana._iniciar_vigilante()
        raiz.update()

        texto = _texto_registro(pestana.registro)
        assert "No se puede vigilar" in texto and "está en uso" in texto
        assert pestana._vigilante is None and not pestana.vigilando
        assert pestana.etiqueta_estado.cget("text") == "esperando ficheros..."
        assert _habilitado(pestana.boton_organizar) and _habilitado(pestana.boton_vigilar)
        assert str(pestana.ruta.campo.cget("state")) == "normal"

    def test_organizar_avisa_y_no_mueve_nada(
        self, raiz: tk.Tk, carpeta_en_uso: Path, sincrono: None
    ):
        """Organizar avisa de que está en uso, no mueve nada y el botón sigue activo."""
        pestana = _pestana_organizar(raiz)
        pestana.ruta.establecer(str(carpeta_en_uso))

        pestana._organizar()
        raiz.update()

        assert "está en uso" in _texto_registro(pestana.registro)
        assert (carpeta_en_uso / "a.jpg").exists()
        assert _habilitado(pestana.boton_organizar)

    def test_limpiar_historial_avisa(
        self, raiz: tk.Tk, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Limpiar el historial avisa de que está en uso y no lo borra."""
        monkeypatch.setattr(pestana_historial, "threading", _HILOS_SINCRONOS)
        monkeypatch.setattr(pestana_historial.messagebox, "askyesno", lambda *a, **k: True)
        carpeta = _ficheros(tmp_path / "carpeta", "a.jpg")
        OrganizadorArchivos(carpeta_origen=carpeta).ejecutar()
        historial = _pestana_historial(raiz)
        historial.ruta.establecer(str(carpeta))

        with BloqueoCarpeta(carpeta):
            historial._limpiar_historial()
            raiz.update()

        assert "No se pudo limpiar el historial" in _texto_registro(historial.registro)
        assert "está en uso" in _texto_registro(historial.registro)
        assert (carpeta / NOMBRE_ARCHIVO_HISTORIAL).exists()


class TestDeshacerPorPasosEnLaGui:
    """El deshacer por pasos visto desde la ventana."""

    def test_deshacer_avisa_del_siguiente_paso_y_de_los_renombrados(
        self, raiz: tk.Tk, tmp_path: Path, sincrono: None
    ):
        """Tras deshacer dice qué paso queda; al deshacer el siguiente, avisa de la foto restaurada
        con otro nombre para no pisar la otra.
        """
        carpeta = _ficheros(tmp_path / "carpeta")
        (carpeta / "foto.jpg").write_text("A")
        OrganizadorArchivos(carpeta_origen=carpeta).ejecutar()
        (carpeta / "foto.jpg").write_text("B")
        OrganizadorArchivos(carpeta_origen=carpeta).ejecutar()
        pestana = _pestana_organizar(raiz)
        pestana.ruta.establecer(str(carpeta))

        pestana._deshacer()
        raiz.update()
        texto = _texto_registro(pestana.registro)
        assert "Quedan 1 paso(s) en el historial" in texto
        assert "revertirá la organización del" in texto

        pestana._deshacer()
        raiz.update()
        assert "foto.jpg ya existía: restaurado como foto_1.jpg" in _texto_registro(
            pestana.registro
        )
        assert (carpeta / "foto.jpg").read_text() == "B"
        assert (carpeta / "foto_1.jpg").read_text() == "A"

    def test_la_pestana_historial_muestra_los_pasos_y_las_estadisticas_del_ultimo(
        self, raiz: tk.Tk, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """El historial se lista por pasos desde el más reciente, empezando arriba; las estadísticas
        son las del último paso.
        """
        monkeypatch.setattr(pestana_historial, "threading", _HILOS_SINCRONOS)
        carpeta = _ficheros(tmp_path / "carpeta", "lunes.pdf")
        OrganizadorArchivos(carpeta_origen=carpeta).ejecutar()
        (carpeta / "martes.jpg").write_text("x")
        OrganizadorArchivos(carpeta_origen=carpeta).ejecutar()
        historial = _pestana_historial(raiz)
        historial.ruta.establecer(str(carpeta))

        historial._cargar_historial()
        raiz.update()
        texto = _texto_registro(historial.registro)
        assert texto.index("PASO 1") < texto.index("martes.jpg") < texto.index("PASO 2")
        assert texto.index("PASO 2") < texto.index("lunes.pdf")
        assert "en 2 paso(s)" in texto
        assert historial.registro.yview()[0] == 0  # se ve desde el principio: el paso 1

        historial._limpiar_panel()
        historial._ver_estadisticas()
        raiz.update()
        texto = _texto_registro(historial.registro)
        assert "ESTADÍSTICAS DEL ÚLTIMO PASO" in texto
        assert "Imagenes" in texto and "Documentos" not in texto  # solo el del martes


def test_generar_clave_en_la_gui_sobrescribe_si_el_dialogo_lo_confirmo(
    raiz: tk.Tk, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """El diálogo de «Guardar» de Tk ya pregunta antes de reemplazar un fichero: si el usuario
    acepta, la GUI sobrescribe la clave (la consola, que no pregunta, se niega)."""
    from file_organizer.gui import pestana_cifrado

    clave = Cifrador.generar_clave(tmp_path / "mia.key")
    original = clave.read_bytes()
    monkeypatch.setattr(
        pestana_cifrado.filedialog, "asksaveasfilename", lambda **_opciones: str(clave)
    )
    pestana = next(
        o for o in gc.get_objects() if isinstance(o, PestanaCifrado) and o.marco.tk is raiz.tk
    )

    pestana._generar_clave()

    assert clave.read_bytes() != original
    assert "Clave generada" in _texto_registro(pestana.registro)


class TestBordeRedondeado:
    """El fondo de botones y marcos se dibuja píxel a píxel: antes era un polígono suavizado
    de Tk y en Windows le faltaban trozos del borde inferior y del derecho."""

    FONDO, BORDE, EXTERIOR = "#141414", "#c8c8c8", "#000000"

    def _hexa(self, imagen: tk.PhotoImage, x: int, y: int) -> str:
        """El color del píxel ``(x, y)`` de la imagen, como ``#rrggbb``."""
        return "#{:02x}{:02x}{:02x}".format(*imagen.get(x, y))

    @pytest.mark.parametrize(("ancho", "alto"), [(170, 46), (171, 47), (40, 20)])
    def test_los_cuatro_lados_son_continuos(self, raiz: tk.Tk, ancho: int, alto: int):
        """Con anchos y altos pares e impares, el borde no tiene huecos en ningún lado."""
        radio = 10
        imagen = componentes._imagen_rectangulo_redondeado(
            raiz, ancho, alto, radio, self.FONDO, self.BORDE, self.EXTERIOR
        )
        for x in range(radio, ancho - radio):
            assert self._hexa(imagen, x, 0) == self.BORDE
            assert self._hexa(imagen, x, alto - 1) == self.BORDE  # abajo
        for y in range(radio, alto - radio):
            assert self._hexa(imagen, 0, y) == self.BORDE
            assert self._hexa(imagen, ancho - 1, y) == self.BORDE  # derecha
        assert self._hexa(imagen, ancho // 2, alto // 2) == self.FONDO

    def test_las_esquinas_dejan_ver_el_fondo_y_se_suavizan(self, raiz: tk.Tk):
        """Las esquinas son del color de fuera, simétricas y con tonos intermedios en la curva."""
        ancho, alto = 170, 46
        imagen = componentes._imagen_rectangulo_redondeado(
            raiz, ancho, alto, 10, self.FONDO, self.BORDE, self.EXTERIOR
        )
        for x, y in [(0, 0), (ancho - 1, 0), (0, alto - 1), (ancho - 1, alto - 1)]:
            assert self._hexa(imagen, x, y) == self.EXTERIOR
        # Las cuatro esquinas son simétricas y la curva tiene tonos intermedios.
        esquina = [self._hexa(imagen, x, 3) for x in range(10)]
        assert esquina == [self._hexa(imagen, ancho - 1 - x, alto - 4) for x in range(10)]
        assert set(esquina) - {self.FONDO, self.BORDE, self.EXTERIOR}

    def test_los_botones_usan_la_imagen_y_no_un_poligono(self, raiz: tk.Tk):
        """Ningún botón de la ventana se dibuja ya con un polígono."""
        raiz.update()
        botones = [
            o for o in gc.get_objects() if isinstance(o, BotonRedondeado) and o.tk is raiz.tk
        ]
        assert botones
        for boton in botones:
            tipos = {boton.type(item) for item in boton.find_all()}
            assert "polygon" not in tipos
            assert "image" in tipos or boton.winfo_width() < 4


class TestCifradoDesdeLaPestana:
    """Cifrar y descifrar de verdad desde la pestaña Cifrado, con un fichero de clave."""

    @pytest.fixture
    def cifrado(
        self, raiz: tk.Tk, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> PestanaCifrado:
        """La pestaña Cifrado con un fichero .key elegido, operaciones en el hilo del test y las
        confirmaciones anotadas (y aceptadas).
        """
        monkeypatch.setattr(pestana_cifrado, "threading", _HILOS_SINCRONOS)
        self.confirmaciones: list[str] = []
        monkeypatch.setattr(
            pestana_cifrado.messagebox,
            "askyesno",
            lambda titulo, mensaje: self.confirmaciones.append(mensaje) or True,
        )
        pestana = next(
            o for o in gc.get_objects() if isinstance(o, PestanaCifrado) and o.marco.tk is raiz.tk
        )
        pestana._metodo.set("archivo_clave")
        pestana._actualizar_metodo()
        pestana.campo_archivo_clave.establecer(str(Cifrador.generar_clave(tmp_path / "k.key")))
        return pestana

    def test_cifra_y_descifra_una_carpeta(
        self, raiz: tk.Tk, cifrado: PestanaCifrado, tmp_path: Path
    ):
        """Pide confirmación con el número real de ficheros, cifra solo los que tocan y descifrar
        los devuelve.
        """
        carpeta = _ficheros(tmp_path / "c", "a.txt", "b.txt", ".oculto", "viejo.txt.enc")
        (carpeta / "dentro.key").write_text("no se cifra")
        cifrado.ruta.establecer(str(carpeta))

        cifrado._cifrar()
        raiz.update()

        # Solo cuenta y cifra a.txt y b.txt: ni el oculto, ni la clave, ni lo ya cifrado.
        assert self.confirmaciones == [
            "Se van a cifrar 2 ficheros dentro de esta carpeta.\n\n¿Deseas continuar?"
        ]
        assert sorted(p.name for p in carpeta.iterdir()) == [
            ".oculto",
            "a.txt",
            "a.txt.enc",
            "b.txt",
            "b.txt.enc",
            "dentro.key",
            "viejo.txt.enc",
        ]
        assert "Cifrando: c" in _texto_registro(cifrado.registro)

        for nombre in ("a.txt", "b.txt", "viejo.txt.enc"):
            (carpeta / nombre).unlink()
        cifrado._descifrar()
        raiz.update()

        assert self.confirmaciones[-1].startswith("Se van a descifrar 2 ficheros")
        assert (carpeta / "a.txt").read_text() == "x"
        assert (carpeta / "b.txt").read_text() == "x"
        assert "Descifrando: c" in _texto_registro(cifrado.registro)

    def test_cifra_y_descifra_un_fichero_sin_pedir_confirmacion(
        self, raiz: tk.Tk, cifrado: PestanaCifrado, tmp_path: Path
    ):
        """Un solo fichero se cifra y descifra sin preguntar, borrando el original si se pidió."""
        fichero = _ficheros(tmp_path / "f", "nota.txt") / "nota.txt"
        cifrado.ruta.establecer(str(fichero))
        cifrado._borrar_original.set(True)

        cifrado._cifrar()
        raiz.update()
        assert not fichero.exists() and fichero.with_name("nota.txt.enc").exists()

        cifrado.ruta.establecer(str(fichero.with_name("nota.txt.enc")))
        cifrado._descifrar()
        raiz.update()

        assert fichero.read_text() == "x"
        assert not fichero.with_name("nota.txt.enc").exists()
        assert self.confirmaciones == []


def test_el_campo_de_ruta_se_rellena_con_lo_elegido_y_no_cambia_si_se_cancela(
    raiz: tk.Tk, monkeypatch: pytest.MonkeyPatch
):
    """Examinar rellena el campo con la carpeta o el fichero elegidos; si se cancela, el campo no
    cambia.
    """
    campo = CampoRedondeado(raiz)
    respuestas = iter(["/carpeta/elegida", "", "/fichero/elegido.key"])
    pedidos: list[dict[str, object]] = []

    def dialogo(**opciones: object) -> str:
        pedidos.append(opciones)
        return next(respuestas)

    monkeypatch.setattr(componentes.filedialog, "askdirectory", dialogo)
    monkeypatch.setattr(componentes.filedialog, "askopenfilename", dialogo)

    campo.elegir_carpeta("Elige")
    assert campo.obtener() == "/carpeta/elegida"
    campo.elegir_carpeta("Elige")  # cancelado: devuelve ""
    assert campo.obtener() == "/carpeta/elegida"
    campo.elegir_fichero("Clave", filetypes=[("Clave", "*.key")])
    assert campo.obtener() == "/fichero/elegido.key"
    assert pedidos[-1] == {"title": "Clave", "filetypes": [("Clave", "*.key")]}


# Un PDF con su fecha de creación (enero de 2020) en los metadatos.
PDF_ENERO_2020 = b"%PDF-1.4\n<< /CreationDate (D:20200115103000) >>\n%%EOF"


class TestAgruparPorFecha:
    """Las opciones de «Agrupar» y el menú de categorías de la pestaña Organizar."""

    @staticmethod
    def _carpeta(base: Path) -> Path:
        """Un PDF de enero de 2020 (según sus metadatos) y una nota de junio de 2021."""
        carpeta = base / "origen"
        carpeta.mkdir()
        (carpeta / "doc.pdf").write_bytes(PDF_ENERO_2020)
        nota = carpeta / "nota.txt"
        nota.write_text("x")
        marca = time.mktime((2021, 6, 10, 12, 0, 0, 0, 0, -1))
        os.utime(nota, (marca, marca))
        return carpeta

    def test_una_opcion_por_criterio_con_su_ejemplo(self, raiz: tk.Tk):
        """Cada forma de agrupar tiene su ayuda con un ejemplo; las de fecha explican de dónde sale
        la fecha.
        """
        pestana = _pestana_organizar(raiz)
        assert set(pestana.ayudas_agrupacion) == set(CriterioAgrupacion)
        for criterio, ayuda in pestana.ayudas_agrupacion.items():
            assert DESCRIPCIONES_AGRUPACION[criterio].ejemplo in ayuda.texto
            assert (EXPLICACION_FECHA in ayuda.texto) is (criterio is not CriterioAgrupacion.TIPO)

    def test_propone_la_ultima_forma_de_agrupar(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Al abrir la ventana está marcada la última forma de agrupar guardada."""
        monkeypatch.setenv("FILE_ORGANIZER_CONFIG", str(tmp_path / "uc.json"))
        cfg = ConfiguracionUsuario().cargar()
        cfg.establecer("agrupar", "tipo-fecha")
        cfg.guardar()
        try:
            ventana = aplicacion.construir_aplicacion()
        except tk.TclError as error:
            pytest.skip(f"No hay pantalla disponible: {error}")
        try:
            assert _pestana_organizar(ventana)._var_agrupar.get() == "tipo-fecha"
        finally:
            ventana.destroy()

    def test_organiza_por_fecha_en_orden_y_dice_de_donde_sale_la_fecha(
        self, raiz: tk.Tk, tmp_path: Path, sincrono: None
    ):
        """Organiza por año y mes, lista los meses en orden, dice de dónde salió cada fecha y guarda
        la elección.
        """
        carpeta = self._carpeta(tmp_path)
        pestana = _pestana_organizar(raiz)
        pestana.ruta.establecer(str(carpeta))
        pestana._var_agrupar.set(CriterioAgrupacion.FECHA.value)

        pestana._organizar()
        raiz.update()

        assert (carpeta / "2020" / "01" / "doc.pdf").exists()
        assert (carpeta / "2021" / "06" / "nota.txt").exists()
        texto = _texto_registro(pestana.registro)
        assert texto.index("-> 2020/01/") < texto.index("-> 2021/06/")
        assert (
            "Fechas: 1 de los metadatos (PDF 1) y 1 de la fecha del propio fichero, porque no "
            "tenía metadatos con fecha." in texto
        )
        assert ConfiguracionUsuario().cargar().criterio_agrupacion() is CriterioAgrupacion.FECHA

    def test_simula_por_tipo_y_fecha(self, raiz: tk.Tk, tmp_path: Path, sincrono: None):
        """Simulando por tipo y fecha, dice la carpeta de cada fichero sin moverlos."""
        carpeta = self._carpeta(tmp_path)
        pestana = _pestana_organizar(raiz)
        pestana.ruta.establecer(str(carpeta))
        pestana._var_agrupar.set(CriterioAgrupacion.TIPO_FECHA.value)

        pestana._simular()
        raiz.update()

        assert sorted(p.name for p in carpeta.iterdir()) == ["doc.pdf", "nota.txt"]
        texto = _texto_registro(pestana.registro)
        assert "[SIMULACIÓN] 1 fichero(s) -> Documentos/2020/01/: doc.pdf" in texto
        assert "[SIMULACIÓN] 1 fichero(s) -> Documentos/2021/06/: nota.txt" in texto

    def test_por_tipo_no_habla_de_fechas(self, raiz: tk.Tk, tmp_path: Path, sincrono: None):
        """Por tipo no se muestra el resumen de fechas."""
        carpeta = self._carpeta(tmp_path)
        pestana = _pestana_organizar(raiz)
        pestana.ruta.establecer(str(carpeta))

        pestana._organizar()
        raiz.update()

        assert (carpeta / "Documentos" / "doc.pdf").exists()
        assert "Fechas: " not in _texto_registro(pestana.registro)

    def test_el_menu_de_categorias_exporta_e_importa(
        self, raiz: tk.Tk, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """El botón «Reglas...» abre un menú con las reglas y con exportar e importar las
        categorías.
        """
        pestana = _pestana_organizar(raiz)
        menu = pestana.menu_categorias
        assert pestana.boton_categorias._texto == "Reglas..."
        etiquetas = [
            menu.entrycget(i, "label") if menu.type(i) == "command" else "---" for i in range(4)
        ]
        assert etiquetas == [
            "Reglas de esta carpeta...",
            "---",
            "Exportar categorías...",
            "Importar categorías...",
        ]
        ruta = tmp_path / "categorias.json"
        monkeypatch.setattr(
            pestana_organizar.filedialog, "asksaveasfilename", lambda **k: str(ruta)
        )
        monkeypatch.setattr(pestana_organizar.filedialog, "askopenfilename", lambda **k: str(ruta))

        menu.invoke(2)
        assert ruta.exists()
        menu.invoke(3)
        assert pestana._config_categorias == str(ruta)

    def test_el_menu_se_abre_bajo_el_boton(self, raiz: tk.Tk, monkeypatch: pytest.MonkeyPatch):
        """El menú aparece justo debajo del botón, alineado a su derecha."""
        pestana = _pestana_organizar(raiz)
        _redimensionar(raiz, POR_DEFECTO)
        posiciones: list[tuple[int, int]] = []
        monkeypatch.setattr(
            pestana.menu_categorias, "tk_popup", lambda x, y: posiciones.append((x, y))
        )

        pestana._mostrar_menu_categorias()

        boton = pestana.boton_categorias
        x, y = posiciones[0]
        assert y == boton.winfo_rooty() + boton.winfo_height()
        assert (
            x + pestana.menu_categorias.winfo_reqwidth()
            == boton.winfo_rootx() + boton.winfo_width()
        )

    def test_el_vigilante_usa_la_agrupacion_y_las_categorias_importadas(
        self, raiz: tk.Tk, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """El vigilante recibe la forma de agrupar y las categorías importadas."""
        capturado: dict[str, object] = {}

        class VigilanteFalso:
            def __init__(self, **opciones: object) -> None:
                capturado.update(opciones)

            def iniciar(self) -> None:
                pass

            def detener(self) -> None:
                pass

        monkeypatch.setattr(pestana_organizar, "VigilanteCarpeta", VigilanteFalso)
        config = tmp_path / "fotos.json"
        config.write_text('{"Fotos": [".jpg"]}', encoding="utf-8")
        pestana = _pestana_organizar(raiz)
        pestana.ruta.establecer(str(tmp_path))
        pestana._config_categorias = str(config)
        pestana._var_agrupar.set(CriterioAgrupacion.TIPO_FECHA.value)

        pestana._iniciar_vigilante()
        pestana.detener_vigilancia()

        assert capturado["agrupar"] is CriterioAgrupacion.TIPO_FECHA
        assert capturado["ruta_config"] == str(config)


class TestDuplicados:
    """La casilla «Apartar duplicados» de la pestaña Organizar."""

    @staticmethod
    def _carpeta(base: Path, copias: int = 1) -> Path:
        """Una foto y ``copias`` copias suyas con otro nombre."""
        carpeta = base / "origen"
        carpeta.mkdir()
        (carpeta / "foto.jpg").write_bytes(b"la misma foto" * 10)
        for i in range(copias):
            (carpeta / f"copia {i:02d}.jpg").write_bytes(b"la misma foto" * 10)
        return carpeta

    def test_la_casilla_explica_lo_que_hace(self, raiz: tk.Tk):
        """Su ayuda dice adónde van y que nunca se borra nada; la del backup habla del zip."""
        pestana = _pestana_organizar(raiz)
        assert pestana.ayuda_duplicados.widget.cget("text") == "Apartar duplicados"
        assert "Duplicados/" in pestana.ayuda_duplicados.texto
        assert "Nunca se borra" in pestana.ayuda_duplicados.texto
        assert ".zip" in pestana.ayuda_backup.texto

    def test_sin_marcar_avisa_y_sugiere_apartarlos(
        self, raiz: tk.Tk, tmp_path: Path, sincrono: None
    ):
        """Sin marcar, se organizan como los demás, se listan y se sugiere marcar la casilla."""
        carpeta = self._carpeta(tmp_path)
        pestana = _pestana_organizar(raiz)
        pestana.ruta.establecer(str(carpeta))

        pestana._organizar()
        raiz.update()

        assert (carpeta / "Imagenes" / "copia 00.jpg").exists()
        texto = _texto_registro(pestana.registro)
        assert (
            "[DUPLICADOS] 1 con el mismo contenido que otro fichero, organizados como los demás:"
            in texto
        )
        assert "copia 00.jpg = Imagenes/foto.jpg" in texto
        assert "Marca «Apartar duplicados»" in texto

    def test_marcada_los_aparta_y_se_recuerda(self, raiz: tk.Tk, tmp_path: Path, sincrono: None):
        """Marcada, van a «Duplicados», no se sugiere nada y se guarda la elección."""
        carpeta = self._carpeta(tmp_path)
        pestana = _pestana_organizar(raiz)
        pestana.ruta.establecer(str(carpeta))
        pestana._var_duplicados.set(True)

        pestana._organizar()
        raiz.update()

        assert (carpeta / "Duplicados" / "copia 00.jpg").exists()
        texto = _texto_registro(pestana.registro)
        assert "apartados en Duplicados/:" in texto
        assert "Marca «Apartar duplicados»" not in texto
        assert ConfiguracionUsuario().cargar().obtener("mover_duplicados") is True

    def test_muchos_duplicados_se_resumen(self, raiz: tk.Tk, tmp_path: Path, sincrono: None):
        """Con muchos se enseñan los primeros y cuántos más hay."""
        carpeta = self._carpeta(tmp_path, copias=13)
        pestana = _pestana_organizar(raiz)
        pestana.ruta.establecer(str(carpeta))

        pestana._simular()
        raiz.update()

        texto = _texto_registro(pestana.registro)
        assert "[DUPLICADOS] 13 con el mismo contenido" in texto
        assert "... y 3 más" in texto

    def test_el_vigilante_usa_la_casilla(
        self, raiz: tk.Tk, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Con la casilla marcada, el vigilante también los aparta."""
        capturado: dict[str, object] = {}

        class VigilanteFalso:
            def __init__(self, **opciones: object) -> None:
                capturado.update(opciones)

            def iniciar(self) -> None:
                pass

            def detener(self) -> None:
                pass

        monkeypatch.setattr(pestana_organizar, "VigilanteCarpeta", VigilanteFalso)
        pestana = _pestana_organizar(raiz)
        pestana.ruta.establecer(str(tmp_path))
        pestana._var_duplicados.set(True)

        pestana._iniciar_vigilante()
        pestana.detener_vigilancia()

        assert capturado["duplicados"] is AccionDuplicados.MOVER


class TestBarraDeCifrado:
    """La barra de la pestaña Cifrado: por bloques con un fichero, por ficheros con una carpeta."""

    @pytest.fixture
    def cifrado(
        self, raiz: tk.Tk, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> PestanaCifrado:
        """La pestaña Cifrado con una contraseña escrita, operaciones en el hilo del test y cada
        avance de la barra anotado.
        """
        monkeypatch.setattr(pestana_cifrado, "threading", _HILOS_SINCRONOS)
        monkeypatch.setattr(pestana_cifrado.messagebox, "askyesno", lambda *a, **k: True)
        pestana = next(
            o for o in gc.get_objects() if isinstance(o, PestanaCifrado) and o.marco.tk is raiz.tk
        )
        pestana.campo_contrasena.establecer("una contraseña")
        self.apuntados: list[tuple[int, int]] = []
        original = pestana.progreso.apuntar

        def apuntar(actual: int, total: int) -> None:
            self.apuntados.append((actual, total))
            original(actual, total)

        monkeypatch.setattr(pestana.progreso, "apuntar", apuntar)
        return pestana

    def test_un_fichero_avanza_por_bloques(
        self, raiz: tk.Tk, cifrado: PestanaCifrado, tmp_path: Path
    ):
        """Un fichero de 200 KB avanza en cuatro pasos (bloques de 64 KB) y la barra acaba llena."""
        fichero = tmp_path / "grande.bin"
        fichero.write_bytes(b"x" * (200 * 1024))  # cuatro bloques de 64 KB
        cifrado.ruta.establecer(str(fichero))

        cifrado._cifrar()
        raiz.update()

        assert [actual for actual, _ in self.apuntados] == [65536, 131072, 196608, 204800]
        assert {total for _, total in self.apuntados} == {204800}
        assert float(cifrado.progreso.cget("value")) == 204800

    def test_una_carpeta_avanza_por_ficheros(
        self, raiz: tk.Tk, cifrado: PestanaCifrado, tmp_path: Path
    ):
        """Una carpeta avanza de fichero en fichero."""
        carpeta = _ficheros(tmp_path / "c", "a.txt", "b.txt", "c.txt")
        cifrado.ruta.establecer(str(carpeta))

        cifrado._cifrar()
        raiz.update()

        assert self.apuntados == [(1, 3), (2, 3), (3, 3)]
        assert float(cifrado.progreso.cget("value")) == 3

    def test_si_falla_la_barra_queda_vacia(
        self, raiz: tk.Tk, cifrado: PestanaCifrado, tmp_path: Path
    ):
        """Si descifrar falla, se muestra el error y la barra no se queda a medias."""
        fichero = tmp_path / "nota.txt"
        fichero.write_text("x")
        Cifrador.cifrar_archivo(fichero, contrasena="otra contraseña")
        cifrado.ruta.establecer(str(fichero.with_name("nota.txt.enc")))

        cifrado._descifrar()
        raiz.update()

        assert "[ERROR]" in _texto_registro(cifrado.registro)
        assert float(cifrado.progreso.cget("value")) == 0

    def test_usa_la_misma_barra_que_organizar(self, raiz: tk.Tk, cifrado: PestanaCifrado):
        """Es el mismo componente y el mismo estilo que la de Organizar."""
        assert isinstance(cifrado.progreso, BarraProgreso)
        assert cifrado.progreso.cget("style") == tema.ESTILO_PROGRESO


class TestBotonDeshabilitado:
    """Un botón deshabilitado tiene que parecerlo, también el verde (antes no cambiaba)."""

    @staticmethod
    def _colores(boton: BotonRedondeado) -> tuple[str, str]:
        """(fondo de la imagen que se ve ahora, color del texto)."""
        imagen = boton.itemcget(boton._id_forma, "image")
        fondo = next(clave[2] for clave, img in boton._imagenes.items() if str(img) == imagen)
        return fondo, boton.itemcget(boton._id_texto, "fill")

    def test_el_verde_se_apaga_al_deshabilitarlo(self, raiz: tk.Tk):
        """El botón verde cambia a sus colores de deshabilitado, fondo y texto."""
        boton = BotonRedondeado(raiz, texto="Organizar", variante="acento")
        boton.pack()
        raiz.update()
        fondo_activo, texto_activo = self._colores(boton)

        boton.establecer_habilitado(False)
        raiz.update()
        fondo, texto = self._colores(boton)

        assert fondo_activo == tema.VERDE and fondo != tema.VERDE
        assert texto != texto_activo
        colores = BotonRedondeado._VARIANTES["acento"]
        assert (fondo, texto) == (colores["fondo_deshabilitado"], colores["texto_deshabilitado"])

    def test_pasar_el_raton_no_lo_resalta(self, raiz: tk.Tk):
        """Con el ratón encima, un botón deshabilitado no se ilumina."""
        boton = BotonRedondeado(raiz, texto="Cifrar", variante="acento")
        boton.pack()
        boton.establecer_habilitado(False)
        raiz.update()
        boton._resaltado = True
        boton._redibujar()
        assert (
            self._colores(boton)[0] == BotonRedondeado._VARIANTES["acento"]["fondo_deshabilitado"]
        )

    @pytest.mark.parametrize("variante", ["secundario", "peligro"])
    def test_los_demas_atenuan_el_texto(self, raiz: tk.Tk, variante: str):
        """Los botones secundario y de peligro atenúan su texto."""
        boton = BotonRedondeado(raiz, texto="Deshacer", variante=variante)
        boton.pack()
        boton.establecer_habilitado(False)
        raiz.update()
        assert self._colores(boton)[1] == tema.TEXTO_DESHABILITADO


class TestDialogoReglas:
    """La ventana de reglas de la carpeta, que se abre desde «Reglas...» en Organizar."""

    @staticmethod
    def _abrir(raiz: tk.Tk, carpeta: Path) -> DialogoReglas:
        """Abre la ventana de reglas de ``carpeta`` desde la pestaña Organizar."""
        pestana = _pestana_organizar(raiz)
        pestana.ruta.establecer(str(carpeta))
        pestana._abrir_reglas()
        raiz.update()
        return pestana.dialogo_reglas

    def test_sin_carpeta_avisa(self, raiz: tk.Tk):
        """Sin carpeta elegida, avisa en el registro en vez de abrirse."""
        pestana = _pestana_organizar(raiz)
        pestana.ruta.establecer("")
        pestana._abrir_reglas()
        assert "Selecciona una carpeta para ver sus reglas" in _texto_registro(pestana.registro)

    def test_anadir_reordenar_y_quitar(self, raiz: tk.Tk, tmp_path: Path):
        """Lista vacía con un aviso; las reglas añadidas salen numeradas y alineadas; se pueden
        subir y quitar, y todo se guarda.
        """
        dialogo = self._abrir(raiz, tmp_path)
        assert "aún no tiene reglas" in dialogo.lista.get(0)
        for patron, carpeta in (("*.pdf", "Documentos"), ("factura*", "Facturas/{año}")):
            dialogo.patron.establecer(patron)
            dialogo.carpeta.establecer(carpeta)
            dialogo._agregar()
        assert dialogo.lista.get(0, "end") == (
            " 1. *.pdf     →  Documentos",
            " 2. factura*  →  Facturas/{año}",
        )
        assert dialogo.patron.obtener() == ""  # listo para la siguiente

        dialogo._mover(-1)  # sube la seleccionada, la última añadida
        assert ReglasCarpeta(tmp_path).cargar()[0] == Regla("factura*", "Facturas/{año}")
        dialogo._quitar()
        assert ReglasCarpeta(tmp_path).cargar() == [Regla("*.pdf", "Documentos")]
        dialogo.cerrar()

    def test_una_regla_que_no_vale_explica_el_motivo(self, raiz: tk.Tk, tmp_path: Path):
        """Una carpeta fuera del destino se explica en la ventana y no se guarda nada."""
        dialogo = self._abrir(raiz, tmp_path)
        dialogo.patron.establecer("*.pdf")
        dialogo.carpeta.establecer("C:/Contabilidad")
        dialogo._agregar()
        assert "dentro del destino" in str(dialogo.mensaje.cget("text"))
        assert not (tmp_path / NOMBRE_ARCHIVO_REGLAS).exists()
        dialogo.cerrar()

    def test_quitar_sin_elegir_una(self, raiz: tk.Tk, tmp_path: Path):
        """Quitar sin seleccionar ninguna pide elegir una primero."""
        ReglasCarpeta(tmp_path).agregar(Regla.crear("*.pdf", "Documentos"))
        dialogo = self._abrir(raiz, tmp_path)
        dialogo._quitar()
        assert "Elige primero una regla" in str(dialogo.mensaje.cget("text"))
        assert len(ReglasCarpeta(tmp_path).cargar()) == 1
        dialogo.cerrar()

    def test_probar_un_nombre(self, raiz: tk.Tk, tmp_path: Path):
        """Probar un nombre dice qué regla se aplicaría y adónde iría, o que va como siempre."""
        reglas = ReglasCarpeta(tmp_path)
        reglas.agregar(Regla.crear("factura*", "Facturas"))
        dialogo = self._abrir(raiz, tmp_path)

        dialogo.prueba.establecer("Factura_luz.PDF")
        dialogo._probar()
        assert dialogo.resultado_prueba.cget("text") == "Va a Facturas/ (regla 1: factura*)"
        dialogo.prueba.establecer("foto.jpg")
        dialogo._probar()
        assert "se organiza como siempre" in str(dialogo.resultado_prueba.cget("text"))
        dialogo.cerrar()

    def test_probar_un_nombre_agrupando_por_fecha(self, raiz: tk.Tk, tmp_path: Path):
        """Agrupando por fecha, la prueba muestra la fecha debajo de la carpeta, salvo en una regla
        que ya lleva su propia fecha.
        """
        reglas = ReglasCarpeta(tmp_path)
        reglas.agregar(Regla.crear("*.pdf", "Documentos"))
        reglas.agregar(Regla.crear("*.jpg", "Fotos/{año}"))
        _pestana_organizar(raiz)._var_agrupar.set(CriterioAgrupacion.FECHA.value)
        dialogo = self._abrir(raiz, tmp_path)

        dialogo.prueba.establecer("informe.pdf")
        dialogo._probar()
        assert dialogo.resultado_prueba.cget("text") == "Va a Documentos/AAAA/MM/ (regla 1: *.pdf)"
        dialogo.prueba.establecer("foto.jpg")
        dialogo._probar()
        assert dialogo.resultado_prueba.cget("text") == "Va a Fotos/{año}/ (regla 2: *.jpg)"
        dialogo.cerrar()

    def test_reglas_rotas_no_abren_la_ventana(
        self, raiz: tk.Tk, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Con el fichero de reglas roto, se explica en un mensaje y la ventana no se abre."""
        (tmp_path / NOMBRE_ARCHIVO_REGLAS).write_text("{roto", encoding="utf-8")
        errores: list[str] = []
        monkeypatch.setattr(
            pestana_organizar.messagebox, "showerror", lambda t, m: errores.append(m)
        )
        pestana = _pestana_organizar(raiz)
        pestana.ruta.establecer(str(tmp_path))
        pestana._abrir_reglas()
        assert errores and "no son un JSON válido" in errores[0]
        assert not hasattr(pestana, "dialogo_reglas")

    def test_cabe_en_pantallas_pequenas(self, raiz: tk.Tk, tmp_path: Path):
        """Se abre con un tamaño que cabe en una pantalla de 1920x1080 al 150 %."""
        dialogo = self._abrir(raiz, tmp_path)
        raiz.update()
        # 1920x1080 al 150 %: 720 px de alto, menos la barra de tareas y la de título.
        assert dialogo.ventana.winfo_height() <= 640
        assert dialogo.ventana.winfo_width() <= 760
        dialogo.cerrar()

    @staticmethod
    def _tras_redimensionar(raiz: tk.Tk, dialogo: DialogoReglas, geometria: str) -> None:
        """Cambia el tamaño de la ventana de reglas y deja que se recoloque."""
        dialogo.ventana.geometry(geometria)
        for _ in range(3):
            raiz.update()

    @staticmethod
    def _cerrar_se_ve(dialogo: DialogoReglas) -> bool:
        """Si el botón «Cerrar» se ve entero dentro de la ventana."""
        ventana = dialogo.ventana
        boton = ventana.winfo_children()[0].winfo_children()[-1].winfo_children()[0]
        fondo = boton.winfo_rooty() + boton.winfo_height() - ventana.winfo_rooty()
        return fondo <= ventana.winfo_height()

    def test_no_se_puede_hacer_mas_pequena_que_al_abrirse(self, raiz: tk.Tk, tmp_path: Path):
        """Antes se podía reducir a 640x400 y el botón «Cerrar» quedaba cortado."""
        dialogo = self._abrir(raiz, tmp_path)
        ancho, alto = dialogo.ventana.winfo_width(), dialogo.ventana.winfo_height()
        assert dialogo.ventana.minsize() == (ancho, alto)

        self._tras_redimensionar(raiz, dialogo, "500x300")
        assert dialogo.ventana.winfo_width() >= ancho
        assert dialogo.ventana.winfo_height() >= alto
        assert self._cerrar_se_ve(dialogo)
        dialogo.cerrar()

    def test_se_puede_ensanchar_hasta_un_maximo_y_el_texto_se_adapta(
        self, raiz: tk.Tk, tmp_path: Path
    ):
        """No pasa del ancho máximo, y los textos largos se reparten al ancho que tenga."""
        dialogo = self._abrir(raiz, tmp_path)
        assert dialogo.ventana.maxsize()[0] == dialogo_reglas.ANCHO_MAXIMO

        self._tras_redimensionar(raiz, dialogo, "1400x800")
        assert dialogo.ventana.winfo_width() <= dialogo_reglas.ANCHO_MAXIMO
        self._tras_redimensionar(raiz, dialogo, "900x800")
        assert dialogo.explicacion.cget("wraplength") == 900 - 2 * dialogo_reglas.MARGEN
        assert dialogo.mensaje.cget("wraplength") == 900 - 2 * dialogo_reglas.MARGEN
        assert self._cerrar_se_ve(dialogo)
        dialogo.cerrar()

    def test_la_barra_de_la_lista_solo_sale_si_no_caben(self, raiz: tk.Tk, tmp_path: Path):
        """La barra de desplazamiento aparece con muchas reglas y se va si la ventana se alarga y ya
        caben.
        """
        reglas = ReglasCarpeta(tmp_path)
        reglas.agregar(Regla.crear("*.pdf", "Documentos"))
        dialogo = self._abrir(raiz, tmp_path)
        assert not dialogo.barra_lista.winfo_ismapped()

        for i in range(10):
            dialogo.patron.establecer(f"p{i}*")
            dialogo.carpeta.establecer(f"C{i}")
            dialogo._agregar()
        raiz.update()
        assert dialogo.barra_lista.winfo_ismapped()

        self._tras_redimensionar(raiz, dialogo, "640x1000")  # más alta: ahora caben todas
        _saltar_si_la_pantalla_no_deja(dialogo.ventana, "640x1000")
        assert not dialogo.barra_lista.winfo_ismapped()
        dialogo.cerrar()

    def test_organizar_dice_que_reglas_se_aplicaron(
        self, raiz: tk.Tk, tmp_path: Path, sincrono: None
    ):
        """Al organizar, el registro dice qué reglas se aplicaron y a cuántos ficheros."""
        carpeta = _ficheros(tmp_path / "origen", "factura.pdf", "foto.jpg")
        ReglasCarpeta(carpeta).agregar(Regla.crear("factura*", "Facturas"))
        pestana = _pestana_organizar(raiz)
        pestana.ruta.establecer(str(carpeta))

        pestana._organizar()
        raiz.update()

        assert (carpeta / "Facturas" / "factura.pdf").exists()
        texto = _texto_registro(pestana.registro)
        assert "Reglas de la carpeta aplicadas a 1 fichero(s): «factura*» 1." in texto


class TestRutasEscritasAMano:
    """Las rutas de los campos se preparan y comprueban igual que en la consola (rutas.py)."""

    def test_la_carpeta_entre_comillas_se_organiza(
        self, raiz: tk.Tk, tmp_path: Path, sincrono: None
    ):
        """Pegada con Ctrl+V tal como la copia Windows, con comillas, se usa sin ellas."""
        carpeta = _ficheros(tmp_path / "Mis descargas", "a.jpg")
        pestana = _pestana_organizar(raiz)
        pestana.ruta.establecer(f'"{carpeta}"')

        pestana._organizar()
        raiz.update()

        assert (carpeta / "Imagenes" / "a.jpg").exists()

    def test_una_carpeta_que_no_existe_avisa_sin_empezar(self, raiz: tk.Tk, tmp_path: Path):
        """Se dice antes de empezar, con el mismo motivo que en la consola."""
        pestana = _pestana_organizar(raiz)
        pestana.ruta.establecer(str(tmp_path / "no_existe"))

        pestana._organizar()

        assert "[ERROR] La carpeta no existe" in _texto_registro(pestana.registro)
        assert _habilitado(pestana.boton_organizar)

    def test_un_destino_dentro_de_un_fichero_da_error(
        self, raiz: tk.Tk, tmp_path: Path, sincrono: None
    ):
        """Antes solo se miraba el destino; ahora también las carpetas por encima, como en la
        consola."""
        origen = _ficheros(tmp_path / "origen", "a.jpg")
        fichero = tmp_path / "nota.txt"
        fichero.write_text("x")
        pestana = _pestana_organizar(raiz)
        pestana.ruta.establecer(str(origen))
        pestana._var_usar_destino.set(True)
        pestana.destino.establecer(str(fichero / "Ordenado"))

        pestana._organizar()
        raiz.update()

        assert f"«{fichero}» es un fichero, no una carpeta" in _texto_registro(pestana.registro)
        assert (origen / "a.jpg").exists()

    def test_el_historial_lee_la_ruta_entre_comillas(
        self, raiz: tk.Tk, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """La pestaña Historial prepara la ruta igual que la de Organizar."""
        monkeypatch.setattr(pestana_historial, "threading", _HILOS_SINCRONOS)
        carpeta = _ficheros(tmp_path / "carpeta", "lunes.pdf")
        OrganizadorArchivos(carpeta_origen=carpeta).ejecutar()
        historial = _pestana_historial(raiz)
        historial.ruta.establecer(f"  '{carpeta}' ")

        historial._cargar_historial()
        raiz.update()

        assert "lunes.pdf" in _texto_registro(historial.registro)

    def test_cifrar_con_comillas_y_una_clave_en_la_carpeta_personal(
        self, raiz: tk.Tk, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """El fichero entre comillas y la clave con ``~`` se encuentran."""
        monkeypatch.setattr(pestana_cifrado, "threading", _HILOS_SINCRONOS)
        casa = tmp_path / "casa"
        casa.mkdir()
        monkeypatch.setenv("HOME", str(casa))
        monkeypatch.setenv("USERPROFILE", str(casa))
        Cifrador.generar_clave(casa / "mia.key")
        nota = _ficheros(tmp_path / "docs", "nota.txt") / "nota.txt"
        pestana = next(
            o for o in gc.get_objects() if isinstance(o, PestanaCifrado) and o.marco.tk is raiz.tk
        )
        pestana._metodo.set("archivo_clave")
        pestana._actualizar_metodo()
        pestana.campo_archivo_clave.establecer("~/mia.key")
        pestana.ruta.establecer(f'"{nota}"')

        pestana._cifrar()
        raiz.update()

        assert nota.with_name("nota.txt.enc").exists()

    def test_pegar_quita_las_comillas_y_los_espacios(self, raiz: tk.Tk, tmp_path: Path):
        """El botón «Pegar» limpia la ruta como el resto: también comillas simples."""
        pestana = _pestana_organizar(raiz)
        raiz.clipboard_clear()
        raiz.clipboard_append(f" '{tmp_path}' ")

        pestana._pegar_en(pestana.ruta)

        assert pestana.ruta.obtener() == str(tmp_path)
