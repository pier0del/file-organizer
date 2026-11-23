"""Tests de la configuración del log (argumentos.py): rotación del fichero y qué se ve en la
consola."""

import logging
import re
import stat
import sys
from logging.handlers import RotatingFileHandler

import pytest

from file_organizer.bloqueo import BloqueoCarpeta
from file_organizer.cli import aplicacion
from file_organizer.cli import argumentos as modulo_argumentos
from file_organizer.cli.salida import imprimir_excepcion, imprimir_exito
from file_organizer.cli.validadores import validar_carpeta
from file_organizer.excepciones import ErrorOrigenNoEncontrado
from file_organizer.organizador import OrganizadorArchivos


@pytest.fixture(autouse=True)
def _restaurar_logging(tmp_path, monkeypatch):
    """Ejecuta cada test en un directorio temporal con el logging aislado y lo restaura al terminar."""
    monkeypatch.chdir(tmp_path)
    raiz = logging.getLogger()
    manejadores_previos = raiz.handlers[:]
    nivel_previo = raiz.level

    # basicConfig() no hace nada si ya hay handlers: los quitamos para que se aplique.
    for h in raiz.handlers[:]:
        raiz.removeHandler(h)

    yield

    for h in raiz.handlers[:]:
        h.close()
        raiz.removeHandler(h)
    for h in manejadores_previos:
        raiz.addHandler(h)
    raiz.setLevel(nivel_previo)


def test_configura_un_manejador_de_fichero_rotativo():
    """El log va a un solo fichero que rota."""
    modulo_argumentos.configurar_registro()
    handlers = logging.getLogger().handlers
    rotativos = [h for h in handlers if isinstance(h, RotatingFileHandler)]
    assert len(rotativos) == 1


def test_parametros_de_rotacion():
    """Rota a 1 MB y guarda 3 copias."""
    modulo_argumentos.configurar_registro()
    rotativo = next(h for h in logging.getLogger().handlers if isinstance(h, RotatingFileHandler))
    assert rotativo.maxBytes == modulo_argumentos.TAMANO_MAXIMO_LOG
    assert rotativo.backupCount == modulo_argumentos.COPIAS_LOG


def test_detallado_activa_nivel_debug():
    """Con ``detallado`` se registra también lo de DEBUG."""
    modulo_argumentos.configurar_registro(detallado=True)
    assert logging.getLogger().level == logging.DEBUG


def test_crea_el_fichero_de_log(tmp_path):
    """El fichero se crea en el directorio de trabajo."""
    modulo_argumentos.configurar_registro()
    logging.getLogger().info("mensaje de prueba")
    assert (tmp_path / modulo_argumentos.NOMBRE_ARCHIVO_LOG).exists()


@pytest.mark.skipif(sys.platform == "win32", reason="permisos POSIX no aplican en windows")
def test_el_log_y_sus_copias_solo_los_puede_leer_su_dueno(tmp_path):
    """Lleva las rutas de los ficheros: el log y la copia rotada tienen permisos 0o600, y se sigue
    escribiendo tras rotar.
    """
    modulo_argumentos.configurar_registro()
    rotativo = next(h for h in logging.getLogger().handlers if isinstance(h, RotatingFileHandler))
    logging.getLogger().info("ruta de un fichero del usuario")
    rotativo.doRollover()
    logging.getLogger().info("otra")

    log = tmp_path / modulo_argumentos.NOMBRE_ARCHIVO_LOG
    for fichero in (log, log.with_name(log.name + ".1")):
        assert stat.S_IMODE(fichero.stat().st_mode) == 0o600
    assert "otra" in log.read_text(encoding="utf-8")


