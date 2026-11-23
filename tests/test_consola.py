"""Tests de la consola sin teclado (Ctrl+D, entrada redirigida) y de la barra de progreso
cuando se escriben mensajes del log mientras avanza."""

import io
import json
import logging
import re
import sys
import threading
import time

import pytest

from file_organizer.cli import aplicacion, comandos
from file_organizer.cli import argumentos as modulo_argumentos
from file_organizer.cli.salida import barra_progreso_consola, confirmar, notificador_vigilante
from file_organizer.mover_archivos import NOMBRE_ARCHIVO_HISTORIAL
from file_organizer.organizador import OrganizadorArchivos
from file_organizer.vigilante import VigilanteCarpeta


@pytest.fixture(autouse=True)
def _aislar(tmp_path, monkeypatch):
    """Cada test en un directorio temporal, con su configuración y el logging restaurado."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FILE_ORGANIZER_CONFIG", str(tmp_path / "uc.json"))
    raiz = logging.getLogger()
    manejadores_previos, nivel_previo = raiz.handlers[:], raiz.level
    yield
    for h in raiz.handlers[:]:
        h.close()
        raiz.removeHandler(h)
    for h in manejadores_previos:
        raiz.addHandler(h)
    raiz.setLevel(nivel_previo)


def _sin_teclado(monkeypatch: pytest.MonkeyPatch) -> None:
    """Como ``comando < /dev/null``: la entrada está vacía desde el principio."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))


def _sin_colores(texto: str) -> str:
    """Quita los códigos de color de la salida de la consola."""
    return re.sub(r"\x1b\[[0-9;]*m", "", texto)


def _pantalla(texto: str) -> list[str]:
    """Lo que queda en un terminal tras escribir el texto: cada ``\\r`` vuelve al principio de
    la línea y lo siguiente sobrescribe lo anterior."""
    lineas: list[str] = []
    linea: list[str] = []
    columna = 0
    for c in _sin_colores(texto):
        if c == "\r":
            columna = 0
        elif c == "\n":
            lineas.append("".join(linea).rstrip())
            linea, columna = [], 0
        else:
            if columna < len(linea):
                linea[columna] = c
            else:
                linea.append(c)
            columna += 1
    if linea:
        lineas.append("".join(linea).rstrip())
    return lineas


class TestSinTeclado:
    """Sin nadie que responda (Ctrl+D o la entrada redirigida), la consola no se cuelga ni hace nada
    peligroso.
    """

    @pytest.mark.parametrize(
        ("respuesta", "esperado"), [("s\n", True), (" S \n", True), ("n\n", False)]
    )
    def test_confirmar_lee_la_respuesta(self, monkeypatch, respuesta, esperado):
        """«s» y «S» con espacios es que sí; «n», que no."""
        monkeypatch.setattr(sys, "stdin", io.StringIO(respuesta))
        assert confirmar("¿Seguro? ") is esperado

    def test_confirmar_sin_respuesta_cuenta_como_no(self, monkeypatch):
        """Sin respuesta, la confirmación es no."""
        _sin_teclado(monkeypatch)
        assert confirmar("¿Seguro? ") is False

    def test_limpiar_historial_sin_teclado_cancela_y_no_borra_nada(
        self, tmp_path, monkeypatch, capsys
    ):
        """``--limpiar-historial`` sin poder confirmar se cancela y el historial queda igual."""
        carpeta = tmp_path / "descargas"
        carpeta.mkdir()
        (carpeta / "a.pdf").write_text("x")
        OrganizadorArchivos(carpeta_origen=carpeta).ejecutar()
        historial = carpeta / NOMBRE_ARCHIVO_HISTORIAL
        antes = json.loads(historial.read_text(encoding="utf-8"))
        _sin_teclado(monkeypatch)
        monkeypatch.setattr(
            sys, "argv", ["file-organizer", "--carpeta", str(carpeta), "--limpiar-historial"]
        )

        assert aplicacion.main() == 0

        assert "Operación cancelada" in capsys.readouterr().out
        assert json.loads(historial.read_text(encoding="utf-8")) == antes

    def test_cifrar_varios_ficheros_sin_teclado_cancela(self, monkeypatch):
        """Cifrar varios ficheros a la vez sin poder confirmar se cancela."""
        _sin_teclado(monkeypatch)
        assert comandos._confirmar_operacion_masiva(2, "cifrar") is False

    def test_el_menu_sin_teclado_sale_sin_error(self, monkeypatch, capsys):
        """El menú, sin entrada, sale con normalidad y no da error."""
        _sin_teclado(monkeypatch)
        monkeypatch.setattr(sys, "argv", ["file-organizer"])

        assert aplicacion.main() == 0

        assert "Saliendo del programa" in capsys.readouterr().out

    def test_el_menu_sale_cuando_se_acaba_un_fichero_de_respuestas(self, monkeypatch, capsys):
        """Con las respuestas en un fichero, al acabarse sale en vez de repetir la pregunta."""
        monkeypatch.setattr(sys, "stdin", io.StringIO("99\n\n"))  # opción no válida, ENTER y fin
        monkeypatch.setattr(sys, "argv", ["file-organizer"])

        assert aplicacion.main() == 0

        salida = capsys.readouterr().out
        assert "Opción no válida" in salida and "Saliendo del programa" in salida


