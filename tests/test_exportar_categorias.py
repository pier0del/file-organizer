"""Tests de la exportación de categorías: núcleo, opción --exportar-categorias y menú."""

import builtins
import json
import sys
from pathlib import Path

import pytest

from file_organizer.clasificador import (
    CATEGORIAS_DEFECTO,
    NOMBRE_EXPORTACION_DEFECTO,
    Clasificador,
)
from file_organizer.cli import aplicacion, interactivo
from file_organizer.config_usuario import ConfiguracionUsuario

PROPIAS = {"Fotos": [".jpg", ".png"], "Papeles": [".pdf"]}


@pytest.fixture(autouse=True)
def _entorno_aislado(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cada test en un directorio temporal y con sus propias preferencias."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FILE_ORGANIZER_CONFIG", str(tmp_path / "uc.json"))


@pytest.fixture
def config_propia(tmp_path: Path) -> Path:
    """Fichero de categorías propio con «Fotos» y «Papeles»."""
    ruta = tmp_path / "propias.json"
    ruta.write_text(json.dumps(PROPIAS), encoding="utf-8")
    return ruta


def _cli(monkeypatch: pytest.MonkeyPatch, *argumentos: str) -> int:
    """Ejecuta la consola con esos argumentos y devuelve su código de salida."""
    monkeypatch.setattr(sys, "argv", ["file-organizer", *argumentos])
    return aplicacion.main()


def _menu(monkeypatch: pytest.MonkeyPatch, respuestas: list[str]) -> int:
    """Ejecuta el menú respondiendo lo que se indica, en orden."""
    pendientes = iter(respuestas)
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(pendientes))
    return interactivo.ejecutar_menu()


class TestExportarCategorias:
    """Exportar las categorías a un JSON, que sirve de plantilla para ``--config``."""

    def test_las_de_por_defecto_se_pueden_volver_a_cargar_con_config(self, tmp_path: Path):
        """El fichero exportado, cargado como configuración, da las mismas categorías."""
        ruta = Clasificador().exportar_categorias(tmp_path / "plantilla.json")
        assert Clasificador(ruta_config=ruta).obtener_categorias() == CATEGORIAS_DEFECTO

    def test_conserva_las_tildes_legibles(self, tmp_path: Path):
        """Se guarda «Diseño» tal cual, sin códigos de escape, para poder editarlo a mano."""
        ruta = Clasificador().exportar_categorias(tmp_path / "plantilla.json")
        assert '"Diseño"' in ruta.read_text(encoding="utf-8")

    def test_exporta_las_categorias_en_uso(self, tmp_path: Path, config_propia: Path):
        """Con una configuración propia se exportan esas, no las de por defecto."""
        ruta = Clasificador(ruta_config=config_propia).exportar_categorias(tmp_path / "copia")
        assert json.loads(ruta.read_text(encoding="utf-8")) == PROPIAS

    def test_sin_extension_le_anade_json(self, tmp_path: Path):
        """A un nombre sin extensión se le añade ``.json``."""
        assert Clasificador().exportar_categorias(tmp_path / "plantilla").name == ("plantilla.json")

    def test_si_es_una_carpeta_crea_el_fichero_dentro(self, tmp_path: Path):
        """Si la ruta es una carpeta, dentro se crea ``categorias.json``."""
        carpeta = tmp_path / "escritorio"
        carpeta.mkdir()
        ruta = Clasificador().exportar_categorias(carpeta)
        assert ruta == carpeta / NOMBRE_EXPORTACION_DEFECTO
        assert ruta.is_file()

    def test_no_sobrescribe_sin_pedirlo(self, tmp_path: Path):
        """Si el fichero ya existe, falla con FileExistsError y no lo toca."""
        existente = tmp_path / "mias.json"
        existente.write_text("no tocar", encoding="utf-8")
        with pytest.raises(FileExistsError):
            Clasificador().exportar_categorias(existente)
        assert existente.read_text(encoding="utf-8") == "no tocar"

    def test_sobrescribe_si_se_pide(self, tmp_path: Path):
        """Con ``sobrescribir`` sí lo reemplaza."""
        existente = tmp_path / "mias.json"
        existente.write_text("viejo", encoding="utf-8")
        Clasificador().exportar_categorias(existente, sobrescribir=True)
        assert json.loads(existente.read_text(encoding="utf-8")) == CATEGORIAS_DEFECTO