class TestMensajesSinRepetir:
    """Lo que ya se imprime en color no se repite en la consola a través del log, pero sigue
    quedando en el fichero de log."""

    @staticmethod
    def _fichero_log(tmp_path) -> str:
        """Lo que hay en el fichero de log."""
        return (tmp_path / modulo_argumentos.NOMBRE_ARCHIVO_LOG).read_text(encoding="utf-8")

    def test_un_error_se_ve_una_vez_y_queda_en_el_fichero(self, tmp_path, capsys):
        """Un error impreso en color no sale otra vez por el log;
        en el fichero queda con su tipo.
        """
        modulo_argumentos.configurar_registro()

        imprimir_excepcion(ErrorOrigenNoEncontrado("No existe la carpeta X"))

        salida = capsys.readouterr()
        assert salida.out.count("No existe la carpeta X") == 1
        assert salida.err == ""
        assert "ErrorOrigenNoEncontrado: No existe la carpeta X" in self._fichero_log(tmp_path)

    def test_un_exito_se_ve_una_vez_y_queda_en_el_fichero(self, tmp_path, capsys):
        """Un éxito impreso en color, igual."""
        modulo_argumentos.configurar_registro()

        imprimir_exito("Clave guardada")

        salida = capsys.readouterr()
        assert salida.out.count("Clave guardada") == 1
        assert salida.err == ""
        assert "Clave guardada" in self._fichero_log(tmp_path)

    def test_los_errores_de_los_validadores_se_ven_una_vez(self, tmp_path, capsys):
        """Los errores de validar lo que escribe el usuario, igual."""
        modulo_argumentos.configurar_registro()

        assert validar_carpeta(str(tmp_path / "no_existe")) is None

        salida = capsys.readouterr()
        assert salida.out.count("La carpeta no existe") == 1
        assert salida.err == ""
        assert "La carpeta no existe" in self._fichero_log(tmp_path)

    def test_el_resto_del_log_se_sigue_viendo_en_la_consola(self, tmp_path, capsys):
        """Un mensaje del log que no se imprime aparte sí sale en la consola."""
        modulo_argumentos.configurar_registro()

        logging.getLogger("file_organizer.prueba").info("Historial cargado")

        assert "Historial cargado" in capsys.readouterr().err
        assert "Historial cargado" in self._fichero_log(tmp_path)

    def test_carpeta_en_uso_con_deshacer_se_ve_una_vez(self, tmp_path, monkeypatch, capsys):
        """El caso real en que se vio el mensaje repetido."""
        monkeypatch.setenv("FILE_ORGANIZER_CONFIG", str(tmp_path / "uc.json"))
        carpeta = tmp_path / "descargas"
        carpeta.mkdir()
        (carpeta / "a.pdf").write_text("x")
        OrganizadorArchivos(carpeta_origen=carpeta).ejecutar()
        capsys.readouterr()
        monkeypatch.setattr(
            sys, "argv", ["file-organizer", "--carpeta", str(carpeta), "--deshacer"]
        )

        with BloqueoCarpeta(carpeta):  # como si otra ventana la estuviera vigilando
            assert aplicacion.main() == 1

        salida = capsys.readouterr()
        texto = re.sub(r"\x1b\[[0-9;]*m", "", salida.out + salida.err)  # sin colores
        assert texto.count("está en uso") == 1
        assert "ErrorCarpetaEnUso" in self._fichero_log(tmp_path)

    def test_el_resumen_de_deshacer_se_ve_una_vez(self, tmp_path, monkeypatch, capsys):
        """Antes salían dos veces el resumen («Restaurados: 1 | Fallidos: 0») y el aviso de
        cada fichero restaurado con otro nombre: una desde el log y otra en color."""
        monkeypatch.setenv("FILE_ORGANIZER_CONFIG", str(tmp_path / "uc.json"))
        carpeta = tmp_path / "descargas"
        carpeta.mkdir()
        (carpeta / "foto.jpg").write_text("A")
        OrganizadorArchivos(carpeta_origen=carpeta).ejecutar()
        (carpeta / "foto.jpg").write_text("otra")  # ocupa el sitio: se restaura como foto_1.jpg
        capsys.readouterr()
        monkeypatch.setattr(
            sys, "argv", ["file-organizer", "--carpeta", str(carpeta), "--deshacer"]
        )

        assert aplicacion.main() == 0

        salida = capsys.readouterr()
        texto = re.sub(r"\x1b\[[0-9;]*m", "", salida.out + salida.err)  # sin colores
        assert texto.count("Restaurados: 1 | Fallidos: 0") == 1
        assert texto.count("foto.jpg ya existía: restaurado como foto_1.jpg") == 1
        assert "Deshaciendo el último paso" in texto  # el progreso sí se sigue viendo
        log = self._fichero_log(tmp_path)
        assert "Deshacer completado | Restaurados: 1 | Fallidos: 0" in log
        assert "foto.jpg ya existía: restaurado como foto_1.jpg" in log
