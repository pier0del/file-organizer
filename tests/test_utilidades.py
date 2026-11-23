"""Tests de las utilidades comunes: nombres sin conflicto, checksums, JSON atómico y rutas."""

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from file_organizer import utilidades
from file_organizer.utilidades import (
    calcular_checksum,
    escribir_json_atomico,
    esta_dentro,
    nombres_sin_pisar,
    resolver_conflicto,
    rutas_numeradas,
)


class TestResolverConflicto:
    """Un nombre libre para no pisar un fichero que ya existe."""

    def test_sin_conflicto_devuelve_la_misma_ruta(self, tmp_path: Path):
        """Si está libre, se usa tal cual."""
        destino = tmp_path / "libre.txt"
        assert resolver_conflicto(destino) == destino

    def test_con_conflicto_anade_sufijo(self, tmp_path: Path):
        """Si está ocupado, ``doc_1.txt``."""
        (tmp_path / "doc.txt").write_text("x")
        assert resolver_conflicto(tmp_path / "doc.txt") == tmp_path / "doc_1.txt"

    def test_incrementa_hasta_hueco_libre(self, tmp_path: Path):
        """Si también lo están ``_1`` y ``_2``, ``doc_3.txt``."""
        for nombre in ("doc.txt", "doc_1.txt", "doc_2.txt"):
            (tmp_path / nombre).write_text("x")
        assert resolver_conflicto(tmp_path / "doc.txt") == tmp_path / "doc_3.txt"

    def test_conserva_la_extension(self, tmp_path: Path):
        """El número va antes de la extensión, que no cambia."""
        (tmp_path / "foto.jpeg").write_text("x")
        resultado = resolver_conflicto(tmp_path / "foto.jpeg")
        assert resultado.suffix == ".jpeg"


class TestCalcularChecksum:
    """El SHA-256 del contenido de un fichero."""

    def test_mismo_contenido_mismo_hash(self, tmp_path: Path):
        """Dos ficheros iguales dan el mismo."""
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_bytes(b"contenido identico")
        b.write_bytes(b"contenido identico")
        assert calcular_checksum(a) == calcular_checksum(b)

    def test_distinto_contenido_distinto_hash(self, tmp_path: Path):
        """Dos ficheros distintos dan uno distinto."""
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_bytes(b"uno")
        b.write_bytes(b"dos")
        assert calcular_checksum(a) != calcular_checksum(b)

    def test_es_sha256_hex(self, tmp_path: Path):
        """Son 64 caracteres hexadecimales en minúscula."""
        f = tmp_path / "f.txt"
        f.write_bytes(b"hola")
        h = calcular_checksum(f)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_funciona_con_ficheros_grandes(self, tmp_path: Path):
        """Un fichero mayor que el bloque de lectura se calcula entero."""
        f = tmp_path / "grande.bin"
        f.write_bytes(b"a" * (256 * 1024))
        assert len(calcular_checksum(f)) == 64


class TestNombresSinPisar:
    """Los nombres que se van probando hasta dar con uno libre."""

    def test_primero_el_nombre_tal_cual_y_luego_desde_el_2(self, tmp_path: Path):
        """Para los backups: el nombre tal cual y luego ``_2``, ``_3``..."""
        candidatos = nombres_sin_pisar(tmp_path / "backup_20260102_030405.zip")
        assert [next(candidatos).name for _ in range(3)] == [
            "backup_20260102_030405.zip",
            "backup_20260102_030405_2.zip",
            "backup_20260102_030405_3.zip",
        ]

    def test_en_un_cifrado_el_numero_va_antes_de_la_extension_original(self, tmp_path: Path):
        """En ``foto.jpg.enc`` el número va antes de ``.jpg``, para no perderla al descifrar."""
        candidatos = rutas_numeradas(tmp_path / "foto.jpg.enc", desde=1)
        assert next(candidatos).name == "foto_1.jpg.enc"


class TestEscribirJsonAtomico:
    """Escribir un JSON sin dejarlo nunca a medias."""

    def test_escribe_el_json_y_no_deja_el_temporal(self, tmp_path: Path):
        """Crea las carpetas que falten, conserva las tildes, no deja el temporal y aplica los
        permisos.
        """
        ruta = tmp_path / "sub" / "datos.json"

        escribir_json_atomico(ruta, {"clave": "ñandú"}, permisos=0o600)

        assert json.loads(ruta.read_text(encoding="utf-8")) == {"clave": "ñandú"}
        assert "ñandú" in ruta.read_text(encoding="utf-8")  # sin escapar
        assert not ruta.with_suffix(".json.tmp").exists()
        if os.name == "posix":
            assert ruta.stat().st_mode & 0o777 == 0o600

    @pytest.mark.skipif(sys.platform == "win32", reason="permisos POSIX no aplican en windows")
    def test_el_temporal_ya_se_crea_con_los_permisos(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Antes de escribir nada el temporal ya es privado, aunque quedara uno con otros permisos
        de una escritura cortada.
        """
        ruta = tmp_path / "datos.json"
        temporal = ruta.with_suffix(".json.tmp")
        temporal.write_text("de una escritura cortada")
        temporal.chmod(0o644)
        modos: list[int] = []
        volcar = json.dump

        def volcar_mirando(datos, f, **opciones):
            modos.append(stat.S_IMODE(temporal.stat().st_mode))  # antes de escribir nada
            volcar(datos, f, **opciones)

        monkeypatch.setattr(utilidades.json, "dump", volcar_mirando)
        escribir_json_atomico(ruta, {"a": 1}, permisos=0o600)

        assert modos == [0o600]
        assert json.loads(ruta.read_text(encoding="utf-8")) == {"a": 1}


class TestEstaDentro:
    """Si una ruta está dentro de una carpeta."""

    def test_dentro_fuera_y_sin_carpeta(self, tmp_path: Path):
        """Un ``..`` saca la ruta de la carpeta; sin carpeta, nunca está dentro."""
        assert esta_dentro(tmp_path / "a" / "b.txt", tmp_path)
        assert not esta_dentro(tmp_path / ".." / "fuera.txt", tmp_path)
        assert not esta_dentro(tmp_path / "a.txt", None)
