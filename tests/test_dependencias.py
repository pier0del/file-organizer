"""Tests de la inversión de dependencias: el organizador y el vigilante funcionan con los
colaboradores que se les pasen, no solo con los que crean ellos."""

import threading
import time
from pathlib import Path, PurePath

from file_organizer.bloqueo import BloqueoCarpeta
from file_organizer.clasificador import Clasificador
from file_organizer.ignorados import ListaIgnorados
from file_organizer.mover_archivos import MoverArchivos
from file_organizer.organizador import OrganizadorArchivos
from file_organizer.vigilante import VigilanteCarpeta


class ClasificadorPorInicial:
    """Otra forma de clasificar, sin heredar de Clasificador: por la inicial del nombre."""

    def clasificar(self, ruta_archivo: Path) -> str:
        """La inicial del nombre, en mayúscula."""
        return ruta_archivo.name[0].upper()

    def es_carpeta_de_categoria(self, carpeta: PurePath) -> bool:
        """Una carpeta de primer nivel cuyo nombre es una sola letra mayúscula."""
        return len(carpeta.parts) == 1 and len(carpeta.name) == 1 and carpeta.name.isupper()


def _ficheros(carpeta: Path, *nombres: str) -> Path:
    """Crea la carpeta con esos ficheros, cada uno con su nombre como contenido."""
    carpeta.mkdir(parents=True, exist_ok=True)
    for nombre in nombres:
        (carpeta / nombre).write_text(nombre)
    return carpeta


def test_por_defecto_crea_los_colaboradores_de_siempre(tmp_path: Path):
    """Sin pasarle nada, el organizador crea su clasificador,
    lista de ignorados, movedor y bloqueo.
    """
    org = OrganizadorArchivos(carpeta_origen=tmp_path)
    assert isinstance(org.clasificador, Clasificador)
    assert isinstance(org.lista_ignorados, ListaIgnorados)
    assert isinstance(org.mover, MoverArchivos)
    assert isinstance(org.bloqueo, BloqueoCarpeta)


def test_otra_forma_de_clasificar_organiza_y_se_deshace(tmp_path: Path):
    """Con un clasificador propio organiza, cuenta las estadísticas y reconoce sus carpetas; el
    deshacer, sin saber cómo se clasificó, lo devuelve todo.
    """
    carpeta = _ficheros(tmp_path / "c", "agua.txt", "beta.jpg")
    org = OrganizadorArchivos(carpeta_origen=carpeta, clasificador=ClasificadorPorInicial())

    assert org.ejecutar() == 2
    assert (carpeta / "A" / "agua.txt").exists() and (carpeta / "B" / "beta.jpg").exists()
    assert org.estadisticas() == {"A": 1, "B": 1}
    assert org.esta_en_carpeta_de_categoria(carpeta / "A" / "agua.txt")
    assert not org.esta_en_carpeta_de_categoria(carpeta / "agua.txt")

    resultado = OrganizadorArchivos(carpeta_origen=carpeta).deshacer()
    assert resultado is not None and resultado.exitosos == 2
    assert sorted(p.name for p in carpeta.iterdir() if p.is_file() and p.name[0] != ".") == [
        "agua.txt",
        "beta.jpg",
    ]


def test_el_vigilante_usa_el_organizador_que_se_le_pasa(tmp_path: Path):
    """El vigilante organiza los ficheros nuevos con el clasificador del organizador que recibe."""
    carpeta = tmp_path / "v"
    carpeta.mkdir()
    org = OrganizadorArchivos(carpeta_origen=carpeta, clasificador=ClasificadorPorInicial())
    vigilante = VigilanteCarpeta(carpeta=carpeta, espera=0.1, organizador=org)
    assert vigilante.organizador is org

    hilo = threading.Thread(target=vigilante.iniciar, daemon=True)
    hilo.start()
    try:
        limite = time.monotonic() + 10
        while not vigilante.observador.is_alive() and time.monotonic() < limite:
            time.sleep(0.02)
        (carpeta / "zeta.pdf").write_text("z")
        while not (carpeta / "Z" / "zeta.pdf").exists() and time.monotonic() < limite:
            time.sleep(0.05)
        assert (carpeta / "Z" / "zeta.pdf").exists()
    finally:
        vigilante.detener()
        hilo.join(timeout=10)
