"""Tests de otras funciones del organizador: ignorados, backup, progreso, su propio log y lo
que recuerda de la última ejecución.
"""

import stat
import sys
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from file_organizer import organizador
from file_organizer.ignorados import NOMBRE_ARCHIVO_IGNORE
from file_organizer.mover_archivos import NOMBRE_ARCHIVO_HISTORIAL, leer_carpeta_origen_historial
from file_organizer.organizador import NOMBRE_CARPETA_BACKUPS, OrganizadorArchivos


def _poblar(carpeta: Path, nombres: list[str]) -> None:
    """Crea en la carpeta ficheros con esos nombres."""
    for nombre in nombres:
        (carpeta / nombre).write_text("x")


class TestIgnorados:
    """Los ficheros que el organizador se salta."""

    def test_salta_ficheros_ignorados(self, tmp_path: Path):
        """Lo que coincide con ``.file-organizer-ignore`` se queda donde está."""
        _poblar(tmp_path, ["foto.jpg", "salida.log", "notas.txt"])
        (tmp_path / NOMBRE_ARCHIVO_IGNORE).write_text("*.log\n", encoding="utf-8")

        org = OrganizadorArchivos(carpeta_origen=tmp_path)
        procesados = org.ejecutar()

        assert procesados == 2  # foto.jpg y notas.txt; salida.log ignorado
        assert (tmp_path / "salida.log").exists()
        assert not (tmp_path / "Imagenes" / "salida.log").exists()

    def test_ignora_su_propio_fichero_ignore(self, tmp_path: Path):
        """El propio fichero de ignorados no se organiza."""
        _poblar(tmp_path, ["foto.jpg"])
        (tmp_path / NOMBRE_ARCHIVO_IGNORE).write_text("*.log\n", encoding="utf-8")
        org = OrganizadorArchivos(carpeta_origen=tmp_path)
        org.ejecutar()
        # El fichero de ignorados no se mueve a ninguna categoría.
        assert (tmp_path / NOMBRE_ARCHIVO_IGNORE).exists()

    def test_esta_ignorado_para_ocultos_e_historial(self, tmp_path: Path):
        """Los ficheros ocultos y el historial siempre se ignoran."""
        org = OrganizadorArchivos(carpeta_origen=tmp_path)
        assert org.esta_ignorado(tmp_path / ".oculto")
        assert org.esta_ignorado(tmp_path / ".file-organizer-history.json")


