"""Tests del deshacer por pasos: cada organización (o sesión de vigilancia) es un paso del
historial, y «Deshacer» los revierte de uno en uno, del más reciente al más antiguo."""

import json
import re
import sys
from pathlib import Path

import pytest

from file_organizer import mover_archivos, organizador
from file_organizer.cli import aplicacion
from file_organizer.excepciones import ErrorHistorialCorrupto, ErrorHistorialNoEncontrado
from file_organizer.mover_archivos import NOMBRE_ARCHIVO_HISTORIAL, MoverArchivos
from file_organizer.organizador import OrganizadorArchivos


def _organizar(carpeta: Path, *ficheros: tuple[str, str]) -> OrganizadorArchivos:
    """Crea los ficheros (nombre, contenido) en la carpeta y la organiza."""
    for nombre, contenido in ficheros:
        (carpeta / nombre).write_text(contenido)
    org = OrganizadorArchivos(carpeta_origen=carpeta)
    org.ejecutar()
    return org


def _deshacer(carpeta: Path):
    """Deshace un paso de la carpeta y devuelve su resultado."""
    resultado = OrganizadorArchivos(carpeta_origen=carpeta).deshacer()
    assert resultado is not None
    return resultado


def _sueltos(carpeta: Path) -> dict[str, str]:
    """Ficheros sin organizar (en la raíz de la carpeta) con su contenido."""
    return {p.name: p.read_text() for p in carpeta.iterdir() if p.is_file() and p.name[0] != "."}


def _pasos(carpeta: Path) -> list[list[str]]:
    """Los ficheros de cada paso guardado en el historial, del más antiguo al último."""
    datos = json.loads((carpeta / NOMBRE_ARCHIVO_HISTORIAL).read_text(encoding="utf-8"))
    return [sorted(Path(r["origen"]).name for r in o["registros"]) for o in datos["operaciones"]]


class TestDeshacerPorPasos:
    """Cada organización es un paso, y deshacer revierte el último."""

    def test_cada_organizacion_es_un_paso_y_se_deshacen_en_orden_inverso(self, tmp_path: Path):
        """Organizado el lunes y el martes, el primer deshacer revierte solo el martes y dice que
        queda el lunes; el segundo, el lunes.
        """
        _organizar(tmp_path, ("lunes.pdf", "l"))
        _organizar(tmp_path, ("martes.jpg", "m"))
        assert _pasos(tmp_path) == [["lunes.pdf"], ["martes.jpg"]]

        primero = _deshacer(tmp_path)
        assert primero.exitosos == 1
        assert _sueltos(tmp_path) == {"martes.jpg": "m"}  # solo el paso del martes
        assert primero.pasos_restantes == 1
        assert primero.siguiente is not None
        assert [r.origen.name for r in primero.siguiente.registros] == ["lunes.pdf"]
        assert primero.siguiente.descripcion().startswith("organización del ")

        segundo = _deshacer(tmp_path)
        assert segundo.exitosos == 1
        assert _sueltos(tmp_path) == {"martes.jpg": "m", "lunes.pdf": "l"}
        assert segundo.pasos_restantes == 0 and segundo.siguiente is None

        assert _deshacer(tmp_path).exitosos == 0  # ya no queda nada

    def test_dos_ficheros_con_el_mismo_nombre_vuelven_los_dos_sin_perder_ninguno(
        self, tmp_path: Path
    ):
        """El ejemplo de la foto del lunes (A) y la del martes (B), las dos «foto.jpg»."""
        _organizar(tmp_path, ("foto.jpg", "A"))
        _organizar(tmp_path, ("foto.jpg", "B"))  # va a Imagenes/foto_1.jpg

        assert _deshacer(tmp_path).renombrados == []
        assert _sueltos(tmp_path) == {"foto.jpg": "B"}  # como el martes antes de organizar

        segundo = _deshacer(tmp_path)
        assert [(r.origen.name, p.name) for r, p in segundo.renombrados] == [
            ("foto.jpg", "foto_1.jpg")
        ]
        assert segundo.fallidos == 0
        assert _sueltos(tmp_path) == {"foto.jpg": "B", "foto_1.jpg": "A"}

    def test_organizar_sin_ficheros_no_crea_un_paso_vacio(self, tmp_path: Path):
        """Organizar una carpeta sin nada que mover no añade un paso al historial."""
        _organizar(tmp_path, ("a.pdf", "x"))
        _organizar(tmp_path)  # no hay nada que organizar
        assert _pasos(tmp_path) == [["a.pdf"]]

    def test_limpiar_borra_todos_los_pasos(self, tmp_path: Path):
        """Limpiar el historial cuenta los movimientos de todos los pasos y los quita."""
        _organizar(tmp_path, ("a.pdf", "x"), ("b.pdf", "x"))
        _organizar(tmp_path, ("c.jpg", "x"))

        assert OrganizadorArchivos(carpeta_origen=tmp_path).limpiar_historial() == 3
        assert _pasos(tmp_path) == []
        assert _deshacer(tmp_path).exitosos == 0


