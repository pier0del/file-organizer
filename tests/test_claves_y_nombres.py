"""Tests de dos protecciones del cifrado: no sobrescribir una clave que ya existe, y no perder
la extensión original cuando un fichero cifrado tiene que cambiar de nombre."""

import re
import sys
from pathlib import Path

import pytest

from file_organizer.cifrador import Cifrador
from file_organizer.cli import aplicacion
from file_organizer.excepciones import ErrorArchivoClaveYaExiste
from file_organizer.utilidades import resolver_conflicto


class TestNoSobrescribirLaClave:
    """Generar una clave encima de otra destruiría lo cifrado con ella: no se hace."""

    def test_generar_una_clave_donde_ya_hay_una_falla_y_no_la_toca(self, tmp_path: Path):
        """Falla con ErrorArchivoClaveYaExiste y la clave queda igual, byte a byte."""
        clave = Cifrador.generar_clave(tmp_path / "mia.key")
        original = clave.read_bytes()

        with pytest.raises(ErrorArchivoClaveYaExiste, match="no se sobrescribe"):
            Cifrador.generar_clave(clave)

        assert clave.read_bytes() == original

    def test_lo_cifrado_con_la_clave_se_sigue_pudiendo_descifrar(self, tmp_path: Path):
        """Tras el intento fallido, lo que se cifró con esa clave se sigue descifrando."""
        clave = Cifrador.generar_clave(tmp_path / "mia.key")
        (tmp_path / "secreto.txt").write_text("hola")
        cifrado = Cifrador.cifrar_archivo(
            tmp_path / "secreto.txt", ruta_clave=clave, borrar_original=True
        ).ruta

        with pytest.raises(ErrorArchivoClaveYaExiste):
            Cifrador.generar_clave(clave)

        descifrado = Cifrador.descifrar_archivo(cifrado, ruta_clave=clave).ruta
        assert descifrado.read_text() == "hola"

    def test_sobrescribir_solo_si_se_pide(self, tmp_path: Path):
        """Con ``sobrescribir`` (la GUI lo pasa tras confirmar) sí se crea una clave nueva."""
        clave = Cifrador.generar_clave(tmp_path / "mia.key")
        original = clave.read_bytes()

        Cifrador.generar_clave(clave, sobrescribir=True)

        assert clave.read_bytes() != original

    def test_la_consola_avisa_y_termina_con_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        """``--generar-clave`` sobre una existente avisa una sola vez, sale con 1 y no dice «Clave
        generada».
        """
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("FILE_ORGANIZER_CONFIG", str(tmp_path / "uc.json"))
        clave = Cifrador.generar_clave(tmp_path / "mia.key")
        original = clave.read_bytes()
        monkeypatch.setattr(sys, "argv", ["file-organizer", "--generar-clave", str(clave)])

        assert aplicacion.main() == 1

        salida = re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)
        assert salida.count("Ya existe el fichero de clave") == 1
        assert "Clave generada" not in salida
        assert clave.read_bytes() == original


class TestNombresDeFicherosCifrados:
    """Al evitar un conflicto de nombres, un ``.enc`` conserva su extensión original."""

    def test_el_numero_va_antes_de_la_extension_original(self, tmp_path: Path):
        """``foto.jpg.enc`` ocupado da ``foto_1.jpg.enc`` y no ``foto.jpg_1.enc``, que al descifrar
        perdería el ``.jpg``.
        """
        (tmp_path / "foto.jpg.enc").write_text("x")
        assert resolver_conflicto(tmp_path / "foto.jpg.enc").name == "foto_1.jpg.enc"

        (tmp_path / "foto_1.jpg.enc").write_text("x")
        assert resolver_conflicto(tmp_path / "foto.jpg.enc").name == "foto_2.jpg.enc"

    @pytest.mark.parametrize(
        ("existente", "esperado"),
        [
            ("nota.enc", "nota_1.enc"),  # cifrado sin extensión original
            ("foto.jpg", "foto_1.jpg"),  # fichero normal: como siempre
        ],
    )
    def test_el_resto_de_nombres_no_cambia(self, tmp_path: Path, existente: str, esperado: str):
        """Un ``.enc`` sin extensión original y un fichero normal se numeran como siempre."""
        (tmp_path / existente).write_text("x")
        assert resolver_conflicto(tmp_path / existente).name == esperado

    def test_cifrar_dos_veces_y_descifrar_conserva_la_extension(self, tmp_path: Path):
        """Cifrar dos veces el mismo fichero da ``foto_1.jpg.enc``, que se descifra en
        ``foto_1.jpg``.
        """
        foto = tmp_path / "foto.jpg"
        foto.write_text("A")
        Cifrador.cifrar_archivo(foto, contrasena="x")

        segundo = Cifrador.cifrar_archivo(foto, contrasena="x").ruta
        assert segundo.name == "foto_1.jpg.enc"

        foto.unlink()
        descifrado = Cifrador.descifrar_archivo(segundo, contrasena="x").ruta
        assert descifrado.name == "foto_1.jpg"
        assert descifrado.read_text() == "A"