class TestBackup:
    """La copia de seguridad en .zip antes de organizar."""

    def test_crear_backup_genera_zip_con_ficheros(self, tmp_path: Path):
        """El zip lleva los ficheros de la carpeta."""
        _poblar(tmp_path, ["a.txt", "b.jpg"])
        org = OrganizadorArchivos(carpeta_origen=tmp_path)
        ruta_zip = org.crear_backup()

        assert ruta_zip is not None and ruta_zip.exists()
        with zipfile.ZipFile(ruta_zip) as zf:
            assert set(zf.namelist()) == {"a.txt", "b.jpg"}

    def test_dos_backups_en_el_mismo_segundo_no_se_pisan(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Con la hora parada, cada backup del mismo segundo lleva ``_2``, ``_3``... y el primero
        sigue intacto.
        """

        class RelojParado(datetime):
            @classmethod
            def now(cls, tz=None):  # type: ignore[override]
                return datetime(2026, 1, 2, 3, 4, 5)

        monkeypatch.setattr(organizador, "datetime", RelojParado)
        _poblar(tmp_path, ["a.txt"])
        primero = OrganizadorArchivos(carpeta_origen=tmp_path).crear_backup()
        (tmp_path / "b.txt").write_text("nuevo")
        segundo = OrganizadorArchivos(carpeta_origen=tmp_path).crear_backup()
        tercero = OrganizadorArchivos(carpeta_origen=tmp_path).crear_backup()

        assert primero is not None and segundo is not None and tercero is not None
        assert [primero.name, segundo.name, tercero.name] == [
            "backup_20260102_030405.zip",
            "backup_20260102_030405_2.zip",
            "backup_20260102_030405_3.zip",
        ]
        with zipfile.ZipFile(primero) as zf:  # el primero sigue intacto
            assert zf.namelist() == ["a.txt"]
        with zipfile.ZipFile(segundo) as zf:
            assert sorted(zf.namelist()) == ["a.txt", "b.txt"]

    @pytest.mark.skipif(sys.platform == "win32", reason="permisos POSIX no aplican en windows")
    def test_el_backup_solo_lo_puede_leer_su_dueno(self, tmp_path: Path):
        """El zip copia también los ficheros privados, así que solo lo puede leer su dueño."""
        _poblar(tmp_path, ["a.txt"])
        ruta_zip = OrganizadorArchivos(carpeta_origen=tmp_path).crear_backup()
        assert ruta_zip is not None
        assert stat.S_IMODE(ruta_zip.stat().st_mode) == 0o600

    def test_backup_vacio_devuelve_none(self, tmp_path: Path):
        """Sin ficheros no se crea ningún zip."""
        org = OrganizadorArchivos(carpeta_origen=tmp_path)
        assert org.crear_backup() is None

    def test_ejecutar_con_backup_crea_carpeta_de_backups(self, tmp_path: Path):
        """Con ``backup``, organizar deja un zip en ``.file-organizer-backups``."""
        _poblar(tmp_path, ["a.txt"])
        org = OrganizadorArchivos(carpeta_origen=tmp_path, backup=True)
        org.ejecutar()
        assert (tmp_path / NOMBRE_CARPETA_BACKUPS).is_dir()
        zips = list((tmp_path / NOMBRE_CARPETA_BACKUPS).glob("*.zip"))
        assert len(zips) == 1

    def test_backup_no_incluye_ignorados(self, tmp_path: Path):
        """Los ficheros ignorados no entran en el zip."""
        _poblar(tmp_path, ["a.txt", "b.log"])
        (tmp_path / NOMBRE_ARCHIVO_IGNORE).write_text("*.log\n", encoding="utf-8")
        org = OrganizadorArchivos(carpeta_origen=tmp_path)
        ruta_zip = org.crear_backup()
        with zipfile.ZipFile(ruta_zip) as zf:
            assert zf.namelist() == ["a.txt"]


class TestProgreso:
    """El aviso de progreso al organizar."""

    def test_callback_recibe_progreso_creciente(self, tmp_path: Path):
        """Se llama tras cada fichero con cuántos van y el total."""
        _poblar(tmp_path, ["a.jpg", "b.pdf", "c.py"])
        llamadas: list[tuple[int, int]] = []

        org = OrganizadorArchivos(carpeta_origen=tmp_path)
        org.ejecutar(progreso=lambda actual, total: llamadas.append((actual, total)))

        assert llamadas == [(1, 3), (2, 3), (3, 3)]

    def test_sin_ficheros_no_llama_al_callback(self, tmp_path: Path):
        """Sin ficheros no se llama nunca."""
        llamadas = []
        org = OrganizadorArchivos(carpeta_origen=tmp_path)
        org.ejecutar(progreso=lambda a, t: llamadas.append((a, t)))
        assert llamadas == []


class TestBackupSinRecomprimir:
    """Los formatos ya comprimidos se guardan en el zip sin volver a comprimirlos."""

    def test_formatos_ya_comprimidos_se_guardan_sin_comprimir(self, tmp_path: Path):
        """Fotos y vídeos van sin comprimir (STORED); el texto, comprimido (DEFLATED)."""
        _poblar(tmp_path, ["foto.jpg", "video.mp4", "notas.txt"])
        org = OrganizadorArchivos(carpeta_origen=tmp_path)
        ruta_zip = org.crear_backup()
        assert ruta_zip is not None

        with zipfile.ZipFile(ruta_zip) as zf:
            metodos = {info.filename: info.compress_type for info in zf.infolist()}
        assert metodos == {
            "foto.jpg": zipfile.ZIP_STORED,
            "video.mp4": zipfile.ZIP_STORED,
            "notas.txt": zipfile.ZIP_DEFLATED,
        }

    def test_el_backup_se_puede_extraer_igual(self, tmp_path: Path):
        """Se extraen igual los guardados sin comprimir y los comprimidos."""
        (tmp_path / "foto.jpg").write_bytes(b"\xff\xd8 datos de imagen")
        (tmp_path / "notas.txt").write_text("hola " * 100)
        ruta_zip = OrganizadorArchivos(carpeta_origen=tmp_path).crear_backup()
        assert ruta_zip is not None

        with zipfile.ZipFile(ruta_zip) as zf:
            assert zf.read("foto.jpg") == b"\xff\xd8 datos de imagen"
            assert zf.read("notas.txt") == ("hola " * 100).encode()


class TestLogPropio:
    """El organizador no mueve su propio fichero de log."""

    def test_no_organiza_su_propio_log(self, tmp_path: Path):
        """``file-organizer.log`` y sus copias rotadas se quedan, aunque se ejecute desde la carpeta
        que se organiza.
        """
        _poblar(tmp_path, ["file-organizer.log", "file-organizer.log.1", "foto.jpg"])

        procesados = OrganizadorArchivos(carpeta_origen=tmp_path).ejecutar()

        assert procesados == 1
        assert (tmp_path / "file-organizer.log").exists()
        assert (tmp_path / "file-organizer.log.1").exists()

    def test_otros_logs_si_se_organizan(self, tmp_path: Path):
        """Los .log de otros programas sí se organizan."""
        _poblar(tmp_path, ["servidor.log"])
        assert OrganizadorArchivos(carpeta_origen=tmp_path).ejecutar() == 1


class TestMovimientosYOrigenDelHistorial:
    """Lo que recuerda de la última ejecución y de dónde vinieron los ficheros."""

    def test_la_simulacion_dice_que_moveria_sin_mover_nada(self, tmp_path: Path):
        """Simulando, cada fichero tiene su categoría y destino previstos, pero no se mueve."""
        _poblar(tmp_path, ["a.jpg", "b.pdf"])
        org = OrganizadorArchivos(carpeta_origen=tmp_path, modo_simulacion=True)
        org.ejecutar()

        planeados = {r.origen.name: (r.categoria, r.destino) for r in org.ultimos_movimientos}
        assert planeados == {
            "a.jpg": ("Imagenes", tmp_path / "Imagenes" / "a.jpg"),
            "b.pdf": ("Documentos", tmp_path / "Documentos" / "b.pdf"),
        }
        assert (tmp_path / "a.jpg").exists() and (tmp_path / "b.pdf").exists()

    def test_organizar_tambien_los_registra(self, tmp_path: Path):
        """Organizando de verdad, también quedan los movimientos de esa ejecución."""
        _poblar(tmp_path, ["a.jpg"])
        org = OrganizadorArchivos(carpeta_origen=tmp_path)
        org.ejecutar()
        assert [r.origen.name for r in org.ultimos_movimientos] == ["a.jpg"]
        assert (tmp_path / "Imagenes" / "a.jpg").exists()

    def test_el_historial_del_destino_indica_el_origen(self, tmp_path: Path):
        """El historial de un destino dice de qué carpeta vienen sus ficheros (lo usa la pestaña
        Historial de la GUI).
        """
        origen = tmp_path / "origen"
        origen.mkdir()
        _poblar(origen, ["a.jpg"])
        destino = tmp_path / "ordenado"
        OrganizadorArchivos(carpeta_origen=origen, carpeta_destino=destino).ejecutar()

        assert leer_carpeta_origen_historial(destino) == origen.resolve()
        assert leer_carpeta_origen_historial(origen) is None  # allí no hay historial

    def test_historial_ilegible_no_indica_origen(self, tmp_path: Path):
        """Si el historial está dañado, no se sabe el origen, pero no falla."""
        (tmp_path / NOMBRE_ARCHIVO_HISTORIAL).write_text("{ roto")
        assert leer_carpeta_origen_historial(tmp_path) is None
