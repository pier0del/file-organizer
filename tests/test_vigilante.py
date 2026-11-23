"""Tests del vigilante: qué ficheros mira, el modo recursivo, su funcionamiento real con
watchdog y cómo continúa el historial que ya hay.
"""

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from watchdog.events import FileCreatedEvent, FileDeletedEvent, FileMovedEvent

from file_organizer.ignorados import NOMBRE_ARCHIVO_IGNORE
from file_organizer.mover_archivos import NOMBRE_ARCHIVO_HISTORIAL
from file_organizer.organizador import OrganizadorArchivos
from file_organizer.vigilante import (
    ManejadorEventosCarpeta,
    VigilanteCarpeta,
)


def _noop(nivel: str, mensaje: str) -> None:
    """Notificador que no hace nada con los avisos."""


@pytest.fixture
def manejador(tmp_path: Path) -> ManejadorEventosCarpeta:
    """Manejador de eventos de una carpeta organizada por tipo."""
    org = OrganizadorArchivos(carpeta_origen=tmp_path)
    return ManejadorEventosCarpeta(org, segundos_espera=1, notificador=_noop)


class TestDeberiaIgnorar:
    """Qué ficheros nuevos no llega a mirar el vigilante."""

    def test_ficheros_ocultos(self, manejador: ManejadorEventosCarpeta, tmp_path: Path):
        """Los que empiezan por punto."""
        assert manejador._deberia_ignorar(tmp_path / ".oculto")

    def test_ficheros_temporales(self, manejador: ManejadorEventosCarpeta, tmp_path: Path):
        """Las descargas a medias, como ``.crdownload``."""
        assert manejador._deberia_ignorar(tmp_path / "descarga.crdownload")

    def test_fichero_normal_no_se_ignora(self, manejador: ManejadorEventosCarpeta, tmp_path: Path):
        """Una foto normal sí se mira."""
        assert not manejador._deberia_ignorar(tmp_path / "foto.jpg")

    def test_respeta_la_lista_de_ignorados(self, tmp_path: Path):
        """Lo que dice ``.file-organizer-ignore``."""
        (tmp_path / NOMBRE_ARCHIVO_IGNORE).write_text("*.log\n", encoding="utf-8")
        org = OrganizadorArchivos(carpeta_origen=tmp_path)
        manejador = ManejadorEventosCarpeta(org, segundos_espera=1, notificador=_noop)
        assert manejador._deberia_ignorar(tmp_path / "salida.log")


class TestYaOrganizado:
    """Qué ficheros cuentan como ya organizados al vigilar subcarpetas."""

    def test_fichero_en_carpeta_de_categoria_se_ignora(
        self, manejador: ManejadorEventosCarpeta, tmp_path: Path
    ):
        """Lo que está en una carpeta de categoría ya está organizado."""
        destino = tmp_path / "Imagenes"
        destino.mkdir()
        foto = destino / "foto.jpg"
        foto.write_text("x")
        assert manejador._ya_organizado(foto)

    def test_fichero_en_raiz_no_esta_organizado(
        self, manejador: ManejadorEventosCarpeta, tmp_path: Path
    ):
        """Lo que está en la raíz no."""
        foto = tmp_path / "foto.jpg"
        foto.write_text("x")
        assert not manejador._ya_organizado(foto)

    def test_carpeta_no_categoria_no_cuenta(
        self, manejador: ManejadorEventosCarpeta, tmp_path: Path
    ):
        """Lo que está en una carpeta cualquiera del usuario tampoco."""
        otra = tmp_path / "CarpetaCualquiera"
        otra.mkdir()
        f = otra / "x.jpg"
        f.write_text("x")
        assert not manejador._ya_organizado(f)


