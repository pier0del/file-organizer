"""Tests del bloqueo de carpeta: dos operaciones no pueden escribir a la vez el mismo historial.

Varios tests usan un segundo proceso de Python que bloquea la carpeta (o la vigila de verdad),
como haría otra ventana del programa, y lo matan de golpe para comprobar que el sistema suelta
el bloqueo.
"""

import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from file_organizer.bloqueo import NOMBRE_ARCHIVO_BLOQUEO, BloqueoCarpeta
from file_organizer.cli import aplicacion
from file_organizer.excepciones import ErrorCarpetaEnUso
from file_organizer.mover_archivos import NOMBRE_ARCHIVO_HISTORIAL
from file_organizer.organizador import OrganizadorArchivos
from file_organizer.vigilante import VigilanteCarpeta

RAIZ_SRC = str(Path(__file__).resolve().parents[1] / "src")

# Programa del otro proceso: bloquea la carpeta (o la vigila) y avisa cuando está listo.
_BLOQUEAR = """
import sys, time
sys.path.insert(0, {src!r})
from file_organizer.bloqueo import BloqueoCarpeta
bloqueo = BloqueoCarpeta({carpeta!r})
bloqueo.adquirir()
print("listo", flush=True)
time.sleep(120)
"""
# Vigila de verdad y se detiene como con el botón «Detener» cuando se cierra su entrada.
_VIGILAR = """
import sys, threading
sys.path.insert(0, {src!r})
from pathlib import Path
from file_organizer.vigilante import VigilanteCarpeta
def avisar(nivel, mensaje):
    if mensaje.startswith("Vigilando"):
        print("listo", flush=True)
vigilante = VigilanteCarpeta(Path({carpeta!r}), espera=0.3, notificador=avisar)
def detener_al_cerrar_la_entrada():
    sys.stdin.read()
    vigilante.detener()
threading.Thread(target=detener_al_cerrar_la_entrada, daemon=True).start()
vigilante.iniciar()
"""


