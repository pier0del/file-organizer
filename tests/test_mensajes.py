"""Tests de los mensajes que cambian según se use la consola con comandos o el menú.

Los comandos pueden hablar de --simular o --destino; el menú no tiene esas opciones, así que
debe indicar lo que sí se puede hacer desde él.
"""

import builtins
import logging
import re
import sys
from pathlib import Path

import pytest

from file_organizer.cli import aplicacion, interactivo
from file_organizer.organizador import OrganizadorArchivos


@pytest.fixture(autouse=True)
def _consola(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Directorio y preferencias temporales, y el logging restaurado al acabar."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FILE_ORGANIZER_CONFIG", str(tmp_path / "uc.json"))
    raiz = logging.getLogger()
    manejadores, nivel = raiz.handlers[:], raiz.level
    yield
    for manejador in raiz.handlers[:]:
        manejador.close()
        raiz.removeHandler(manejador)
    for manejador in manejadores:
        raiz.addHandler(manejador)
    raiz.setLevel(nivel)


@pytest.fixture
def carpeta(tmp_path: Path) -> Path:
    """Una carpeta con una foto y un PDF sin organizar."""
    carpeta = tmp_path / "Descargas"
    carpeta.mkdir()
    (carpeta / "foto.jpg").write_text("foto")
    (carpeta / "informe.pdf").write_text("informe")
    return carpeta


def _main(monkeypatch: pytest.MonkeyPatch, capsys, *argumentos: str) -> str:
    """Ejecuta la consola con esos argumentos y devuelve lo que mostró, sin colores."""
    monkeypatch.setattr(sys, "argv", ["file-organizer", *argumentos])
    aplicacion.main()
    return re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)


def _menu(monkeypatch: pytest.MonkeyPatch, capsys, respuestas: list[str]) -> str:
    """Ejecuta el menú respondiendo lo que se indica y devuelve lo que mostró, sin colores."""
    pendientes = iter(respuestas)
    monkeypatch.setattr(builtins, "input", lambda pregunta="": next(pendientes))
    interactivo.ejecutar_menu()
    return re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)


class TestSimular:
    """Qué hacer después de simular."""

    def test_con_comandos_dice_que_se_quite_simular(self, carpeta: Path, monkeypatch, capsys):
        """Con comandos: ejecutar lo mismo sin ``--simular``, sin hablar de opciones del menú."""
        salida = _main(monkeypatch, capsys, "--carpeta", str(carpeta), "--simular")
        assert "RESUMEN DE LA SIMULACIÓN (no se ha movido nada)" in salida
        assert "Para hacerlo de verdad, ejecuta lo mismo sin --simular." in salida
        assert "opción 01" not in salida

    def test_en_el_menu_dice_que_opcion_elegir(self, carpeta: Path, monkeypatch, capsys):
        """En el menú: elegir la opción 01, sin hablar de ``--simular``."""
        # 02, carpeta, ENTER en agrupar/duplicados/config, pausa, salir
        salida = _menu(monkeypatch, capsys, ["02", str(carpeta), "", "", "", "", "11"])
        assert "Para hacerlo de verdad, elige la opción 01 (Organizar carpeta)." in salida
        assert "--simular" not in salida

    def test_no_repite_que_no_se_mueve_nada(self, carpeta: Path, monkeypatch, capsys):
        """«No se ha movido nada» sale una vez, no en el resumen y otra vez al final."""
        salida = _main(monkeypatch, capsys, "--carpeta", str(carpeta), "--simular")
        assert salida.count("No se ha movido nada") == 1


class TestSinHistorial:
    """Qué hacer si no hay historial porque se organizó en otro destino."""

    def test_con_comandos_sugiere_destino(self, carpeta: Path, monkeypatch, capsys):
        """Con comandos: indicar esa carpeta con ``--destino``, y el aviso sale una sola vez."""
        salida = _main(monkeypatch, capsys, "--carpeta", str(carpeta), "--deshacer")
        assert salida.count("No hay historial guardado para deshacer") == 1  # no sale repetido
        assert "indica también esa carpeta con --destino." in salida

    @pytest.mark.parametrize("opcion", ["03", "04", "05", "06"])
    def test_en_el_menu_no_habla_de_destino(self, carpeta: Path, monkeypatch, capsys, opcion):
        """En el menú (que no tiene destino): usar la ventana o los comandos, en cada opción que lee
        el historial.
        """
        respuestas = [opcion, str(carpeta)]
        if opcion == "06":
            respuestas.append("")  # estadísticas pregunta por las categorías
        salida = _menu(monkeypatch, capsys, [*respuestas, "", "11"])
        assert "el menú no puede usarla" in salida
        assert "con --destino." not in salida


def test_tras_deshacer_dice_que_paso_viene_sin_hablar_de_opciones(
    carpeta: Path, monkeypatch, capsys
):
    """Tras deshacer, dice qué paso se deshará la próxima vez, sin nombrar ``--deshacer``."""
    OrganizadorArchivos(carpeta_origen=carpeta).ejecutar()
    (carpeta / "nuevo.png").write_text("nuevo")
    OrganizadorArchivos(carpeta_origen=carpeta).ejecutar()

    salida = _main(monkeypatch, capsys, "--carpeta", str(carpeta), "--deshacer")

    assert "Quedan 1 paso(s) en el historial: el próximo que se deshará es la organización" in (
        salida
    )
    assert "--deshacer revertirá" not in salida
