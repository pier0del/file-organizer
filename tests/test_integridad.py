"""Tests de los modos de integridad (completa, híbrida y rápida) y de su elección."""

import os
import sys
from pathlib import Path

import pytest

from file_organizer import integridad
from file_organizer.cli import aplicacion
from file_organizer.config_usuario import ConfiguracionUsuario
from file_organizer.integridad import (
    DESCRIPCIONES_INTEGRIDAD,
    MODO_INTEGRIDAD_DEFECTO,
    TOLERANCIA_FECHA_NS,
    ModoIntegridad,
    calcular_huella,
    ha_cambiado,
)
from file_organizer.mover_archivos import MoverArchivos
from file_organizer.organizador import OrganizadorArchivos
from file_organizer.utilidades import calcular_checksum

UMBRAL_PRUEBA = 100  # bytes: "grande" a partir de aquí en el modo híbrido


@pytest.fixture(autouse=True)
def _umbral_pequeno(monkeypatch: pytest.MonkeyPatch) -> None:
    """Baja el umbral del modo híbrido a 100 bytes, para no crear ficheros de verdad grandes."""
    monkeypatch.setattr(integridad, "UMBRAL_HIBRIDO_BYTES", UMBRAL_PRUEBA)


@pytest.fixture
def lecturas(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Registra cada vez que se lee un fichero entero para calcular su SHA-256."""
    leidos: list[Path] = []

    def checksum_contado(ruta: Path) -> str:
        leidos.append(ruta)
        return calcular_checksum(ruta)

    monkeypatch.setattr(integridad, "calcular_checksum", checksum_contado)
    return leidos


def _crear(tmp_path: Path, nombre: str, tamano: int) -> Path:
    """Crea un fichero de ``tamano`` bytes."""
    ruta = tmp_path / nombre
    ruta.write_bytes(b"x" * tamano)
    return ruta


class TestCalcularHuella:
    """Qué guarda cada modo de un fichero y si tiene que leerlo entero."""

    def test_completa_calcula_sha256_siempre(self, tmp_path: Path, lecturas: list[Path]):
        """La completa calcula el SHA-256 aunque el fichero sea grande."""
        grande = _crear(tmp_path, "grande.bin", UMBRAL_PRUEBA * 10)
        huella = calcular_huella(grande, ModoIntegridad.COMPLETA)
        assert huella.checksum == calcular_checksum(grande)
        assert lecturas == [grande]

    def test_hibrida_calcula_sha256_solo_hasta_el_umbral(
        self, tmp_path: Path, lecturas: list[Path]
    ):
        """La híbrida lee los pequeños; de los grandes guarda solo tamaño y fecha, sin leerlos."""
        pequeno = _crear(tmp_path, "pequeno.txt", UMBRAL_PRUEBA)
        grande = _crear(tmp_path, "grande.bin", UMBRAL_PRUEBA + 1)

        assert calcular_huella(pequeno, ModoIntegridad.HIBRIDA).checksum is not None
        huella_grande = calcular_huella(grande, ModoIntegridad.HIBRIDA)

        assert huella_grande.checksum is None
        assert huella_grande.tamano == UMBRAL_PRUEBA + 1
        assert lecturas == [pequeno]  # el grande no se ha leído

    def test_rapida_no_lee_el_contenido(self, tmp_path: Path, lecturas: list[Path]):
        """La rápida guarda tamaño y fecha sin leer nunca el contenido."""
        pequeno = _crear(tmp_path, "pequeno.txt", 10)
        huella = calcular_huella(pequeno, ModoIntegridad.RAPIDA)
        assert huella.checksum is None
        assert huella.tamano == 10
        assert huella.mtime_ns == pequeno.stat().st_mtime_ns
        assert lecturas == []


class TestHaCambiado:
    """Si un fichero cambió desde que se guardó su huella."""

    def test_sin_cambios(self, tmp_path: Path):
        """Un fichero sin tocar no ha cambiado, en ningún modo."""
        f = _crear(tmp_path, "a.txt", 10)
        for modo in ModoIntegridad:
            h = calcular_huella(f, modo)
            assert ha_cambiado(f, h.checksum, h.tamano, h.mtime_ns) is False

    def test_cambio_de_tamano_se_detecta_sin_leer(self, tmp_path: Path, lecturas: list[Path]):
        """Si cambia el tamaño, se sabe sin calcular el SHA-256."""
        f = _crear(tmp_path, "a.txt", 10)
        h = calcular_huella(f, ModoIntegridad.COMPLETA)
        lecturas.clear()
        f.write_bytes(b"y" * 20)
        assert ha_cambiado(f, h.checksum, h.tamano, h.mtime_ns) is True
        assert lecturas == []

    def test_completa_detecta_cambio_de_contenido_con_misma_fecha(self, tmp_path: Path):
        """Con el mismo tamaño y la misma fecha, solo el SHA-256 ve el cambio."""
        f = _crear(tmp_path, "a.txt", 10)
        h = calcular_huella(f, ModoIntegridad.COMPLETA)
        f.write_bytes(b"y" * 10)  # mismo tamaño
        os.utime(f, ns=(h.mtime_ns, h.mtime_ns))  # y misma fecha: solo el SHA-256 lo ve
        assert ha_cambiado(f, h.checksum, h.tamano, h.mtime_ns) is True

    def test_rapida_detecta_edicion_por_la_fecha(self, tmp_path: Path):
        """Sin SHA-256, una edición con el mismo tamaño se ve por la fecha."""
        f = _crear(tmp_path, "a.txt", 10)
        h = calcular_huella(f, ModoIntegridad.RAPIDA)
        f.write_bytes(b"y" * 10)
        nueva = h.mtime_ns + 60 * 1_000_000_000
        os.utime(f, ns=(nueva, nueva))
        assert ha_cambiado(f, h.checksum, h.tamano, h.mtime_ns) is True

    def test_rapida_tolera_el_redondeo_de_fecha_de_fat(self, tmp_path: Path):
        """Una diferencia de fecha menor que la tolerancia (FAT guarda de 2 en 2 s) no cuenta como
        cambio.
        """
        f = _crear(tmp_path, "a.txt", 10)
        h = calcular_huella(f, ModoIntegridad.RAPIDA)
        redondeada = h.mtime_ns + TOLERANCIA_FECHA_NS // 2
        os.utime(f, ns=(redondeada, redondeada))
        assert ha_cambiado(f, h.checksum, h.tamano, h.mtime_ns) is False

    def test_registro_antiguo_sin_huella(self, tmp_path: Path):
        """Sin nada con qué comparar (historial antiguo), la respuesta es None: no se sabe."""
        f = _crear(tmp_path, "a.txt", 10)
        assert ha_cambiado(f, None, None, None) is None


class TestModosAlOrganizar:
    """Los modos de integridad al organizar y deshacer de verdad."""

    def test_rapida_organiza_y_deshace_sin_leer_contenido(
        self, tmp_path: Path, lecturas: list[Path]
    ):
        """Con la rápida, organizar y deshacer vídeos no lee ninguno entero."""
        for i in range(3):
            _crear(tmp_path, f"video{i}.mp4", UMBRAL_PRUEBA * 50)

        OrganizadorArchivos(
            carpeta_origen=tmp_path, modo_integridad=ModoIntegridad.RAPIDA
        ).ejecutar()
        resultado = OrganizadorArchivos(carpeta_origen=tmp_path).deshacer()

        assert resultado is not None and resultado.exitosos == 3
        assert lecturas == []

    def test_hibrida_no_lee_los_ficheros_grandes(self, tmp_path: Path, lecturas: list[Path]):
        """Por defecto (híbrida) solo se lee entero el fichero pequeño."""
        _crear(tmp_path, "notas.txt", 10)
        _crear(tmp_path, "video.mp4", UMBRAL_PRUEBA * 50)

        OrganizadorArchivos(carpeta_origen=tmp_path).ejecutar()

        assert [p.name for p in lecturas] == ["notas.txt"]

    @pytest.mark.parametrize("modo", list(ModoIntegridad))
    def test_cada_modo_detecta_una_edicion_al_deshacer(self, tmp_path: Path, modo: ModoIntegridad):
        """En cualquier modo, un fichero editado tras organizarlo se restaura y se cuenta como
        cambiado.
        """
        origen, destino = tmp_path / "o", tmp_path / "d"
        origen.mkdir()
        destino.mkdir()
        f = _crear(origen, "a.txt", 10)
        mover = MoverArchivos(carpeta_origen=origen, carpeta_destino=destino, modo_integridad=modo)
        movido = mover.mover(f, destino, "Documentos").registro
        assert movido is not None

        movido.destino.write_bytes(b"editado, con otro tamano")
        resultado = mover.deshacer_todo(carpeta_base=destino)

        assert resultado.exitosos == 1
        assert resultado.integridad_fallida == 1

    def test_la_huella_se_guarda_y_se_recupera(self, tmp_path: Path):
        """La huella de un fichero grande (sin SHA-256) se guarda en el historial y se lee igual."""
        f = _crear(tmp_path, "grande.bin", UMBRAL_PRUEBA * 2)
        mover = MoverArchivos(carpeta_origen=tmp_path, carpeta_destino=tmp_path)
        mover.mover(f, tmp_path, "Otros")
        mover.guardar_historial()

        otro = MoverArchivos(carpeta_origen=tmp_path, carpeta_destino=tmp_path)
        otro.cargar_historial()
        registro = otro.obtener_historial()[0]
        assert registro.checksum is None  # híbrida: grande → huella rápida
        assert registro.tamano == UMBRAL_PRUEBA * 2
        assert registro.mtime_ns is not None


class TestEleccionDelModo:
    """Qué modo se usa y cómo se elige desde la consola."""

    def test_hay_tres_modos_con_titulo_resumen_y_detalle(self):
        """Cada modo tiene su descripción completa para la ayuda y los menús."""
        assert set(DESCRIPCIONES_INTEGRIDAD) == set(ModoIntegridad)
        for descripcion in DESCRIPCIONES_INTEGRIDAD.values():
            assert descripcion.titulo and descripcion.resumen and descripcion.detalle

    def test_por_defecto_es_la_hibrida(self, tmp_path: Path):
        """Sin elegir nada, ni en las preferencias, se usa la híbrida."""
        assert MODO_INTEGRIDAD_DEFECTO is ModoIntegridad.HIBRIDA
        assert ConfiguracionUsuario(tmp_path / "c.json").cargar().modo_integridad() is (
            ModoIntegridad.HIBRIDA
        )

    def test_valor_guardado_invalido_usa_el_de_por_defecto(self, tmp_path: Path):
        """Un modo desconocido en las preferencias no rompe nada: se usa el de por defecto."""
        ruta = tmp_path / "c.json"
        ruta.write_text('{"modo_integridad": "inventado"}', encoding="utf-8")
        assert ConfiguracionUsuario(ruta).cargar().modo_integridad() is MODO_INTEGRIDAD_DEFECTO

    def test_cli_integridad_rapida(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """``--integridad rapida`` organiza sin guardar ningún SHA-256."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("FILE_ORGANIZER_CONFIG", str(tmp_path / "uc.json"))
        carpeta = tmp_path / "descargas"
        carpeta.mkdir()
        _crear(carpeta, "notas.txt", 10)
        monkeypatch.setattr(
            sys, "argv", ["file-organizer", "--carpeta", str(carpeta), "--integridad", "rapida"]
        )

        assert aplicacion.main() == 0

        org = OrganizadorArchivos(carpeta_origen=carpeta)
        org.mover.cargar_historial()
        assert [r.checksum for r in org.mover.obtener_historial()] == [None]

    def test_cli_sin_flag_usa_el_modo_guardado(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Sin ``--integridad``, la consola usa el modo guardado en las preferencias."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("FILE_ORGANIZER_CONFIG", str(tmp_path / "uc.json"))
        cfg = ConfiguracionUsuario().cargar()
        cfg.establecer("modo_integridad", "rapida")
        cfg.guardar()
        carpeta = tmp_path / "descargas"
        carpeta.mkdir()
        _crear(carpeta, "notas.txt", 10)
        monkeypatch.setattr(sys, "argv", ["file-organizer", "--carpeta", str(carpeta)])

        assert aplicacion.main() == 0

        org = OrganizadorArchivos(carpeta_origen=carpeta)
        org.mover.cargar_historial()
        assert [r.checksum for r in org.mover.obtener_historial()] == [None]
