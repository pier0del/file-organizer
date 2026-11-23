"""Tests de las rutas que escribe o pega el usuario: cómo se preparan y comprueban (rutas.py) y
que la consola y el menú las traten igual.

Los de la GUI están en test_gui.py, con el resto de la interfaz gráfica.
"""

import builtins
import logging
import re
import sys
from pathlib import Path

import pytest

from file_organizer.cli import aplicacion, comandos, interactivo
from file_organizer.excepciones import ErrorRuta
from file_organizer.rutas import (
    comprobar_carpeta,
    comprobar_destino,
    comprobar_existe,
    comprobar_fichero_de_categorias,
    comprobar_fichero_nuevo,
    limpiar_texto_ruta,
    preparar_ruta,
)

SOLO_POSIX = pytest.mark.skipif(
    sys.platform == "win32", reason="en Windows la barra invertida separa carpetas"
)


@pytest.fixture
def casa(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Una carpeta personal de prueba: ``~`` apunta a ella (HOME en macOS y Linux, USERPROFILE
    en Windows)."""
    carpeta = tmp_path / "casa"
    carpeta.mkdir()
    monkeypatch.setenv("HOME", str(carpeta))
    monkeypatch.setenv("USERPROFILE", str(carpeta))
    return carpeta


# ---------------------------------------------------------------------------- preparar


class TestPrepararRuta:
    """Dejar lista para usar la ruta tal como la escribe o la pega el usuario."""

    def test_quita_los_espacios_de_alrededor(self):
        """Los espacios que sobran al copiar o al escribir no forman parte de la ruta."""
        assert preparar_ruta("   /datos/fotos  \n") == Path("/datos/fotos")

    def test_quita_las_comillas_de_copiar_como_ruta(self):
        """«Copiar como ruta de acceso» en Windows la pone entre comillas dobles."""
        assert preparar_ruta('"C:/Users/Ana/Mis fotos"') == Path("C:/Users/Ana/Mis fotos")

    def test_quita_las_comillas_simples_de_arrastrar_a_la_terminal(self):
        """Arrastrar una carpeta a la terminal de Linux la pone entre comillas simples y deja un
        espacio detrás."""
        assert preparar_ruta("'/home/ana/Mis fotos' ") == Path("/home/ana/Mis fotos")

    @pytest.mark.parametrize("texto", ['"/datos/fotos', "/datos/fotos'", "'/datos/fotos\""])
    def test_las_comillas_que_no_rodean_la_ruta_se_quedan(self, texto: str):
        """Solo se quitan si abren y cierran la ruta entera, con la misma comilla."""
        assert limpiar_texto_ruta(texto) == texto

    @pytest.mark.parametrize("texto", [None, "", "   ", '""', "''", ' " " '])
    def test_sin_nada_escrito_no_hay_ruta(self, texto: str | None):
        """Vacía no es la carpeta actual (lo que haría ``Path("")``): por eso ENTER en blanco
        en el menú organizaba la carpeta desde la que se abrió el programa."""
        assert preparar_ruta(texto) is None

    def test_la_virgulilla_es_la_carpeta_personal(self, casa: Path):
        """``~`` y ``~/...`` se cambian por la carpeta personal, también entre comillas."""
        assert preparar_ruta("~") == casa
        assert preparar_ruta('"~/Descargas"') == casa / "Descargas"

    @SOLO_POSIX
    def test_quita_los_escapes_de_la_terminal(self):
        """Arrastrar a la terminal de macOS escribe ``Mis\\ fotos``: se lee como ``Mis fotos``."""
        assert preparar_ruta("/Users/ana/Mis\\ fotos\\ (2024)") == Path(
            "/Users/ana/Mis fotos (2024)"
        )

    @SOLO_POSIX
    def test_un_nombre_que_lleva_una_barra_invertida_de_verdad_se_respeta(self, tmp_path: Path):
        """Si la ruta tal cual existe, no se tocan sus barras invertidas."""
        raro = tmp_path / "a\\b"
        raro.mkdir()
        assert preparar_ruta(str(raro)) == raro


# --------------------------------------------------------------------------- comprobar


class TestComprobaciones:
    """Cada comprobación explica con su motivo por qué no sirve una ruta."""

    def test_la_carpeta_tiene_que_existir_y_ser_una_carpeta(self, tmp_path: Path):
        """Una que no existe o un fichero no valen; una carpeta, sí."""
        fichero = tmp_path / "nota.txt"
        fichero.write_text("x")
        with pytest.raises(ErrorRuta, match="La carpeta no existe"):
            comprobar_carpeta(tmp_path / "no_existe")
        with pytest.raises(ErrorRuta, match="no es una carpeta, es un fichero"):
            comprobar_carpeta(fichero)
        assert comprobar_carpeta(tmp_path) == tmp_path

    def test_el_destino_puede_no_existir_aun(self, tmp_path: Path):
        """Se creará al organizar: basta con que se pueda crear."""
        assert comprobar_destino(tmp_path / "nuevo" / "sub") == tmp_path / "nuevo" / "sub"

    def test_el_destino_no_puede_ser_un_fichero_ni_estar_dentro_de_uno(self, tmp_path: Path):
        """Si él o una carpeta por encima es un fichero, no se podría crear: se dice cuál."""
        fichero = tmp_path / "nota.txt"
        fichero.write_text("x")
        with pytest.raises(ErrorRuta, match="El destino es un fichero"):
            comprobar_destino(fichero)
        with pytest.raises(ErrorRuta, match=re.escape(f"«{fichero}» es un fichero")):
            comprobar_destino(fichero / "Ordenado")

    def test_lo_que_se_cifra_tiene_que_existir(self, tmp_path: Path):
        """Un fichero o una carpeta valen; lo que no existe, no."""
        with pytest.raises(ErrorRuta, match="No existe"):
            comprobar_existe(tmp_path / "no_existe.txt")
        assert comprobar_existe(tmp_path) == tmp_path

    def test_el_fichero_de_categorias_tiene_que_ser_un_json(self, tmp_path: Path):
        """Tiene que existir y ser un fichero .json, no otro tipo ni una carpeta."""
        (tmp_path / "cat.txt").write_text("{}")
        (tmp_path / "carpeta.json").mkdir()
        (tmp_path / "cat.json").write_text("{}")
        with pytest.raises(ErrorRuta, match="no existe"):
            comprobar_fichero_de_categorias(tmp_path / "falta.json")
        for nombre in ("cat.txt", "carpeta.json"):
            with pytest.raises(ErrorRuta, match=r"debe ser un fichero \.json"):
                comprobar_fichero_de_categorias(tmp_path / nombre)
        assert comprobar_fichero_de_categorias(tmp_path / "cat.json") == tmp_path / "cat.json"

    def test_un_fichero_nuevo_necesita_nombre_y_un_sitio_donde_crearse(self, tmp_path: Path):
        """Una carpeta no es un nombre de fichero, y no se puede crear dentro de un fichero."""
        fichero = tmp_path / "nota.txt"
        fichero.write_text("x")
        with pytest.raises(ErrorRuta, match="Es una carpeta"):
            comprobar_fichero_nuevo(tmp_path)
        with pytest.raises(ErrorRuta, match="No se puede crear"):
            comprobar_fichero_nuevo(fichero / "mi.key")
        assert comprobar_fichero_nuevo(tmp_path / "sub" / "mi.key") == tmp_path / "sub" / "mi.key"

    def test_el_error_guarda_el_motivo_y_la_ruta(self, tmp_path: Path):
        """Cada interfaz los muestra a su manera; juntos forman el mensaje."""
        error = ErrorRuta("La carpeta no existe", tmp_path / "x")
        assert (error.motivo, error.ruta) == ("La carpeta no existe", tmp_path / "x")
        assert str(error) == f"La carpeta no existe: {tmp_path / 'x'}"


# -------------------------------------------------------------------- consola y menú


@pytest.fixture
def consola(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Consola aislada: directorio y preferencias temporales, y el logging restaurado al acabar."""
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
def descargas(tmp_path: Path) -> Path:
    """Una carpeta con espacio en el nombre y una foto y un PDF sin organizar."""
    carpeta = tmp_path / "Mis descargas"
    carpeta.mkdir()
    (carpeta / "foto.jpg").write_text("foto")
    (carpeta / "informe.pdf").write_text("informe")
    return carpeta


def _main(monkeypatch: pytest.MonkeyPatch, capsys, *argumentos: str) -> tuple[int, str]:
    """Ejecuta la consola con esos argumentos; devuelve su código y lo que mostró, sin colores."""
    monkeypatch.setattr(sys, "argv", ["file-organizer", *argumentos])
    codigo = aplicacion.main()
    return codigo, re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)