class TestVigilanteRecursivo:
    """La opción de vigilar también las subcarpetas."""

    def test_por_defecto_no_es_recursivo(self, tmp_path: Path):
        """Por defecto solo vigila la carpeta."""
        v = VigilanteCarpeta(carpeta=tmp_path)
        assert v.recursivo is False

    def test_recursivo_se_puede_activar(self, tmp_path: Path):
        """Se puede pedir que vigile también las subcarpetas."""
        v = VigilanteCarpeta(carpeta=tmp_path, recursivo=True)
        assert v.recursivo is True

    def test_iniciar_programa_el_observador_en_modo_recursivo(self, tmp_path: Path, monkeypatch):
        """La opción llega al observador de watchdog, y al acabar se suelta la carpeta."""
        v = VigilanteCarpeta(carpeta=tmp_path, recursivo=True)
        capturado = {}

        def schedule_falso(manejador, ruta, recursive=False):
            capturado["recursive"] = recursive
            v._evento_detencion.set()  # para que el bucle de espera termine de inmediato

        monkeypatch.setattr(v.observador, "schedule", schedule_falso)
        monkeypatch.setattr(v.observador, "start", lambda: None)
        v.iniciar()
        assert capturado["recursive"] is True
        assert not v.organizador.bloqueo.adquirido  # al terminar suelta la carpeta

    def test_espera_negativa_lanza_value_error(self, tmp_path: Path):
        """Una espera negativa no tiene sentido: ValueError."""
        with pytest.raises(ValueError):
            VigilanteCarpeta(carpeta=tmp_path, espera=-1)


# --------------------------------------------------------------------------------------
# Comportamiento real del vigilante (con watchdog funcionando sobre una carpeta temporal)
# --------------------------------------------------------------------------------------

ESPERA_CORTA = 0.3


def _esperar(condicion, timeout: float = 10.0) -> bool:
    """Espera activa hasta que se cumpla ``condicion`` o venza ``timeout``."""
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        if condicion():
            return True
        time.sleep(0.05)
    return condicion()


def _sin_organizar(carpeta: Path) -> list[str]:
    """Ficheros visibles que siguen en la raíz de la carpeta."""
    return sorted(f.name for f in carpeta.iterdir() if f.is_file() and not f.name.startswith("."))


@pytest.fixture
def vigilante_activo(tmp_path: Path):
    """Arranca un vigilante real sobre tmp_path y lo detiene al terminar el test."""
    avisos: list[tuple[str, str]] = []
    vigilante = VigilanteCarpeta(
        carpeta=tmp_path, espera=ESPERA_CORTA, notificador=lambda n, m: avisos.append((n, m))
    )
    hilo = threading.Thread(target=vigilante.iniciar, daemon=True)
    hilo.start()
    assert _esperar(vigilante.observador.is_alive)
    vigilante.avisos = avisos  # type: ignore[attr-defined]
    yield vigilante
    vigilante.detener()
    hilo.join(timeout=10)