class TestBarraDeProgreso:
    """La barra de progreso de la consola junto a los mensajes del log."""

    def test_los_mensajes_del_log_no_se_pegan_a_la_barra(self, monkeypatch, capsys):
        """Cada mensaje sale en su línea y la barra se vuelve a dibujar debajo; al final queda
        completa.
        """
        # Salida y errores en el mismo sitio y en orden, como en un terminal.
        monkeypatch.setattr(sys, "stderr", sys.stdout)
        modulo_argumentos.configurar_registro()
        log = logging.getLogger("file_organizer.prueba")

        log.info("Movido: a.pdf")
        barra_progreso_consola(1, 3, ancho=10)
        log.info("Movido: b.jpg")
        barra_progreso_consola(2, 3, ancho=10)
        log.info("Movido: c.mp3")
        barra_progreso_consola(3, 3, ancho=10)
        log.info("Proceso completado")

        assert _pantalla(capsys.readouterr().out) == [
            "Movido: a.pdf",
            "Movido: b.jpg",
            "Movido: c.mp3",
            " [##########] 3/3 (100.0%)",
            "Proceso completado",
        ]

    def test_organizar_desde_la_consola_deja_la_barra_al_final(self, tmp_path, monkeypatch, capsys):
        """Al organizar, la barra completa queda justo debajo del último «Movido»."""
        monkeypatch.setattr(sys, "stderr", sys.stdout)
        carpeta = tmp_path / "descargas"
        carpeta.mkdir()
        for nombre in ("a.pdf", "b.jpg", "c.mp3"):
            (carpeta / nombre).write_text("x")
        monkeypatch.setattr(sys, "argv", ["file-organizer", "--carpeta", str(carpeta)])

        assert aplicacion.main() == 0

        pantalla = _pantalla(capsys.readouterr().out)
        movidos = [i for i, linea in enumerate(pantalla) if linea.startswith("Movido:")]
        barra = pantalla.index(next(linea for linea in pantalla if "3/3 (100.0%)" in linea))
        assert len(movidos) == 3
        assert all(linea.startswith("Movido:") for linea in pantalla[movidos[0] : barra])
        assert barra == movidos[-1] + 1  # la barra, completa, justo debajo del último


class TestVigilarSinRepetir:
    """Con ``--vigilar``, cada aviso del vigilante sale una sola vez."""

    def test_cada_aviso_del_vigilante_sale_una_vez_y_todo_queda_en_el_fichero(
        self, tmp_path, monkeypatch, capsys
    ):
        """Antes, con --vigilar, cada aviso salía dos veces: en color (notificador) y desde
        el log; y cada movimiento, una tercera («Movido: ...»)."""
        monkeypatch.setattr(sys, "stderr", sys.stdout)  # como en un terminal
        modulo_argumentos.configurar_registro()
        carpeta = tmp_path / "v"
        carpeta.mkdir()
        vigilante = VigilanteCarpeta(carpeta=carpeta, espera=0.1, notificador=notificador_vigilante)
        hilo = threading.Thread(target=vigilante.iniciar, daemon=True)
        hilo.start()
        limite = time.monotonic() + 10
        while not vigilante.observador.is_alive() and time.monotonic() < limite:
            time.sleep(0.02)
        (carpeta / "nuevo.jpg").write_text("x")
        while not (carpeta / "Imagenes" / "nuevo.jpg").exists() and time.monotonic() < limite:
            time.sleep(0.05)
        vigilante.detener()
        hilo.join(timeout=10)

        lineas = [linea.strip() for linea in _sin_colores(capsys.readouterr().out).splitlines()]
        for aviso in (
            "[VIGILANTE] Vigilando: ",
            "[VIGILANTE] Nuevo fichero detectado: nuevo.jpg",
            "[VIGILANTE] Organizando: nuevo.jpg",
            "[VIGILANTE] nuevo.jpg -> Imagenes/",
            "[VIGILANTE] Vigilancia detenida",
        ):
            assert sum(linea.startswith(aviso) for linea in lineas) == 1, aviso
        assert not any(linea.startswith("Movido:") for linea in lineas)

        log = (tmp_path / modulo_argumentos.NOMBRE_ARCHIVO_LOG).read_text(encoding="utf-8")
        for linea in (
            "[VIGILANTE] Nuevo fichero detectado: nuevo.jpg",
            "[VIGILANTE] Organizando: nuevo.jpg",
            "Movido: nuevo.jpg -> ",
            "[VIGILANTE] Movido: nuevo.jpg -> Imagenes/",
            "[VIGILANTE] Vigilancia detenida",
        ):
            assert linea in log