class TestOpcionExportarCategorias:
    """La opción ``--exportar-categorias`` de la consola."""

    def test_exporta_sin_necesitar_carpeta(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """Funciona sola, sin ``--carpeta``, y exporta las de por defecto."""
        assert _cli(monkeypatch, "--exportar-categorias", "plantilla.json") == 0
        guardado = json.loads((tmp_path / "plantilla.json").read_text(encoding="utf-8"))
        assert guardado == CATEGORIAS_DEFECTO

    def test_con_config_exporta_esas(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, config_propia: Path
    ):
        """Junto a ``--config``, exporta las de ese fichero."""
        codigo = _cli(
            monkeypatch, "--exportar-categorias", "copia.json", "--config", str(config_propia)
        )
        assert codigo == 0
        assert json.loads((tmp_path / "copia.json").read_text(encoding="utf-8")) == PROPIAS

    def test_no_sobrescribe_un_fichero_existente(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """Si el fichero ya existe, termina con error y lo deja como estaba."""
        existente = tmp_path / "mias.json"
        existente.write_text("no tocar", encoding="utf-8")
        assert _cli(monkeypatch, "--exportar-categorias", str(existente)) == 1
        assert existente.read_text(encoding="utf-8") == "no tocar"

    def test_config_invalida_da_error_y_no_crea_nada(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """Con un ``--config`` que no existe, termina con error sin crear el fichero."""
        codigo = _cli(
            monkeypatch, "--exportar-categorias", "copia.json", "--config", "no_existe.json"
        )
        assert codigo == 1
        assert not (tmp_path / "copia.json").exists()


class TestMenuExportarCategorias:
    """La opción 09 del menú (exportar categorías)."""

    def test_opcion_09_exporta_las_de_por_defecto(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """Respondiendo ENTER a todo, guarda las de por defecto en ``categorias.json``."""
        # 09=exportar, config=ENTER (por defecto), destino=ENTER (categorias.json), pausa, salir
        assert _menu(monkeypatch, ["09", "", "", "", "11"]) == 0
        guardado = (tmp_path / NOMBRE_EXPORTACION_DEFECTO).read_text(encoding="utf-8")
        assert json.loads(guardado) == CATEGORIAS_DEFECTO

    def test_pregunta_antes_de_sobrescribir(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """Si el fichero existe, pregunta: con «n» lo deja y con «s» lo reemplaza."""
        existente = tmp_path / "mias.json"
        existente.write_text("no tocar", encoding="utf-8")

        _menu(monkeypatch, ["09", "", "mias.json", "n", "", "11"])
        assert existente.read_text(encoding="utf-8") == "no tocar"

        _menu(monkeypatch, ["09", "", "mias.json", "s", "", "11"])
        assert json.loads(existente.read_text(encoding="utf-8")) == CATEGORIAS_DEFECTO

    def test_elegir_que_exportar_no_cambia_el_config_que_se_usa_al_organizar(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, config_propia: Path
    ):
        """Exportar las de por defecto («-») no cambia la configuración guardada para organizar."""
        cfg = ConfiguracionUsuario().cargar()
        cfg.establecer("ultima_config", str(config_propia))
        cfg.guardar()

        # '-' = exportar las de por defecto
        _menu(monkeypatch, ["09", "-", "defecto.json", "", "11"])

        assert json.loads((tmp_path / "defecto.json").read_text(encoding="utf-8")) == (
            CATEGORIAS_DEFECTO
        )
        assert ConfiguracionUsuario().cargar().obtener("ultima_config") == str(config_propia)