class TestVigilanteEnFuncionamiento:
    """El vigilante de verdad, con watchdog vigilando una carpeta temporal."""

    def test_organiza_un_fichero_nuevo(self, vigilante_activo: VigilanteCarpeta, tmp_path: Path):
        """Un fichero que aparece acaba en su categoría."""
        (tmp_path / "foto.jpg").write_text("x")
        assert _esperar(lambda: (tmp_path / "Imagenes" / "foto.jpg").exists())

    def test_muchos_ficheros_no_crean_un_hilo_por_fichero(
        self, vigilante_activo: VigilanteCarpeta, tmp_path: Path
    ):
        """300 ficheros de golpe se organizan con un solo hilo de trabajo, no uno por fichero."""
        hilos_al_empezar = threading.active_count()
        pico = hilos_al_empezar
        for i in range(300):
            (tmp_path / f"doc{i}.txt").write_text("x")
            pico = max(pico, threading.active_count())

        def terminado() -> bool:
            nonlocal pico
            pico = max(pico, threading.active_count())
            return not _sin_organizar(tmp_path)

        assert _esperar(terminado, timeout=30)
        assert len(list((tmp_path / "Documentos").iterdir())) == 300
        assert pico <= hilos_al_empezar + 1

    def test_descarga_que_termina_con_un_renombrado(
        self, vigilante_activo: VigilanteCarpeta, tmp_path: Path
    ):
        """Una descarga que acaba renombrando el ``.crdownload`` se organiza con su nombre final."""
        temporal = tmp_path / "informe.pdf.crdownload"
        temporal.write_bytes(b"x" * 1000)
        temporal.rename(tmp_path / "informe.pdf")
        assert _esperar(lambda: (tmp_path / "Documentos" / "informe.pdf").exists())

    def test_espera_mientras_hay_una_descarga_en_curso(
        self, vigilante_activo: VigilanteCarpeta, tmp_path: Path
    ):
        """Mientras exista el ``.part`` de Firefox no se mueve el fichero vacío; al terminar, se
        mueve el completo una sola vez.
        """
        # Firefox crea el fichero final vacío y descarga en "<nombre>.part".
        final = tmp_path / "informe.pdf"
        parcial = tmp_path / "informe.pdf.part"
        final.write_bytes(b"")
        parcial.write_bytes(b"x" * 1000)
        time.sleep(ESPERA_CORTA * 10)
        assert final.exists(), "no debe moverse mientras la descarga sigue en curso"

        parcial.replace(final)  # la descarga termina
        assert _esperar(lambda: (tmp_path / "Documentos" / "informe.pdf").exists())
        assert (tmp_path / "Documentos" / "informe.pdf").read_bytes() == b"x" * 1000
        assert not (tmp_path / "Documentos" / "informe_1.pdf").exists()

    def test_un_fichero_que_sigue_creciendo_se_espera(
        self, vigilante_activo: VigilanteCarpeta, tmp_path: Path
    ):
        """Un fichero que se está escribiendo no se mueve hasta que deja de crecer."""
        fichero = tmp_path / "video.mp4"
        with open(fichero, "wb") as f:
            for _ in range(8):
                f.write(b"x" * 1000)
                f.flush()
                time.sleep(ESPERA_CORTA / 2)
            assert fichero.exists(), "no debe moverse mientras se está escribiendo"
        assert _esperar(lambda: (tmp_path / "Videos" / "video.mp4").exists())
        assert (tmp_path / "Videos" / "video.mp4").stat().st_size == 8000

    def test_un_error_no_detiene_la_vigilancia(
        self, vigilante_activo: VigilanteCarpeta, tmp_path: Path, monkeypatch
    ):
        """Un error inesperado con un fichero se avisa y los siguientes se organizan igual."""
        mover = vigilante_activo.organizador.mover
        mover_original = mover.mover

        def mover_que_falla_una_vez(ruta_archivo, carpeta_base, categoria):
            if ruta_archivo.name == "roto.txt":
                raise RuntimeError("fallo inesperado")
            return mover_original(ruta_archivo, carpeta_base, categoria)

        monkeypatch.setattr(mover, "mover", mover_que_falla_una_vez)
        (tmp_path / "roto.txt").write_text("x")
        assert _esperar(lambda: any(n == "error" for n, _ in vigilante_activo.avisos))

        (tmp_path / "despues.txt").write_text("x")
        assert _esperar(lambda: (tmp_path / "Documentos" / "despues.txt").exists())
        assert vigilante_activo.observador.is_alive()

    def test_detener_guarda_el_historial(self, tmp_path: Path):
        """Al detener, lo organizado queda en el historial para poder deshacerlo."""
        vigilante = VigilanteCarpeta(carpeta=tmp_path, espera=ESPERA_CORTA, notificador=_noop)
        hilo = threading.Thread(target=vigilante.iniciar, daemon=True)
        hilo.start()
        assert _esperar(vigilante.observador.is_alive)
        (tmp_path / "foto.jpg").write_text("x")
        assert _esperar(lambda: (tmp_path / "Imagenes" / "foto.jpg").exists())

        vigilante.detener()
        hilo.join(timeout=10)

        org = OrganizadorArchivos(carpeta_origen=tmp_path)
        org.mover.cargar_historial()
        assert [r.origen.name for r in org.mover.obtener_historial()] == ["foto.jpg"]


# --------------------------------------------------------------------------------------
# El vigilante continúa el historial existente (antes lo sustituía)
# --------------------------------------------------------------------------------------


