"""Tests de la configuración persistente del usuario."""

import stat
import sys
from pathlib import Path

import pytest

from file_organizer.config_usuario import (
    CONFIG_DEFECTO,
    ConfiguracionUsuario,
    ruta_config_por_defecto,
)


@pytest.fixture
def ruta_cfg(tmp_path: Path) -> Path:
    """Ruta de un fichero de preferencias que aún no existe."""
    return tmp_path / "config.json"


class TestCargarGuardar:
    """Leer y guardar las preferencias."""

    def test_valores_por_defecto_si_no_existe(self, ruta_cfg: Path):
        """Sin fichero, cada preferencia tiene su valor por defecto."""
        cfg = ConfiguracionUsuario(ruta_cfg).cargar()
        assert cfg.obtener("ultima_carpeta") == ""
        assert cfg.obtener("backup") is False

    def test_guardar_y_recargar(self, ruta_cfg: Path):
        """Lo guardado lo lee igual otra instancia, como haría otra ventana o proceso."""
        cfg = ConfiguracionUsuario(ruta_cfg)
        cfg.establecer("ultima_carpeta", "C:/Descargas")
        cfg.establecer("backup", True)
        cfg.guardar()

        recargada = ConfiguracionUsuario(ruta_cfg).cargar()
        assert recargada.obtener("ultima_carpeta") == "C:/Descargas"
        assert recargada.obtener("backup") is True

    def test_actualizar_varias_claves(self, ruta_cfg: Path):
        """``actualizar`` cambia varias preferencias de una vez."""
        cfg = ConfiguracionUsuario(ruta_cfg)
        cfg.actualizar(ultima_carpeta="X", recursivo_watch=True)
        assert cfg.obtener("ultima_carpeta") == "X"
        assert cfg.obtener("recursivo_watch") is True

    @pytest.mark.skipif(sys.platform == "win32", reason="permisos POSIX no aplican en windows")
    def test_solo_las_puede_leer_su_dueno(self, tmp_path: Path):
        """Llevan las rutas de las últimas carpetas: fichero 0o600 y su carpeta, al crearla, 0o700."""
        ruta = tmp_path / "prefs" / "config.json"
        cfg = ConfiguracionUsuario(ruta)
        cfg.establecer("ultima_carpeta", "/datos/fotos")
        cfg.guardar()

        assert stat.S_IMODE(ruta.stat().st_mode) == 0o600
        assert stat.S_IMODE(ruta.parent.stat().st_mode) == 0o700

    def test_guardar_crea_carpeta_padre(self, tmp_path: Path):
        """Guardar crea las carpetas que falten hasta el fichero."""
        ruta = tmp_path / "sub" / "dir" / "config.json"
        ConfiguracionUsuario(ruta).guardar()
        assert ruta.exists()


class TestRobustez:
    """Un fichero de preferencias dañado o de otra versión no rompe el programa."""

    def test_fichero_corrupto_usa_valores_por_defecto(self, ruta_cfg: Path):
        """Si no es un JSON válido, se usan todos los valores por defecto."""
        ruta_cfg.write_text("{ esto no es json", encoding="utf-8")
        cfg = ConfiguracionUsuario(ruta_cfg).cargar()
        assert cfg.datos == dict(CONFIG_DEFECTO)

    def test_ignora_claves_desconocidas(self, ruta_cfg: Path):
        """Se descartan las claves que el programa no conoce y se leen las demás."""
        ruta_cfg.write_text('{"ultima_carpeta": "A", "no_existe": 123}', encoding="utf-8")
        cfg = ConfiguracionUsuario(ruta_cfg).cargar()
        assert cfg.obtener("ultima_carpeta") == "A"
        assert "no_existe" not in cfg.datos

    def test_completa_claves_faltantes(self, ruta_cfg: Path):
        """Las preferencias que falten (de una versión anterior) toman su valor por defecto."""
        ruta_cfg.write_text('{"ultima_carpeta": "A"}', encoding="utf-8")
        cfg = ConfiguracionUsuario(ruta_cfg).cargar()
        assert cfg.obtener("backup") is False  # rellenado con el valor por defecto


class TestRutaPorDefecto:
    """Dónde se guardan las preferencias."""

    def test_usa_variable_de_entorno(self, monkeypatch, tmp_path: Path):
        """FILE_ORGANIZER_CONFIG cambia la ruta (los tests la usan para no tocar la real)."""
        objetivo = tmp_path / "custom.json"
        monkeypatch.setenv("FILE_ORGANIZER_CONFIG", str(objetivo))
        assert ruta_config_por_defecto() == objetivo

    def test_sin_variable_usa_home(self, monkeypatch):
        """Sin la variable, ``~/.file-organizer/config.json``."""
        monkeypatch.delenv("FILE_ORGANIZER_CONFIG", raising=False)
        ruta = ruta_config_por_defecto()
        assert ruta.name == "config.json"
        assert ".file-organizer" in str(ruta)