class TestLimiteDePasos:
    """El historial guarda un número limitado de pasos."""

    def test_se_guardan_como_mucho_los_ultimos_pasos_y_se_avisa(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Al pasar del límite se descarta el paso más antiguo, se avisa, y sus ficheros ya no se
        pueden devolver.
        """
        monkeypatch.setattr(mover_archivos, "MAX_OPERACIONES", 3)
        monkeypatch.setattr(organizador, "MAX_OPERACIONES", 3)
        for i in range(3):
            assert _organizar(tmp_path, (f"f{i}.pdf", "x")).avisos == []

        cuarto = _organizar(tmp_path, ("f3.pdf", "x"))

        assert _pasos(tmp_path) == [["f1.pdf"], ["f2.pdf"], ["f3.pdf"]]
        assert len(cuarto.avisos) == 1 and "como mucho 3 pasos" in cuarto.avisos[0]
        for _ in range(3):
            _deshacer(tmp_path)
        # El paso más antiguo se descartó: ese fichero ya no se puede deshacer.
        assert sorted(_sueltos(tmp_path)) == ["f1.pdf", "f2.pdf", "f3.pdf"]
        assert (tmp_path / "Documentos" / "f0.pdf").exists()


class TestHistorialesAnteriores:
    """Historiales de versiones anteriores o dañados."""

    def test_un_historial_de_la_version_2_se_lee_como_un_paso(self, tmp_path: Path):
        """Uno de la versión 2 (sin pasos) cuenta como un paso, y lo nuevo se apila encima."""
        (tmp_path / "Documentos").mkdir()
        (tmp_path / "Documentos" / "viejo.pdf").write_text("v")
        historial_v2 = {
            "version": 2,
            "carpeta_origen": str(tmp_path.resolve()),
            "carpeta_destino": str(tmp_path.resolve()),
            "registros": [
                {
                    "origen": str(tmp_path.resolve() / "viejo.pdf"),
                    "destino": str(tmp_path.resolve() / "Documentos" / "viejo.pdf"),
                    "categoria": "Documentos",
                    "fecha": "2026-01-01T10:00:00",
                    "checksum": None,
                }
            ],
        }
        (tmp_path / NOMBRE_ARCHIVO_HISTORIAL).write_text(json.dumps(historial_v2))

        _organizar(tmp_path, ("nuevo.jpg", "n"))  # se apila encima

        assert _pasos(tmp_path) == [["viejo.pdf"], ["nuevo.jpg"]]
        _deshacer(tmp_path)
        segundo = _deshacer(tmp_path)
        assert segundo.exitosos == 1
        assert sorted(_sueltos(tmp_path)) == ["nuevo.jpg", "viejo.pdf"]

    def test_un_tipo_de_paso_desconocido_es_un_historial_corrupto(self, tmp_path: Path):
        """Un paso de un tipo que el programa no conoce hace que el historial sea inválido."""
        datos = {
            "version": 3,
            "carpeta_origen": str(tmp_path.resolve()),
            "carpeta_destino": str(tmp_path.resolve()),
            "operaciones": [
                {"tipo": "inventado", "inicio": "2026-01-01T10:00:00", "registros": []}
            ],
        }
        (tmp_path / NOMBRE_ARCHIVO_HISTORIAL).write_text(json.dumps(datos))
        mover = MoverArchivos(carpeta_origen=tmp_path, carpeta_destino=tmp_path)
        with pytest.raises(ErrorHistorialCorrupto):
            mover.cargar_historial()

    def test_organizar_con_un_historial_danado_lo_aparta_y_avisa(self, tmp_path: Path):
        """Si el historial está dañado, organizar lo guarda aparte tal cual, avisa y empieza uno
        nuevo.
        """
        (tmp_path / NOMBRE_ARCHIVO_HISTORIAL).write_text("{ roto")

        org = _organizar(tmp_path, ("a.pdf", "x"))

        assert len(org.avisos) == 1 and "guardado aparte" in org.avisos[0]
        copias = list(tmp_path.glob(".file-organizer-history.*.json"))
        assert len(copias) == 1 and copias[0].read_text() == "{ roto"
        assert _pasos(tmp_path) == [["a.pdf"]]


class TestConsola:
    """Los pasos del historial vistos desde la consola."""

    @pytest.fixture(autouse=True)
    def _aislar(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Cada test en un directorio temporal y con sus propias preferencias."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("FILE_ORGANIZER_CONFIG", str(tmp_path / "uc.json"))

    @staticmethod
    def _cli(monkeypatch: pytest.MonkeyPatch, carpeta: Path, *opciones: str) -> int:
        """Ejecuta la consola sobre ``carpeta`` con esas opciones y devuelve su código."""
        monkeypatch.setattr(sys, "argv", ["file-organizer", "--carpeta", str(carpeta), *opciones])
        return aplicacion.main()

    def test_historial_y_deshacer_por_pasos(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        """``--historial`` numera los pasos del más reciente al más antiguo; cada ``--deshacer``
        dice cuántos quedan.
        """
        carpeta = tmp_path / "descargas"
        carpeta.mkdir()
        _organizar(carpeta, ("lunes.pdf", "l"))
        _organizar(carpeta, ("martes.jpg", "m"))
        capsys.readouterr()

        assert self._cli(monkeypatch, carpeta, "--historial") == 0
        salida = capsys.readouterr().out
        assert salida.index("PASO 1") < salida.index("martes.jpg") < salida.index("PASO 2")
        assert salida.index("PASO 2") < salida.index("lunes.pdf")
        assert "2 movimiento(s) en 2 paso(s)" in salida

        assert self._cli(monkeypatch, carpeta, "--deshacer") == 0
        assert "Quedan 1 paso(s)" in capsys.readouterr().out
        assert self._cli(monkeypatch, carpeta, "--deshacer") == 0
        assert "Quedan" not in capsys.readouterr().out
        assert sorted(_sueltos(carpeta)) == ["lunes.pdf", "martes.jpg"]

    def test_deshacer_avisa_de_los_restaurados_con_otro_nombre(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        """Si en el sitio original ya hay otro fichero, se restaura con otro nombre y se dice."""
        carpeta = tmp_path / "descargas"
        carpeta.mkdir()
        _organizar(carpeta, ("foto.jpg", "A"))
        (carpeta / "foto.jpg").write_text("del usuario")

        assert self._cli(monkeypatch, carpeta, "--deshacer") == 0

        salida = re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)  # sin colores
        assert "foto.jpg ya existía: restaurado como foto_1.jpg" in salida
        assert _sueltos(carpeta) == {"foto.jpg": "del usuario", "foto_1.jpg": "A"}

    def test_sin_historial_limpiar_lo_indica(self, tmp_path: Path):
        """Limpiar una carpeta sin historial lanza ErrorHistorialNoEncontrado."""
        with pytest.raises(ErrorHistorialNoEncontrado):
            OrganizadorArchivos(carpeta_origen=tmp_path / "no_existe").limpiar_historial()
