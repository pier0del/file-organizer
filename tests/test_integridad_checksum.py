"""Tests del SHA-256 que anota el historial al mover y de cómo se usa al deshacer."""

from pathlib import Path

import pytest

from file_organizer.mover_archivos import MoverArchivos
from file_organizer.utilidades import calcular_checksum


@pytest.fixture
def mover(tmp_path: Path) -> MoverArchivos:
    """Movedor de una carpeta que se organiza en ella misma."""
    return MoverArchivos(carpeta_origen=tmp_path, carpeta_destino=tmp_path)


def _crear(tmp_path: Path, nombre: str, contenido: bytes = b"datos") -> Path:
    """Crea un fichero con ese contenido."""
    f = tmp_path / nombre
    f.write_bytes(contenido)
    return f


class TestChecksumEnMovimiento:
    """El SHA-256 que se anota al mover un fichero."""

    def test_mover_guarda_checksum(self, mover: MoverArchivos, tmp_path: Path):
        """El registro lleva el SHA-256 del fichero movido."""
        f = _crear(tmp_path, "doc.pdf", b"contenido")
        esperado = calcular_checksum(f)
        resultado = mover.mover(f, tmp_path, "Documentos")
        assert resultado.registro.checksum == esperado

    def test_simulacion_no_calcula_checksum(self, tmp_path: Path):
        """Simulando no se lee ningún fichero: el registro no lleva SHA-256."""
        mover = MoverArchivos(
            modo_simulacion=True, carpeta_origen=tmp_path, carpeta_destino=tmp_path
        )
        f = _crear(tmp_path, "doc.pdf")
        resultado = mover.mover(f, tmp_path, "Documentos")
        assert resultado.registro.checksum is None


class TestPersistenciaChecksum:
    """El SHA-256 en el fichero del historial."""

    def test_checksum_sobrevive_guardar_cargar(self, mover: MoverArchivos, tmp_path: Path):
        """Se guarda con el historial y otra instancia lo lee igual."""
        f = _crear(tmp_path, "doc.pdf", b"abc")
        esperado = calcular_checksum(f)
        mover.mover(f, tmp_path, "Documentos")
        mover.guardar_historial()

        otro = MoverArchivos(carpeta_origen=tmp_path, carpeta_destino=tmp_path)
        otro.cargar_historial()
        assert otro.obtener_historial()[0].checksum == esperado

    def test_historial_antiguo_sin_checksum_carga_none(self, mover: MoverArchivos, tmp_path: Path):
        """Un historial de una versión anterior, sin SHA-256, se carga con None."""
        import json

        f = _crear(tmp_path, "doc.pdf")
        mover.mover(f, tmp_path, "Documentos")
        mover.guardar_historial()

        # Simula un historial de una version antigua sin la clave 'checksum'.
        ruta = mover.ruta_historial
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        for operacion in datos["operaciones"]:
            for r in operacion["registros"]:
                r.pop("checksum", None)
        ruta.write_text(json.dumps(datos), encoding="utf-8")

        otro = MoverArchivos(carpeta_origen=tmp_path, carpeta_destino=tmp_path)
        otro.cargar_historial()
        assert otro.obtener_historial()[0].checksum is None


class TestIntegridadEnDeshacer:
    """Qué pasa al deshacer si el fichero cambió."""

    def test_contenido_cambiado_cuenta_como_integridad_fallida_pero_restaura(
        self, mover: MoverArchivos, tmp_path: Path
    ):
        """Si se editó tras organizarlo, se restaura igual y se cuenta como cambiado."""
        f = _crear(tmp_path, "doc.pdf", b"original")
        resultado = mover.mover(f, tmp_path, "Documentos")
        resultado.registro.destino.write_bytes(b"cambiado")

        resultado_deshacer = mover.deshacer_todo(carpeta_base=tmp_path)
        assert resultado_deshacer.exitosos == 1
        assert resultado_deshacer.integridad_fallida == 1
        assert (tmp_path / "doc.pdf").exists()

    def test_sin_cambios_no_marca_integridad(self, mover: MoverArchivos, tmp_path: Path):
        """Si nadie lo tocó, no se cuenta ningún cambio."""
        f = _crear(tmp_path, "doc.pdf", b"original")
        mover.mover(f, tmp_path, "Documentos")
        resultado_deshacer = mover.deshacer_todo(carpeta_base=tmp_path)
        assert resultado_deshacer.integridad_fallida == 0