def _otro_proceso(programa: str, carpeta: Path) -> subprocess.Popen[str]:
    """Arranca ``programa`` en otro proceso de Python sobre ``carpeta`` y espera a que avise de que
    está listo.
    """
    proceso = subprocess.Popen(
        [sys.executable, "-c", programa.format(src=RAIZ_SRC, carpeta=str(carpeta))],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert proceso.stdout is not None
    assert proceso.stdout.readline().strip() == "listo"
    return proceso


@pytest.fixture
def carpeta_organizada(tmp_path: Path) -> Path:
    """Una carpeta con una foto y un PDF ya organizados, así que tiene historial."""
    carpeta = tmp_path / "descargas"
    carpeta.mkdir()
    for nombre in ("a.jpg", "b.pdf"):
        (carpeta / nombre).write_text("x")
    OrganizadorArchivos(carpeta_origen=carpeta).ejecutar()
    return carpeta


@pytest.fixture
def bloqueada_por_otro_proceso(carpeta_organizada: Path) -> Iterator[subprocess.Popen[str]]:
    """Otro proceso que tiene bloqueada la carpeta organizada; al acabar se mata."""
    proceso = _otro_proceso(_BLOQUEAR, carpeta_organizada)
    yield proceso
    proceso.kill()
    proceso.wait()


def _organizados(carpeta: Path) -> list[str]:
    """Nombres de los ficheros que hay dentro de las carpetas de categoría."""
    return sorted(p.name for p in carpeta.glob("*/*") if not p.name.startswith("."))


class TestBloqueoCarpeta:
    """El bloqueo por sí solo, dentro de un mismo proceso."""

    def test_dos_bloqueos_de_la_misma_carpeta_se_excluyen(self, tmp_path: Path):
        """Mientras uno lo tiene, el segundo falla con «está en uso»; al soltarlo, ya se puede."""
        primero = BloqueoCarpeta(tmp_path)
        primero.adquirir()
        with pytest.raises(ErrorCarpetaEnUso, match="está en uso"):
            BloqueoCarpeta(tmp_path).adquirir()
        primero.liberar()
        with BloqueoCarpeta(tmp_path) as segundo:
            assert segundo.adquirido

    def test_carpetas_distintas_no_se_estorban(self, tmp_path: Path):
        """Bloquear una carpeta no impide bloquear otra a la vez."""
        with BloqueoCarpeta(tmp_path / "una"), BloqueoCarpeta(tmp_path / "otra") as otra:
            assert otra.adquirido

    def test_se_suelta_aunque_haya_un_error(self, tmp_path: Path):
        """Usado con ``with``, una excepción a mitad de la
        operación no deja la carpeta bloqueada.
        """
        with pytest.raises(RuntimeError), BloqueoCarpeta(tmp_path):
            raise RuntimeError("fallo a mitad de la operación")
        with BloqueoCarpeta(tmp_path) as bloqueo:
            assert bloqueo.adquirido

    def test_se_puede_soltar_desde_otro_hilo(self, tmp_path: Path):
        """La GUI empieza a vigilar en un hilo y lo detiene desde otro."""
        bloqueo = BloqueoCarpeta(tmp_path)
        bloqueo.adquirir()
        hilo = threading.Thread(target=bloqueo.liberar)
        hilo.start()
        hilo.join()

        with BloqueoCarpeta(tmp_path) as otro:  # sin volver a soltarlo en este hilo
            assert otro.adquirido

    def test_soltarlo_varias_veces_no_falla(self, tmp_path: Path):
        """Liberar un bloqueo ya liberado no lanza nada (p. ej. al detener dos veces)."""
        bloqueo = BloqueoCarpeta(tmp_path)
        bloqueo.adquirir()
        bloqueo.liberar()
        bloqueo.liberar()
        assert not bloqueo.adquirido

    def test_el_fichero_de_bloqueo_esta_oculto_y_no_se_organiza(self, tmp_path: Path):
        """El fichero ``.file-organizer.lock`` empieza por punto y no acaba en «Otros»."""
        (tmp_path / "a.jpg").write_text("x")
        OrganizadorArchivos(carpeta_origen=tmp_path).ejecutar()
        assert NOMBRE_ARCHIVO_BLOQUEO.startswith(".")
        assert not (tmp_path / "Otros").exists()


class TestConOtroProcesoUsandoLaCarpeta:
    """Otro proceso del programa (otra ventana o terminal) tiene la carpeta bloqueada."""

    def test_no_se_organiza(self, tmp_path: Path):
        """Organizar falla sin mover nada; simular sí funciona, porque no escribe."""
        carpeta = tmp_path / "descargas"
        carpeta.mkdir()
        (carpeta / "a.jpg").write_text("x")
        proceso = _otro_proceso(_BLOQUEAR, carpeta)
        try:
            with pytest.raises(ErrorCarpetaEnUso):
                OrganizadorArchivos(carpeta_origen=carpeta).ejecutar()
            assert (carpeta / "a.jpg").exists()
            # Simular no escribe nada, así que no necesita la carpeta.
            simulacion = OrganizadorArchivos(carpeta_origen=carpeta, modo_simulacion=True)
            assert simulacion.ejecutar() == 1
        finally:
            proceso.kill()
            proceso.wait()

    def test_no_se_deshace_ni_se_limpia_el_historial(
        self, carpeta_organizada: Path, bloqueada_por_otro_proceso: subprocess.Popen[str]
    ):
        """Deshacer y limpiar fallan con ErrorCarpetaEnUso y el historial sigue intacto."""
        with pytest.raises(ErrorCarpetaEnUso):
            OrganizadorArchivos(carpeta_origen=carpeta_organizada).deshacer()
        with pytest.raises(ErrorCarpetaEnUso):
            OrganizadorArchivos(carpeta_origen=carpeta_organizada).limpiar_historial()
        assert _organizados(carpeta_organizada) == ["a.jpg", "b.pdf"]
        assert (carpeta_organizada / NOMBRE_ARCHIVO_HISTORIAL).exists()

    def test_no_se_empieza_a_vigilar(
        self, carpeta_organizada: Path, bloqueada_por_otro_proceso: subprocess.Popen[str]
    ):
        """El vigilante falla al iniciar en lugar de quedarse vigilando sin poder guardar."""
        vigilante = VigilanteCarpeta(carpeta_organizada, notificador=lambda n, m: None)
        errores: list[BaseException] = []

        def iniciar() -> None:
            try:
                vigilante.iniciar()
            except BaseException as error:
                errores.append(error)

        # En un hilo aparte: si no fallara, se quedaría vigilando y el test no terminaría.
        hilo = threading.Thread(target=iniciar, daemon=True)
        hilo.start()
        hilo.join(timeout=5)
        if hilo.is_alive():
            vigilante.detener()
            pytest.fail("empezó a vigilar una carpeta que otro proceso estaba usando")
        assert len(errores) == 1 and isinstance(errores[0], ErrorCarpetaEnUso)
        assert not vigilante.observador.is_alive()

    def test_si_ese_proceso_muere_de_golpe_la_carpeta_queda_libre(
        self, carpeta_organizada: Path, bloqueada_por_otro_proceso: subprocess.Popen[str]
    ):
        """El sistema suelta el bloqueo de un proceso que muere sin cerrar nada: no hace falta
        borrar el fichero a mano.
        """
        bloqueada_por_otro_proceso.kill()  # sin cerrar nada: como un cuelgue o un corte de luz
        bloqueada_por_otro_proceso.wait()

        resultado = OrganizadorArchivos(carpeta_origen=carpeta_organizada).deshacer()

        assert resultado is not None and resultado.exitosos == 2


class TestDeshacerDesdeLaTerminalMientrasOtraVentanaVigila:
    """El caso que motivó el bloqueo: la GUI vigila y en una terminal se pide deshacer."""

    def test_la_terminal_avisa_y_no_toca_nada_hasta_que_se_deja_de_vigilar(
        self,
        carpeta_organizada: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ):
        """La terminal dice que la carpeta está en uso y no mueve nada; la vigilancia sigue
        organizando. Al detenerla, se deshace paso a paso: primero lo del vigilante.
        """
        monkeypatch.chdir(carpeta_organizada.parent)
        monkeypatch.setenv("FILE_ORGANIZER_CONFIG", str(carpeta_organizada.parent / "uc.json"))
        argumentos = ["file-organizer", "--carpeta", str(carpeta_organizada), "--deshacer"]
        monkeypatch.setattr(sys, "argv", argumentos)
        vigilancia = _otro_proceso(_VIGILAR, carpeta_organizada)
        try:
            assert aplicacion.main() == 1
            assert "está en uso" in capsys.readouterr().out
            assert _organizados(carpeta_organizada) == ["a.jpg", "b.pdf"]

            # Mientras tanto la vigilancia sigue funcionando con normalidad.
            (carpeta_organizada / "c.mp3").write_text("x")
            limite = time.monotonic() + 10
            while not (carpeta_organizada / "Musica" / "c.mp3").exists():
                assert time.monotonic() < limite, "la vigilancia dejó de organizar"
                time.sleep(0.05)
        finally:
            assert vigilancia.stdin is not None
            vigilancia.stdin.close()  # «Detener»: guarda el historial y suelta la carpeta
            try:
                vigilancia.wait(timeout=15)
            except subprocess.TimeoutExpired:
                vigilancia.kill()
                raise

        # Ya sin vigilancia se puede deshacer, paso a paso: primero lo que organizó el
        # vigilante (c.mp3) y después la organización anterior.
        assert aplicacion.main() == 0
        assert _organizados(carpeta_organizada) == ["a.jpg", "b.pdf"]
        assert aplicacion.main() == 0
        assert not _organizados(carpeta_organizada)


class TestElVigilanteSueltaLaCarpeta:
    """El vigilante tiene la carpeta mientras vigila y la suelta al acabar, falle o no."""

    def test_mientras_vigila_tampoco_se_puede_deshacer_desde_el_mismo_proceso(
        self, carpeta_organizada: Path
    ):
        """El bloqueo vale también dentro del mismo proceso (la GUI vigilando y deshaciendo); al
        detener, ya se puede deshacer todo.
        """
        vigilante = VigilanteCarpeta(carpeta_organizada, espera=0.3, notificador=lambda n, m: None)
        hilo = threading.Thread(target=vigilante.iniciar, daemon=True)
        hilo.start()
        limite = time.monotonic() + 10
        while not vigilante.observador.is_alive():
            assert time.monotonic() < limite
            time.sleep(0.05)

        with pytest.raises(ErrorCarpetaEnUso):
            OrganizadorArchivos(carpeta_origen=carpeta_organizada).deshacer()

        vigilante.detener()  # al volver, el historial está guardado y la carpeta libre
        hilo.join(timeout=10)
        resultado = OrganizadorArchivos(carpeta_origen=carpeta_organizada).deshacer()
        assert resultado is not None and resultado.exitosos == 2

    def test_si_falla_al_arrancar_suelta_la_carpeta(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Si el observador no puede empezar (la carpeta desapareció), el bloqueo se suelta."""
        vigilante = VigilanteCarpeta(tmp_path, notificador=lambda n, m: None)

        def falla(*args, **kwargs):
            raise OSError("la carpeta ya no existe")

        monkeypatch.setattr(vigilante.observador, "schedule", falla)
        with pytest.raises(OSError):
            vigilante.iniciar()
        assert not vigilante.organizador.bloqueo.adquirido

    def test_detener_antes_de_que_arranque_no_deja_nada_en_marcha(self, tmp_path: Path):
        """Detener antes de iniciar hace que iniciar vuelva enseguida, sin vigilar ni bloquear la
        carpeta.
        """
        avisos: list[str] = []
        vigilante = VigilanteCarpeta(tmp_path, notificador=lambda n, m: avisos.append(m))
        vigilante.detener()  # p. ej. la GUI se cierra justo al pulsar «Vigilar»

        vigilante.iniciar()  # vuelve enseguida

        assert not vigilante.observador.is_alive()
        assert not vigilante.organizador.bloqueo.adquirido
        assert not any(m.startswith("Vigilando") for m in avisos)