def _vigilar_y_recibir(carpeta: Path, nombre: str) -> list[tuple[str, str]]:
    """Vigila ``carpeta``, deja caer un fichero, espera a que se organice y se detiene."""
    avisos: list[tuple[str, str]] = []
    vigilante = VigilanteCarpeta(
        carpeta=carpeta, espera=ESPERA_CORTA, notificador=lambda n, m: avisos.append((n, m))
    )
    hilo = threading.Thread(target=vigilante.iniciar, daemon=True)
    hilo.start()
    assert _esperar(vigilante.observador.is_alive)
    (carpeta / nombre).write_text("x")
    assert _esperar(lambda: not (carpeta / nombre).exists())
    vigilante.detener()
    hilo.join(timeout=10)
    return avisos


def _pasos_guardados(carpeta: Path) -> list[list[str]]:
    """Ficheros de cada paso del historial guardado, del más antiguo al más reciente."""
    datos = json.loads((carpeta / NOMBRE_ARCHIVO_HISTORIAL).read_text(encoding="utf-8"))
    return [sorted(Path(r["origen"]).name for r in o["registros"]) for o in datos["operaciones"]]


def _movimientos_guardados(carpeta: Path) -> list[str]:
    """Todos los ficheros del historial guardado, de cualquier paso."""
    return sorted(nombre for paso in _pasos_guardados(carpeta) for nombre in paso)


class TestHistorialContinuo:
    """El vigilante continúa el historial que hay en vez de sustituirlo."""

    def test_vigilar_no_impide_deshacer_la_organizacion_anterior(self, tmp_path: Path):
        """La vigilancia es un paso encima de la organización anterior: se deshace primero y luego
        se puede deshacer la otra.
        """
        for nombre in ("a.jpg", "b.pdf", "c.mp3"):
            (tmp_path / nombre).write_text("x")
        OrganizadorArchivos(carpeta_origen=tmp_path).ejecutar()

        avisos = _vigilar_y_recibir(tmp_path, "nuevo.png")

        # La vigilancia es un paso propio encima de la organización anterior.
        assert _pasos_guardados(tmp_path) == [["a.jpg", "b.pdf", "c.mp3"], ["nuevo.png"]]
        assert any("antes que el paso anterior." in m for _, m in avisos)

        primero = OrganizadorArchivos(carpeta_origen=tmp_path).deshacer()
        assert primero is not None and primero.exitosos == 1
        assert _sin_organizar(tmp_path) == ["nuevo.png"]
        assert primero.pasos_restantes == 1
        assert primero.siguiente is not None and primero.siguiente.tipo == "organizar"

        segundo = OrganizadorArchivos(carpeta_origen=tmp_path).deshacer()
        assert segundo is not None and segundo.exitosos == 3
        assert _sin_organizar(tmp_path) == ["a.jpg", "b.pdf", "c.mp3", "nuevo.png"]

    def test_un_historial_danado_se_guarda_aparte_y_se_empieza_otro(self, tmp_path: Path):
        """Un historial dañado se guarda aparte intacto, se avisa y se empieza uno nuevo."""
        (tmp_path / NOMBRE_ARCHIVO_HISTORIAL).write_text("{ esto no es json", encoding="utf-8")

        avisos = _vigilar_y_recibir(tmp_path, "nuevo.png")

        copias = list(tmp_path.glob(".file-organizer-history.*.json"))
        assert len(copias) == 1
        assert copias[0].read_text(encoding="utf-8") == "{ esto no es json"  # intacta
        assert _movimientos_guardados(tmp_path) == ["nuevo.png"]
        assert any(n == "aviso" and copias[0].name in m for n, m in avisos)

    def test_el_historial_de_otras_carpetas_no_se_mezcla(self, tmp_path: Path):
        """Si el historial es de otra organización hacia esta carpeta, se guarda aparte en lugar de
        mezclarlo.
        """
        otra = tmp_path / "otra"
        otra.mkdir()
        (otra / "x.jpg").write_text("x")
        vigilada = tmp_path / "vigilada"
        vigilada.mkdir()
        # Se organizó «otra» con destino «vigilada»: el historial está en «vigilada».
        OrganizadorArchivos(carpeta_origen=otra, carpeta_destino=vigilada).ejecutar()

        avisos = _vigilar_y_recibir(vigilada, "nuevo.png")

        assert _movimientos_guardados(vigilada) == ["nuevo.png"]
        copias = list(vigilada.glob(".file-organizer-history.*.json"))
        assert len(copias) == 1 and "x.jpg" in copias[0].read_text(encoding="utf-8")
        assert any(n == "aviso" for n, _ in avisos)

    def test_sin_historial_previo_no_avisa(self, tmp_path: Path):
        """Sin historial anterior no hay aviso ni copia apartada."""
        avisos = _vigilar_y_recibir(tmp_path, "nuevo.png")
        assert _movimientos_guardados(tmp_path) == ["nuevo.png"]
        assert not any("historial" in m and "anterior" in m for _, m in avisos)
        assert not list(tmp_path.glob(".file-organizer-history.*.json"))