def _menu(monkeypatch: pytest.MonkeyPatch, capsys, respuestas: list[str]) -> str:
    """Ejecuta el menú respondiendo lo que se indica y devuelve lo que mostró (también el log,
    que sale por la salida de errores), sin colores.
    """
    pendientes = iter(respuestas)
    monkeypatch.setattr(builtins, "input", lambda pregunta="": next(pendientes))
    assert interactivo.ejecutar_menu() == 0
    salida = capsys.readouterr()
    return re.sub(r"\x1b\[[0-9;]*m", "", salida.out + salida.err)


def _sin_organizar(carpeta: Path) -> list[str]:
    """Los ficheros visibles que siguen sueltos en la carpeta."""
    return sorted(p.name for p in carpeta.iterdir() if p.is_file() and p.name[0] != ".")


@pytest.mark.usefixtures("consola")
class TestComandos:
    """Las rutas de las opciones de la consola."""

    def test_la_carpeta_entre_comillas_se_organiza(self, descargas: Path, monkeypatch, capsys):
        """Una ruta que llega con sus comillas (copiada así en un script) se lee igual."""
        codigo, _ = _main(monkeypatch, capsys, "--carpeta", f'"{descargas}"')
        assert codigo == 0
        assert (descargas / "Imagenes" / "foto.jpg").exists()

    def test_el_destino_con_virgulilla(self, descargas: Path, casa: Path, monkeypatch, capsys):
        """``~`` en el destino es la carpeta personal, aunque la terminal no lo cambie (entre
        comillas, o en algunas terminales de Windows)."""
        codigo, _ = _main(monkeypatch, capsys, "-f", str(descargas), "--destino", "~/Ordenado")
        assert codigo == 0
        assert (casa / "Ordenado" / "Imagenes" / "foto.jpg").exists()

    def test_un_destino_dentro_de_un_fichero_se_explica(self, descargas: Path, monkeypatch, capsys):
        """No se crea nada y se dice qué ruta es un fichero."""
        fichero = descargas / "informe.pdf"
        codigo, salida = _main(
            monkeypatch, capsys, "-f", str(descargas), "--destino", str(fichero / "Ordenado")
        )
        assert codigo == 1
        assert f"«{fichero}» es un fichero, no una carpeta" in salida
        assert _sin_organizar(descargas) == ["foto.jpg", "informe.pdf"]

    def test_la_carpeta_vacia_pide_la_opcion(self, monkeypatch, capsys):
        """``--carpeta ""`` (o solo comillas) es como no ponerla: se explica qué falta."""
        for vacia in ("", '""'):
            codigo, salida = _main(monkeypatch, capsys, "--carpeta", vacia)
            assert codigo == 1
            assert "Falta --carpeta" in salida

    def test_cifrar_una_ruta_vacia_lo_explica(self, monkeypatch, capsys):
        """``--cifrar ""`` no cifra la carpeta actual: dice que falta la ruta."""
        codigo, salida = _main(monkeypatch, capsys, "--cifrar", "", "--archivo-clave", "k.key")
        assert codigo == 1
        assert "Indica qué fichero o carpeta quieres cifrar" in salida

    def test_la_ruta_se_comprueba_antes_de_pedir_la_contrasena(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        """Así no se escribe la contraseña dos veces para luego saber que la ruta no existe."""

        def no_deberia_pedirla(*_args: object) -> str:
            raise AssertionError("pidió la contraseña antes de comprobar la ruta")

        monkeypatch.setattr(comandos, "getpass", no_deberia_pedirla)
        codigo, salida = _main(
            monkeypatch, capsys, "--cifrar", str(tmp_path / "no_existe.txt"), "--contrasena"
        )
        assert codigo == 1
        assert "No existe" in salida

    def test_generar_una_clave_en_una_carpeta_pide_el_nombre(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        """Una carpeta no es un fichero de clave: se pide el nombre en vez de un error confuso."""
        codigo, salida = _main(monkeypatch, capsys, "--generar-clave", str(tmp_path))
        assert codigo == 1
        assert "Es una carpeta: indica también el nombre del fichero" in salida

    def test_generar_una_clave_sin_ruta_lo_explica(self, monkeypatch, capsys):
        """``--generar-clave ""`` dice dónde hay que indicar el fichero."""
        codigo, salida = _main(monkeypatch, capsys, "--generar-clave", "")
        assert codigo == 1
        assert "Indica dónde guardar la clave nueva" in salida

    def test_exportar_con_virgulilla(self, casa: Path, monkeypatch, capsys):
        """El fichero exportado va a la carpeta personal."""
        codigo, _ = _main(monkeypatch, capsys, "--exportar-categorias", '"~/categorias.json"')
        assert codigo == 0
        assert (casa / "categorias.json").is_file()


@pytest.mark.usefixtures("consola")
class TestMenu:
    """Las rutas que se escriben en el menú interactivo."""

    def test_enter_en_blanco_no_organiza_la_carpeta_actual(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        """El fallo que motivó rutas.py: sin carpeta anterior, ENTER organizaba la carpeta
        desde la que se abrió el programa."""
        (tmp_path / "foto.jpg").write_text("x")
        (tmp_path / "informe.pdf").write_text("x")

        # 01, ENTER en la carpeta, pausa, salir
        salida = _menu(monkeypatch, capsys, ["01", "", "", "11"])

        assert "No has escrito ninguna carpeta" in salida
        assert (tmp_path / "foto.jpg").exists() and (tmp_path / "informe.pdf").exists()
        assert not (tmp_path / "Imagenes").exists()
        assert not (tmp_path / "Documentos").exists()

    def test_entre_comillas_y_con_virgulilla(self, casa: Path, monkeypatch, capsys):
        """Una ruta pegada con comillas y ``~`` se encuentra."""
        fotos = casa / "Mis fotos"
        fotos.mkdir()
        (fotos / "a.jpg").write_text("x")

        # 02 (simular), carpeta, ENTER en agrupar/duplicados/config, pausa, salir
        salida = _menu(monkeypatch, capsys, ["02", '"~/Mis fotos"', "", "", "", "", "11"])

        assert "a.jpg -> Imagenes/" in salida
        assert "no existe" not in salida

    def test_cifrar_con_enter_en_blanco_no_cifra_nada(self, tmp_path: Path, monkeypatch, capsys):
        """Antes se iba a cifrar la carpeta actual entera; ahora dice que falta la ruta."""
        (tmp_path / "nota.txt").write_text("secreto")

        # 08, [1] cifrar, ENTER en la ruta, pausa, [4] volver, salir
        salida = _menu(monkeypatch, capsys, ["08", "1", "", "", "4", "11"])

        assert "No has escrito qué fichero o carpeta quieres cifrar" in salida
        assert sorted(p.name for p in tmp_path.iterdir() if p.suffix == ".enc") == []

    def test_un_fichero_de_clave_en_blanco_se_explica(self, tmp_path: Path, monkeypatch, capsys):
        """Antes se volvía al menú sin decir nada."""
        nota = tmp_path / "nota.txt"
        nota.write_text("secreto")

        # 08, [1] cifrar, la nota, [2] fichero de clave, ENTER, pausa, [4] volver, salir
        salida = _menu(monkeypatch, capsys, ["08", "1", str(nota), "2", "", "", "4", "11"])

        assert "No has escrito qué fichero de clave usar" in salida
        assert not nota.with_name("nota.txt.enc").exists()
