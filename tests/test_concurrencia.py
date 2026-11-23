"""Tests de concurrencia del historial de MoverArchivos: varios hilos moviendo, leyendo y
limpiando a la vez.
"""

import threading
from pathlib import Path

import pytest

from file_organizer.mover_archivos import MoverArchivos


@pytest.fixture
def carpetas(tmp_path: Path) -> tuple[Path, Path]:
    """Carpetas de origen y de destino vacías."""
    origen = tmp_path / "origen"
    destino = tmp_path / "destino"
    origen.mkdir()
    destino.mkdir()
    return origen, destino


def test_muchos_movimientos_concurrentes_no_pierden_registros(carpetas):
    """60 hilos mueven un fichero cada uno a la vez: el historial tiene los 60 registros."""
    origen, destino = carpetas
    mover = MoverArchivos(carpeta_origen=origen, carpeta_destino=destino)

    n = 60
    ficheros = []
    for i in range(n):
        f = origen / f"f{i}.txt"
        f.write_text(str(i))
        ficheros.append(f)

    errores: list[Exception] = []
    barrera = threading.Barrier(n)

    def trabajador(f: Path) -> None:
        try:
            barrera.wait()  # maximiza la contención arrancando a la vez
            mover.mover(f, destino, "Documentos")
        except Exception as e:
            errores.append(e)

    hilos = [threading.Thread(target=trabajador, args=(f,)) for f in ficheros]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert not errores
    assert len(mover.obtener_historial()) == n


def test_lectura_concurrente_durante_escritura_no_falla(carpetas):
    """Leer el historial y las estadísticas mientras otro hilo mueve ficheros no lanza errores
    (p. ej. por cambiar una lista mientras se recorre).
    """
    origen, destino = carpetas
    mover = MoverArchivos(carpeta_origen=origen, carpeta_destino=destino)

    n = 50
    ficheros = []
    for i in range(n):
        f = origen / f"f{i}.txt"
        f.write_text(str(i))
        ficheros.append(f)

    errores: list[Exception] = []
    parar = threading.Event()

    def escritor() -> None:
        try:
            for f in ficheros:
                mover.mover(f, destino, "Documentos")
        except Exception as e:
            errores.append(e)
        finally:
            parar.set()

    def lector() -> None:
        try:
            while not parar.is_set():
                # iterar/copiar el historial mientras otro hilo lo modifica
                mover.obtener_historial()
                mover.obtener_estadisticas()
        except Exception as e:
            errores.append(e)

    hilos = [threading.Thread(target=escritor)] + [
        threading.Thread(target=lector) for _ in range(4)
    ]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert not errores
    assert len(mover.obtener_historial()) == n


def test_limpiar_historial_concurrente_no_falla(carpetas):
    """Limpiar el historial una y otra vez mientras otro hilo lo lee no lanza errores y lo deja
    vacío.
    """
    origen, destino = carpetas
    mover = MoverArchivos(carpeta_origen=origen, carpeta_destino=destino)
    for i in range(30):
        f = origen / f"f{i}.txt"
        f.write_text(str(i))
        mover.mover(f, destino, "Documentos")

    errores: list[Exception] = []

    def limpiar() -> None:
        try:
            for _ in range(100):
                mover.limpiar_historial()
        except Exception as e:
            errores.append(e)

    def leer() -> None:
        try:
            for _ in range(100):
                mover.obtener_historial()
        except Exception as e:
            errores.append(e)

    hilos = [threading.Thread(target=limpiar), threading.Thread(target=leer)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert not errores
    assert mover.obtener_historial() == []