class TestEventosConRetraso:
    """Los eventos tal como los entrega macOS (FSEvents), que los agrupa y los da con retraso.

    Se llama al manejador directamente con esos eventos, así se comprueba en cualquier sistema.
    """

    @pytest.fixture
    def manejador_activo(self, tmp_path: Path):
        """Manejador con su hilo de trabajo en marcha; se detiene al terminar el test."""
        manejador = ManejadorEventosCarpeta(
            OrganizadorArchivos(carpeta_origen=tmp_path), ESPERA_CORTA, notificador=_noop
        )
        manejador.iniciar()
        yield manejador
        manejador.detener()

    def test_descarga_de_firefox_con_el_borrado_del_vacio_al_final(
        self, manejador_activo: ManejadorEventosCarpeta, tmp_path: Path
    ):
        """Antes fallaba en macOS: el ``.part`` se renombra encima del ``informe.pdf`` vacío y el
        aviso de que el vacío se borró llegaba después del renombrado. Se olvidaba el fichero y la
        descarga completa no se organizaba nunca.
        """
        final = tmp_path / "informe.pdf"
        parcial = tmp_path / "informe.pdf.part"
        final.write_bytes(b"")
        parcial.write_bytes(b"x" * 1000)
        manejador_activo.on_created(FileCreatedEvent(str(final)))
        manejador_activo.on_created(FileCreatedEvent(str(parcial)))

        parcial.replace(final)  # la descarga termina
        manejador_activo.on_moved(FileMovedEvent(str(parcial), str(final)))
        manejador_activo.on_deleted(FileDeletedEvent(str(final)))  # el del vacío, con retraso

        assert _esperar(lambda: (tmp_path / "Documentos" / "informe.pdf").exists())
        assert (tmp_path / "Documentos" / "informe.pdf").read_bytes() == b"x" * 1000

    def test_un_fichero_borrado_de_verdad_se_olvida(
        self, manejador_activo: ManejadorEventosCarpeta, tmp_path: Path
    ):
        """Si el fichero ya no está, se deja de esperar por él."""
        fichero = tmp_path / "nota.txt"
        fichero.write_text("x")
        manejador_activo.on_created(FileCreatedEvent(str(fichero)))
        assert manejador_activo.pendientes == 1

        fichero.unlink()
        manejador_activo.on_deleted(FileDeletedEvent(str(fichero)))
        assert manejador_activo.pendientes == 0


@pytest.mark.parametrize("manejar", ["on_created", "on_modified", "on_moved", "on_deleted"])
def test_un_evento_con_una_ruta_imposible_no_detiene_la_vigilancia(tmp_path: Path, manejar: str):
    """Antes la ruta del evento se interpretaba fuera de la protección y el error salía del
    manejador (y mataba el hilo de watchdog). Ahora se avisa como error y sigue."""
    avisos: list[tuple[str, str]] = []
    manejador = ManejadorEventosCarpeta(
        OrganizadorArchivos(carpeta_origen=tmp_path),
        0.1,
        notificador=lambda nivel, mensaje: avisos.append((nivel, mensaje)),
    )
    evento = SimpleNamespace(is_directory=False, src_path=None, dest_path=None)

    getattr(manejador, manejar)(evento)  # no lanza

    assert [nivel for nivel, _ in avisos] == ["error"]
    assert avisos[0][1].startswith("Error inesperado en el vigilante")
    assert getattr(ManejadorEventosCarpeta, manejar).__name__ == manejar  # functools.wraps
