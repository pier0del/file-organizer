"""Tests de la detección de duplicados: el detector, el organizador (avisar y apartar), deshacer,
el vigilante, la consola y el menú."""

import builtins
import logging
import os
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

from file_organizer import duplicados as modulo_duplicados
from file_organizer.clasificador import CriterioAgrupacion
from file_organizer.cli import aplicacion, interactivo
from file_organizer.config_usuario import ConfiguracionUsuario
from file_organizer.duplicados import (
    CARPETA_DUPLICADOS,
    AccionDuplicados,
    DetectorDuplicados,
    Duplicado,
)
from file_organizer.organizador import OrganizadorArchivos
from file_organizer.vigilante import VigilanteCarpeta

FOTO = b"\xff\xd8 foto de las vacaciones " * 50
OTRA = b"\xff\xd8 otra foto distinta!!!! " * 50  # mismo tamaño que FOTO, otro contenido


def fichero(ruta: Path, datos: bytes = FOTO) -> Path:
    """Crea el fichero (y su carpeta) con ese contenido."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(datos)
    return ruta


def contenido(carpeta: Path) -> list[str]:
    """Ficheros bajo ``carpeta`` (sin los ocultos del programa), en formato posix."""
    return sorted(
        p.relative_to(carpeta).as_posix()
        for p in carpeta.rglob("*")
        if p.is_file() and not any(parte.startswith(".") for parte in p.relative_to(carpeta).parts)
    )


# --------------------------------------------------------------------- detector


class TestDetector:
    """Cuándo un fichero es igual que otro de la carpeta adonde va o de los ya organizados."""

    def test_igual_que_uno_de_su_carpeta(self, tmp_path: Path):
        """Si ya hay uno con el mismo contenido en la carpeta de destino, ese es el original."""
        original = fichero(tmp_path / "Imagenes" / "foto.jpg")
        nuevo = fichero(tmp_path / "copia.jpg")
        assert DetectorDuplicados().original_de(nuevo, tmp_path / "Imagenes") == original

    def test_mismo_tamano_distinto_contenido(self, tmp_path: Path):
        """El tamaño solo descarta: dos fotos del mismo tamaño y distinto contenido no son
        duplicados.
        """
        fichero(tmp_path / "Imagenes" / "foto.jpg", FOTO)
        nuevo = fichero(tmp_path / "otra.jpg", OTRA)
        assert len(FOTO) == len(OTRA)
        assert DetectorDuplicados().original_de(nuevo, tmp_path / "Imagenes") is None

    def test_mismo_principio_y_final_pero_distinto_en_medio(self, tmp_path: Path):
        """La huella rápida empata; el SHA-256 completo los distingue."""
        extremo = b"x" * (64 * 1024)
        fichero(tmp_path / "Videos" / "a.mp4", extremo + b"A" * 1000 + extremo)
        nuevo = fichero(tmp_path / "b.mp4", extremo + b"B" * 1000 + extremo)
        assert DetectorDuplicados().original_de(nuevo, tmp_path / "Videos") is None

    def test_ficheros_grandes_iguales(self, tmp_path: Path):
        """Dos ficheros de 300 KB iguales, más grandes que lo que
        mira la huella rápida, sí lo son.
        """
        datos = os.urandom(300 * 1024)
        original = fichero(tmp_path / "Videos" / "a.mp4", datos)
        nuevo = fichero(tmp_path / "b.mp4", datos)
        assert DetectorDuplicados().original_de(nuevo, tmp_path / "Videos") == original

    def test_los_vacios_no_son_duplicados(self, tmp_path: Path):
        """Dos ficheros vacíos no cuentan: todos los vacíos serían «iguales»."""
        fichero(tmp_path / "Documentos" / "a.txt", b"")
        nuevo = fichero(tmp_path / "b.txt", b"")
        assert DetectorDuplicados().original_de(nuevo, tmp_path / "Documentos") is None

    def test_carpeta_que_aun_no_existe(self, tmp_path: Path):
        """Si la carpeta de destino todavía no existe, no hay con qué comparar."""
        nuevo = fichero(tmp_path / "foto.jpg")
        assert DetectorDuplicados().original_de(nuevo, tmp_path / "Imagenes") is None

    def test_ignora_ficheros_ocultos_y_subcarpetas(self, tmp_path: Path):
        """Solo cuentan los ficheros visibles que están directamente en la carpeta."""
        fichero(tmp_path / "Imagenes" / ".foto.jpg")
        fichero(tmp_path / "Imagenes" / "sub" / "foto.jpg")
        nuevo = fichero(tmp_path / "foto.jpg")
        assert DetectorDuplicados().original_de(nuevo, tmp_path / "Imagenes") is None

    def test_compara_con_los_anotados_de_la_misma_tanda(self, tmp_path: Path):
        """Un fichero se compara también con los ya organizados en esta misma ejecución, aunque
        vayan a otra carpeta.
        """
        detector = DetectorDuplicados()
        primero = fichero(tmp_path / "foto.jpg")
        detector.anotar(primero, primero)
        segundo = fichero(tmp_path / "copia.jpg")
        assert detector.original_de(segundo, tmp_path / "Otra carpeta") == primero

    def test_los_duplicados_anotados_no_cuentan_como_originales(self, tmp_path: Path):
        """Lo que se anotó como duplicado no sirve de original para los siguientes."""
        detector = DetectorDuplicados()
        copia = fichero(tmp_path / "copia.jpg")
        detector.anotar(copia, copia, es_duplicado=True)
        assert detector.original_de(fichero(tmp_path / "otra.jpg"), tmp_path / "x") is None

    def test_lista_cada_carpeta_una_vez(self, tmp_path: Path, monkeypatch):
        """Al organizar muchos ficheros en la misma carpeta, esta se recorre una sola vez y lo que
        se va moviendo se anota.
        """
        listados: list[Path] = []
        listar = modulo_duplicados._listar
        monkeypatch.setattr(
            modulo_duplicados,
            "_listar",
            lambda carpeta: listados.append(carpeta) or listar(carpeta),
        )
        carpeta = tmp_path / "Imagenes"
        fichero(carpeta / "previa.jpg", OTRA)
        detector = DetectorDuplicados()
        for i in range(5):
            nuevo = fichero(tmp_path / f"n{i}.jpg", bytes([i]) * 100)
            assert detector.original_de(nuevo, carpeta) is None
            movido = carpeta / nuevo.name
            nuevo.rename(movido)  # lo que hace el organizador
            detector.anotar(nuevo, movido)
        assert listados == [carpeta]

    def test_vuelve_a_listar_si_otro_cambia_la_carpeta(self, tmp_path: Path):
        """Si la carpeta cambia por su cuenta (cambia su fecha), se vuelve a recorrer y se ve el
        fichero nuevo.
        """
        carpeta = tmp_path / "Imagenes"
        carpeta.mkdir()
        detector = DetectorDuplicados()
        assert detector.original_de(fichero(tmp_path / "a.jpg"), carpeta) is None
        original = fichero(carpeta / "foto.jpg")  # alguien lo deja ahí por su cuenta
        os.utime(carpeta, ns=(1, 1))  # su fecha cambia (aunque el reloj sea poco preciso)
        assert detector.original_de(tmp_path / "a.jpg", carpeta) == original

    def test_un_fichero_que_cambia_se_vuelve_a_leer(self, tmp_path: Path):
        """Si el original cambia de contenido, sus huellas guardadas ya no valen."""
        detector = DetectorDuplicados()
        original = fichero(tmp_path / "Imagenes" / "foto.jpg", FOTO)
        nuevo = fichero(tmp_path / "copia.jpg", FOTO)
        assert detector.original_de(nuevo, tmp_path / "Imagenes") == original
        fichero(original, OTRA)
        os.utime(original, ns=(1, 1))
        assert detector.original_de(nuevo, tmp_path / "Imagenes") is None

    def test_muchos_del_mismo_tamano_no_se_comparan_todos_con_todos(
        self, tmp_path: Path, monkeypatch
    ):
        """Antes cada fichero se comparaba con todos los anteriores del mismo tamaño: 2000
        ficheros tardaban 10 s. Ahora cada uno consulta sus huellas un número fijo de veces."""
        consultas = []
        huellas_de = DetectorDuplicados._huellas_de
        monkeypatch.setattr(
            DetectorDuplicados,
            "_huellas_de",
            lambda self, ruta: consultas.append(ruta) or huellas_de(self, ruta),
        )
        n = 300
        carpeta = tmp_path / "Imagenes"
        detector = DetectorDuplicados()
        for i in range(n):
            nuevo = fichero(tmp_path / f"f{i:03d}.jpg", i.to_bytes(2, "big") * 500)
            assert detector.original_de(nuevo, carpeta) is None
            detector.anotar(nuevo, nuevo)
        assert len(consultas) <= 3 * n  # con todos contra todos serían unas 45 000

    def test_texto_del_duplicado(self, tmp_path: Path):
        """El texto muestra las rutas relativas a la carpeta: ``copia.jpg = Imagenes/foto.jpg``."""
        duplicado = Duplicado(tmp_path / "copia.jpg", tmp_path / "Imagenes" / "foto.jpg")
        assert duplicado.texto(tmp_path) == "copia.jpg = Imagenes/foto.jpg"
        assert duplicado.texto(tmp_path / "otra") == "copia.jpg = foto.jpg"


# -------------------------------------------------------- organizar y deshacer


@pytest.fixture
def descargas(tmp_path: Path) -> Path:
    """«foto.jpg» y su «copia de foto.jpg», más otra foto distinta del mismo tamaño."""
    carpeta = tmp_path / "descargas"
    fichero(carpeta / "copia de foto.jpg")
    fichero(carpeta / "foto.jpg")
    fichero(carpeta / "otra.jpg", OTRA)
    return carpeta


class TestOrganizar:
    """Organizar avisando de los duplicados o apartándolos en «Duplicados»."""

    def test_por_defecto_avisa_y_organiza_igual(self, descargas: Path):
        """Sin indicar nada, el duplicado se organiza como los demás y se avisa de él."""
        org = OrganizadorArchivos(carpeta_origen=descargas)
        assert org.ejecutar() == 3

        assert contenido(descargas) == [
            "Imagenes/copia de foto.jpg",
            "Imagenes/foto.jpg",
            "Imagenes/otra.jpg",
        ]
        assert [d.texto(descargas) for d in org.ultimos_duplicados] == [
            "copia de foto.jpg = Imagenes/foto.jpg"
        ]

    def test_mover_los_aparta_en_duplicados(self, descargas: Path):
        """Con MOVER, el duplicado va a «Duplicados» y cuenta en sus propias estadísticas."""
        org = OrganizadorArchivos(carpeta_origen=descargas, duplicados=AccionDuplicados.MOVER)
        org.ejecutar()

        assert contenido(descargas) == [
            "Duplicados/copia de foto.jpg",
            "Imagenes/foto.jpg",
            "Imagenes/otra.jpg",
        ]
        assert org.estadisticas() == {"Imagenes": 2, CARPETA_DUPLICADOS: 1}

    def test_igual_que_uno_ya_organizado(self, tmp_path: Path):
        """Un fichero igual que uno organizado antes se detecta; con el mismo nombre, se renombra en
        lugar de pisarlo.
        """
        carpeta = tmp_path / "c"
        fichero(carpeta / "Imagenes" / "foto.jpg")
        fichero(carpeta / "foto.jpg")

        org = OrganizadorArchivos(carpeta_origen=carpeta)
        org.ejecutar()

        assert contenido(carpeta) == ["Imagenes/foto.jpg", "Imagenes/foto_1.jpg"]
        assert [d.texto(carpeta) for d in org.ultimos_duplicados] == [
            "foto.jpg = Imagenes/foto.jpg"
        ]

    def test_el_original_es_el_de_nombre_mas_corto(self, tmp_path: Path):
        """Entre copias, se queda como original el nombre más corto («foto.jpg» y no «foto
        (1).jpg»).
        """
        carpeta = tmp_path / "c"
        for nombre in ("foto - copia.jpg", "foto (1).jpg", "foto.jpg"):
            fichero(carpeta / nombre)
        org = OrganizadorArchivos(carpeta_origen=carpeta, duplicados=AccionDuplicados.MOVER)
        org.ejecutar()

        assert contenido(carpeta) == [
            "Duplicados/foto (1).jpg",
            "Duplicados/foto - copia.jpg",
            "Imagenes/foto.jpg",
        ]
        assert {d.original.name for d in org.ultimos_duplicados} == {"foto.jpg"}

    def test_simular_encuentra_los_mismos_sin_mover_nada(self, descargas: Path):
        """Simulando se detectan los mismos duplicados y se dice que irían a «Duplicados», sin mover
        nada.
        """
        antes = contenido(descargas)
        org = OrganizadorArchivos(
            carpeta_origen=descargas, modo_simulacion=True, duplicados=AccionDuplicados.MOVER
        )
        org.ejecutar()

        assert contenido(descargas) == antes
        assert [d.texto(descargas) for d in org.ultimos_duplicados] == [
            "copia de foto.jpg = foto.jpg"
        ]
        destinos = {r.origen.name: r.destino.parent.name for r in org.ultimos_movimientos}
        assert destinos["copia de foto.jpg"] == CARPETA_DUPLICADOS

    def test_entre_carpetas_distintas_al_agrupar_por_fecha(self, tmp_path: Path):
        """Agrupando por fecha, dos copias de meses distintos se detectan igual."""
        carpeta = tmp_path / "c"
        nota = fichero(carpeta / "nota.txt", b"la misma nota")
        copia = fichero(carpeta / "nota copia.txt", b"la misma nota")
        for ruta, fecha in ((nota, datetime(2021, 6, 1)), (copia, datetime(2024, 3, 1))):
            os.utime(ruta, (fecha.timestamp(), fecha.timestamp()))

        org = OrganizadorArchivos(
            carpeta_origen=carpeta,
            agrupar=CriterioAgrupacion.FECHA,
            duplicados=AccionDuplicados.MOVER,
        )
        org.ejecutar()

        assert contenido(carpeta) == ["2021/06/nota.txt", "Duplicados/nota copia.txt"]

    def test_tres_copias_dos_duplicados_del_mismo(self, tmp_path: Path):
        """Con tres copias, las dos últimas son duplicados del mismo original."""
        carpeta = tmp_path / "c"
        for nombre in ("a.jpg", "a2.jpg", "a33.jpg"):
            fichero(carpeta / nombre)
        org = OrganizadorArchivos(carpeta_origen=carpeta)
        org.ejecutar()
        assert [(d.fichero.name, d.original.name) for d in org.ultimos_duplicados] == [
            ("a2.jpg", "a.jpg"),
            ("a33.jpg", "a.jpg"),
        ]

    def test_cada_ejecucion_empieza_de_cero(self, descargas: Path):
        """Ejecutar dos veces no acumula los duplicados de la anterior."""
        org = OrganizadorArchivos(carpeta_origen=descargas, modo_simulacion=True)
        org.ejecutar()
        org.ejecutar()
        assert len(org.ultimos_duplicados) == 1

    def test_deshacer_devuelve_los_duplicados_y_borra_la_carpeta(self, descargas: Path):
        """Deshacer devuelve también los apartados y quita «Duplicados» si queda vacía."""
        antes = contenido(descargas)
        OrganizadorArchivos(carpeta_origen=descargas, duplicados=AccionDuplicados.MOVER).ejecutar()
        resultado = OrganizadorArchivos(carpeta_origen=descargas).deshacer()

        assert resultado is not None and resultado.exitosos == 3
        assert contenido(descargas) == antes
        assert not (descargas / CARPETA_DUPLICADOS).exists()


# --------------------------------------------------------------------- vigilante


class TestVigilante:
    """El vigilante y la carpeta «Duplicados»."""

    def test_lo_que_esta_en_duplicados_ya_esta_organizado(self, tmp_path: Path):
        """Lo que hay en «Duplicados» no se vuelve a organizar."""
        org = OrganizadorArchivos(carpeta_origen=tmp_path)
        assert org.esta_en_carpeta_de_categoria(fichero(tmp_path / CARPETA_DUPLICADOS / "a.jpg"))

    def test_aparta_el_duplicado_que_llega_y_avisa(self, tmp_path: Path):
        """Una copia que llega se aparta y se avisa de ella; vigilando subcarpetas no se mueve otra
        vez desde «Duplicados».
        """
        original = fichero(tmp_path / "Imagenes" / "foto.jpg")
        avisos: list[tuple[str, str]] = []
        vigilante = VigilanteCarpeta(
            carpeta=tmp_path,
            espera=0.3,
            recursivo=True,
            duplicados=AccionDuplicados.MOVER,
            notificador=lambda nivel, mensaje: avisos.append((nivel, mensaje)),
        )
        hilo = threading.Thread(target=vigilante.iniciar, daemon=True)
        hilo.start()
        try:
            limite = time.monotonic() + 10
            while not vigilante.observador.is_alive() and time.monotonic() < limite:
                time.sleep(0.05)
            fichero(tmp_path / "copia.jpg")
            apartado = tmp_path / CARPETA_DUPLICADOS / "copia.jpg"
            while not apartado.exists() and time.monotonic() < limite:
                time.sleep(0.05)
            assert apartado.exists()
            time.sleep(0.8)  # margen para que un segundo movimiento (el fallo) ocurriera
        finally:
            vigilante.detener()
            hilo.join(timeout=10)

        assert original.exists() and apartado.exists()
        assert contenido(tmp_path) == ["Duplicados/copia.jpg", "Imagenes/foto.jpg"]
        assert ("aviso", "Duplicado: copia.jpg = Imagenes/foto.jpg") in avisos


# ------------------------------------------------------------ consola y menú


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


def _main(monkeypatch: pytest.MonkeyPatch, *argumentos: str) -> int:
    """Ejecuta la consola con esos argumentos y devuelve su código de salida."""
    monkeypatch.setattr(sys, "argv", ["file-organizer", *argumentos])
    return aplicacion.main()


def _sin_colores(texto: str) -> str:
    """Quita los códigos de color de la salida de la consola."""
    return re.sub(r"\x1b\[[0-9;]*m", "", texto)


@pytest.mark.usefixtures("consola")
class TestConsola:
    """La opción ``--duplicados`` de la consola y cómo se listan."""

    def test_por_defecto_lista_los_duplicados(self, descargas: Path, monkeypatch, capsys):
        """Sin la opción se listan con su original, pero no se crea «Duplicados»."""
        assert _main(monkeypatch, "--carpeta", str(descargas)) == 0

        salida = _sin_colores(capsys.readouterr().out)
        assert "1 duplicado(s) (mismo contenido que otro fichero), organizados como los demás:" in (
            salida
        )
        assert "copia de foto.jpg = Imagenes/foto.jpg" in salida
        assert not (descargas / CARPETA_DUPLICADOS).exists()

    def test_mover_los_aparta(self, descargas: Path, monkeypatch, capsys):
        """Con ``--duplicados mover`` se apartan y se dice dónde."""
        assert _main(monkeypatch, "--carpeta", str(descargas), "--duplicados", "mover") == 0

        assert (descargas / CARPETA_DUPLICADOS / "copia de foto.jpg").exists()
        assert "apartados en Duplicados/:" in _sin_colores(capsys.readouterr().out)

    def test_simular_los_lista(self, descargas: Path, monkeypatch, capsys):
        """Simulando, se listan sin mover nada."""
        antes = contenido(descargas)
        assert _main(monkeypatch, "-f", str(descargas), "--simular", "--duplicados", "mover") == 0
        assert contenido(descargas) == antes
        assert "copia de foto.jpg = foto.jpg" in _sin_colores(capsys.readouterr().out)

    def test_muchos_duplicados_se_resumen(self, tmp_path: Path, monkeypatch, capsys):
        """Con más de 20, la consola enseña 20 y dice cuántos más hay; el log los tiene todos."""
        carpeta = tmp_path / "c"
        fichero(carpeta / "a.jpg")
        for i in range(25):
            fichero(carpeta / f"copia {i:02d}.jpg")
        _main(monkeypatch, "--carpeta", str(carpeta))

        salida = _sin_colores(capsys.readouterr().out)
        assert "25 duplicado(s)" in salida
        assert "... y 5 más (todos en el fichero de log)" in salida
        log = (tmp_path / "file-organizer.log").read_text(encoding="utf-8")
        assert log.count("Duplicado: ") == 25

    def test_accion_que_no_existe(self, descargas: Path, monkeypatch, capsys):
        """Una acción no válida es un error de uso (código 2)."""
        with pytest.raises(SystemExit) as salida:
            _main(monkeypatch, "--carpeta", str(descargas), "--duplicados", "borrar")
        assert salida.value.code == 2

    def test_vigilar_con_duplicados_apartados(self, descargas: Path, monkeypatch):
        """``--vigilar`` recibe la acción elegida para los duplicados."""
        capturado: dict[str, object] = {}

        def vigilar_falso(carpeta: Path, **opciones: object) -> int:
            capturado.update(opciones)
            return 0

        monkeypatch.setattr(aplicacion, "ejecutar_vigilar", vigilar_falso)
        _main(monkeypatch, "--carpeta", str(descargas), "--vigilar", "--duplicados", "mover")
        assert capturado["duplicados"] is AccionDuplicados.MOVER


def _menu(monkeypatch: pytest.MonkeyPatch, respuestas: list[str]) -> list[str]:
    """Ejecuta el menú con esas respuestas y devuelve las preguntas que hizo."""
    pendientes = iter(respuestas)
    preguntas: list[str] = []

    def input_falso(pregunta: str = "") -> str:
        preguntas.append(pregunta)
        return next(pendientes)

    monkeypatch.setattr(builtins, "input", input_falso)
    assert interactivo.ejecutar_menu() == 0
    return preguntas


@pytest.mark.usefixtures("consola")
class TestMenu:
    """La pregunta sobre duplicados del menú interactivo."""

    def test_apartar_los_duplicados_y_recordarlo(self, descargas: Path, monkeypatch):
        """Responder «s» los aparta y guarda la respuesta para la próxima vez."""
        # 01, carpeta, agrupar=ENTER, duplicados=s, config=ENTER, backup=n, integridad, pausa, salir
        _menu(monkeypatch, ["01", str(descargas), "", "s", "", "n", "", "", "11"])

        assert (descargas / CARPETA_DUPLICADOS / "copia de foto.jpg").exists()
        assert ConfiguracionUsuario().cargar().obtener("mover_duplicados") is True

    def test_propone_la_ultima_respuesta(self, descargas: Path, monkeypatch):
        """Si la última vez se apartaron, la pregunta propone «S» con ENTER."""
        cfg = ConfiguracionUsuario().cargar()
        cfg.establecer("mover_duplicados", True)
        cfg.guardar()

        # 02 (simular), carpeta, agrupar, duplicados y config=ENTER, pausa, salir
        preguntas = _menu(monkeypatch, ["02", str(descargas), "", "", "", "", "11"])

        pregunta = next(p for p in preguntas if "duplicados" in p)
        assert "(S/n)" in _sin_colores(pregunta)

    def test_vigilar_con_duplicados_apartados(self, descargas: Path, monkeypatch):
        """La opción 07 (vigilar) también pregunta y lo pasa al vigilante."""
        capturado: dict[str, object] = {}

        def vigilar_falso(carpeta: Path, **opciones: object) -> int:
            capturado.update(opciones)
            return 0

        monkeypatch.setattr(interactivo, "ejecutar_vigilar", vigilar_falso)
        # 07, carpeta, agrupar=ENTER, duplicados=s, config=ENTER, subcarpetas=n, integridad, ...
        _menu(monkeypatch, ["07", str(descargas), "", "s", "", "n", "", "", "11"])

        assert capturado["duplicados"] is AccionDuplicados.MOVER


def test_deshacer_avisa_del_progreso(tmp_path: Path):
    """Deshacer llama al aviso de progreso tras cada fichero, con el total."""
    carpeta = tmp_path / "c"
    for nombre in ("a.jpg", "b.pdf", "c.txt"):
        fichero(carpeta / nombre, nombre.encode())
    OrganizadorArchivos(carpeta_origen=carpeta).ejecutar()
    avances: list[tuple[int, int]] = []

    OrganizadorArchivos(carpeta_origen=carpeta).deshacer(
        progreso=lambda actual, total: avances.append((actual, total))
    )

    assert avances == [(1, 3), (2, 3), (3, 3)]
