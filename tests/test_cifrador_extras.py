"""Tests del cifrador sin estado y de sus operaciones sobre carpetas: progreso y errores."""

import inspect
from pathlib import Path

import pytest

from file_organizer.cifrador import SUFIJO_CIFRADO, AccionCifrado, Cifrador


def _poblar(carpeta: Path, n: int) -> None:
    """Crea ``n`` ficheros de texto distintos en la carpeta."""
    for i in range(n):
        (carpeta / f"doc{i}.txt").write_text(f"contenido {i}")


class TestDisenoSinEstado:
    """El cifrador no guarda estado: se usa sin crear una instancia."""

    @pytest.mark.parametrize(
        "metodo",
        [
            "cifrar_archivo",
            "descifrar_archivo",
            "procesar_carpeta",
            "generar_clave",
        ],
    )
    def test_metodos_publicos_son_estaticos(self, metodo: str):
        """Cada operación es un método estático."""
        assert isinstance(inspect.getattr_static(Cifrador, metodo), staticmethod)

    def test_se_puede_llamar_sin_instancia(self, tmp_path: Path):
        """Se puede cifrar llamando a la clase directamente."""
        f = tmp_path / "secreto.txt"
        f.write_text("hola")
        r = Cifrador.cifrar_archivo(f, contrasena="ClaveSegura123")
        assert r.ruta.suffix == SUFIJO_CIFRADO


class TestProgresoPorLotes:
    """El aviso de progreso al cifrar o descifrar una carpeta."""

    def test_cifrar_carpeta_reporta_progreso(self, tmp_path: Path):
        """Al cifrar, se avisa tras cada fichero con cuántos van y el total."""
        _poblar(tmp_path, 4)
        llamadas: list[tuple[int, int]] = []
        Cifrador.procesar_carpeta(
            tmp_path,
            AccionCifrado.CIFRAR,
            contrasena="Clave123",
            progreso=lambda actual, total: llamadas.append((actual, total)),
        )
        assert llamadas == [(1, 4), (2, 4), (3, 4), (4, 4)]

    def test_descifrar_carpeta_reporta_progreso(self, tmp_path: Path):
        """Al descifrar, igual."""
        _poblar(tmp_path, 3)
        Cifrador.procesar_carpeta(
            tmp_path, AccionCifrado.CIFRAR, contrasena="Clave123", borrar_original=True
        )
        llamadas: list[tuple[int, int]] = []
        Cifrador.procesar_carpeta(
            tmp_path,
            AccionCifrado.DESCIFRAR,
            contrasena="Clave123",
            progreso=lambda actual, total: llamadas.append((actual, total)),
        )
        assert llamadas == [(1, 3), (2, 3), (3, 3)]

    def test_carpeta_vacia_no_reporta_progreso(self, tmp_path: Path):
        """Con la carpeta vacía no se avisa nunca."""
        llamadas = []
        Cifrador.procesar_carpeta(
            tmp_path,
            AccionCifrado.CIFRAR,
            contrasena="Clave123",
            progreso=lambda a, t: llamadas.append((a, t)),
        )
        assert llamadas == []


class TestLoteCapturaErrores:
    """Un fallo con un fichero no corta la carpeta entera."""

    def test_contrasena_incorrecta_no_aborta_el_lote(self, tmp_path: Path):
        """Con la contraseña mal fallan todos, pero se intentan todos y se cuentan."""
        _poblar(tmp_path, 3)
        Cifrador.procesar_carpeta(
            tmp_path, AccionCifrado.CIFRAR, contrasena="Correcta", borrar_original=True
        )
        resultado = Cifrador.procesar_carpeta(
            tmp_path, AccionCifrado.DESCIFRAR, contrasena="Incorrecta"
        )
        # Todos fallan por contraseña errónea, pero el lote termina y los cuenta.
        assert resultado.fallidos == 3
        assert resultado.exitosos == 0
