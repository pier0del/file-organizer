"""Tests de agrupar por fecha (``2024/03``) y por tipo y fecha (``Imagenes/2024/03``): el
clasificador, el organizador, el deshacer y el vigilante con carpetas de varios niveles."""

import builtins
import logging
import os
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path, PurePath

import pytest

from file_organizer.clasificador import (
    DESCRIPCIONES_AGRUPACION,
    Clasificador,
    ClasificadorPorFecha,
    CriterioAgrupacion,
    crear_clasificador,
)
from file_organizer.cli import aplicacion, interactivo
from file_organizer.config_usuario import ConfiguracionUsuario
from file_organizer.organizador import OrganizadorArchivos
from file_organizer.vigilante import ManejadorEventosCarpeta, VigilanteCarpeta

# Un PDF con su fecha de creación: basta para que la fecha salga de los metadatos.
PDF_ENERO_2020 = b"%PDF-1.4\n<< /CreationDate (D:20200115103000) >>\n%%EOF"


def fichero(ruta: Path, fecha: datetime | None = None, datos: bytes = b"x") -> Path:
    """Crea el fichero (y su carpeta) con esa fecha de modificación."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(datos)
    if fecha is not None:
        os.utime(ruta, (fecha.timestamp(), fecha.timestamp()))
    return ruta


def contenido(carpeta: Path) -> list[str]:
    """Ficheros y carpetas bajo ``carpeta`` (sin los ocultos del programa), en formato posix."""
    return sorted(
        p.relative_to(carpeta).as_posix()
        for p in carpeta.rglob("*")
        if not any(parte.startswith(".") for parte in p.relative_to(carpeta).parts)
    )


JUNIO_2021 = datetime(2021, 6, 10, 12, 0)
MARZO_2024 = datetime(2024, 3, 1, 9, 0)


# ------------------------------------------------------------------ clasificador


class TestClasificadorPorFecha:
    """Qué carpeta de fecha toca a cada fichero y qué carpetas cuentan como ya organizadas."""

    def test_clasifica_por_anio_y_mes(self, tmp_path: Path):
        """Sin metadatos, la fecha del propio fichero da su carpeta ``AAAA/MM``."""
        clf = ClasificadorPorFecha()
        assert clf.clasificar(fichero(tmp_path / "nota.txt", JUNIO_2021)) == "2021/06"

    def test_con_tipo_va_dentro_de_su_categoria(self, tmp_path: Path):
        """Por tipo y fecha, la fecha va dentro de la categoría, también en «Otros»."""
        clf = ClasificadorPorFecha(Clasificador())
        assert clf.clasificar(fichero(tmp_path / "foto.jpg", MARZO_2024)) == "Imagenes/2024/03"
        assert clf.clasificar(fichero(tmp_path / "raro.xyz", MARZO_2024)) == "Otros/2024/03"

    def test_usa_la_fecha_de_los_metadatos(self, tmp_path: Path):
        """La fecha de creación del PDF manda sobre la fecha de modificación del fichero."""
        ruta = fichero(tmp_path / "doc.pdf", MARZO_2024, PDF_ENERO_2020)
        assert ClasificadorPorFecha().clasificar(ruta) == "2020/01"

    @pytest.mark.parametrize(
        ("carpeta", "esperado"),
        [
            ("2021/06", True),
            ("1999/12", True),
            ("2021", False),
            ("2021/13", False),
            ("2021/00", False),
            ("21/06", False),
            ("2021/06/dias", False),
            ("Imagenes/2021/06", False),
            ("Imagenes", False),
        ],
    )
    def test_carpetas_de_categoria_por_fecha(self, carpeta: str, esperado: bool):
        """Solo ``AAAA/MM`` con un mes válido cuenta como carpeta de fecha."""
        assert ClasificadorPorFecha().es_carpeta_de_categoria(PurePath(carpeta)) is esperado

    @pytest.mark.parametrize(
        ("carpeta", "esperado"),
        [
            ("Imagenes/2021/06", True),
            ("Otros/2021/06", True),
            ("2021/06", False),
            ("Imagenes/2021", False),
            ("Inventada/2021/06", False),
        ],
    )
    def test_carpetas_de_categoria_por_tipo_y_fecha(self, carpeta: str, esperado: bool):
        """Hace falta una categoría conocida y debajo su ``AAAA/MM``."""
        clf = ClasificadorPorFecha(Clasificador())
        assert clf.es_carpeta_de_categoria(PurePath(carpeta)) is esperado

    def test_categorias_propias_de_varios_niveles(self, tmp_path: Path):
        """Una categoría propia como ``Fotos/Viajes`` lleva la fecha debajo de sus dos niveles."""
        config = tmp_path / "cat.json"
        config.write_text('{"Fotos/Viajes": [".jpg"]}', encoding="utf-8")
        clf = ClasificadorPorFecha(Clasificador(config))
        assert clf.clasificar(fichero(tmp_path / "f.jpg", MARZO_2024)) == "Fotos/Viajes/2024/03"
        assert clf.es_carpeta_de_categoria(PurePath("Fotos/Viajes/2024/03"))
        assert not clf.es_carpeta_de_categoria(PurePath("Fotos/2024/03"))
        assert Clasificador(config).es_carpeta_de_categoria(PurePath("Fotos/Viajes"))

    def test_carpetas_de_categoria_por_tipo(self):
        """Por tipo solo cuentan las categorías, no sus subcarpetas ni otras carpetas."""
        clf = Clasificador()
        assert clf.es_carpeta_de_categoria(PurePath("Imagenes"))
        assert clf.es_carpeta_de_categoria(PurePath("Otros"))
        assert not clf.es_carpeta_de_categoria(PurePath("Imagenes/2021"))
        assert not clf.es_carpeta_de_categoria(PurePath("Vacaciones"))

    def test_resumen_de_donde_salen_las_fechas(self, tmp_path: Path):
        """El resumen cuenta cuántas fechas salieron de los metadatos (y de qué formato) y cuántas
        del fichero, y dice por qué.
        """
        clf = ClasificadorPorFecha()
        assert clf.fechas.resumen() is None
        clf.clasificar(fichero(tmp_path / "a.pdf", datos=PDF_ENERO_2020))
        clf.clasificar(fichero(tmp_path / "b.pdf", datos=PDF_ENERO_2020))
        clf.clasificar(fichero(tmp_path / "c.txt", JUNIO_2021))
        assert clf.fechas.resumen() == (
            "Fechas: 2 de los metadatos (PDF 2) y 1 de la fecha del propio fichero, porque no "
            "tenía metadatos con fecha."
        )

    def test_resumen_sin_metadatos(self, tmp_path: Path):
        """Si todas salen del fichero, el resumen no menciona los metadatos."""
        clf = ClasificadorPorFecha()
        clf.clasificar(fichero(tmp_path / "c.txt", JUNIO_2021))
        clf.clasificar(fichero(tmp_path / "d.txt", JUNIO_2021))
        assert clf.fechas.resumen() == (
            "Fechas: 2 de la fecha del propio fichero, porque no tenían metadatos con fecha."
        )

    def test_resumen_solo_con_metadatos(self, tmp_path: Path):
        """Si todas salen de los metadatos, el resumen no habla de la fecha del fichero."""
        clf = ClasificadorPorFecha()
        clf.clasificar(fichero(tmp_path / "a.pdf", datos=PDF_ENERO_2020))
        assert clf.fechas.resumen() == "Fechas: 1 de los metadatos (PDF 1)."


class TestCrearClasificador:
    """Qué clasificador se crea para cada forma de agrupar."""

    def test_por_tipo(self):
        """Por tipo, el clasificador de siempre."""
        assert isinstance(crear_clasificador(CriterioAgrupacion.TIPO), Clasificador)

    def test_por_fecha(self):
        """Por fecha, uno por fecha sin clasificador por tipo debajo."""
        clf = crear_clasificador(CriterioAgrupacion.FECHA)
        assert isinstance(clf, ClasificadorPorFecha) and clf.por_tipo is None

    def test_por_tipo_y_fecha_con_categorias_propias(self, tmp_path: Path):
        """Por tipo y fecha, con las categorías del fichero de configuración indicado."""
        config = tmp_path / "cat.json"
        config.write_text('{"Fotos": [".jpg"]}', encoding="utf-8")
        clf = crear_clasificador(CriterioAgrupacion.TIPO_FECHA, config)
        assert isinstance(clf, ClasificadorPorFecha)
        assert clf.clasificar(fichero(tmp_path / "f.jpg", MARZO_2024)) == "Fotos/2024/03"

    def test_preferencia_guardada_no_valida_vuelve_a_por_tipo(self, tmp_path: Path):
        """Un valor desconocido en las preferencias no rompe nada: se agrupa por tipo."""
        cfg = ConfiguracionUsuario(tmp_path / "uc.json")
        assert cfg.criterio_agrupacion() is CriterioAgrupacion.TIPO
        cfg.establecer("agrupar", "semana")
        assert cfg.criterio_agrupacion() is CriterioAgrupacion.TIPO
        cfg.establecer("agrupar", "tipo-fecha")
        assert cfg.criterio_agrupacion() is CriterioAgrupacion.TIPO_FECHA

    def test_el_organizador_usa_el_criterio(self, tmp_path: Path):
        """``agrupar`` del organizador decide su clasificador."""
        org = OrganizadorArchivos(carpeta_origen=tmp_path, agrupar=CriterioAgrupacion.FECHA)
        assert isinstance(org.clasificador, ClasificadorPorFecha)


# ----------------------------------------------------------- organizar y deshacer


@pytest.fixture
def carpeta(tmp_path: Path) -> Path:
    """Carpeta con un PDF de enero de 2020 (metadatos) y dos ficheros de junio de 2021."""
    c = tmp_path / "descargas"
    fichero(c / "informe.pdf", MARZO_2024, PDF_ENERO_2020)
    fichero(c / "nota.txt", JUNIO_2021)
    fichero(c / "foto.jpg", JUNIO_2021)
    return c


class TestOrganizarPorFecha:
    """Organizar de verdad, o simulando, agrupando por fecha."""

    def test_mueve_a_carpetas_de_anio_y_mes(self, carpeta: Path):
        """Cada fichero acaba en ``AAAA/MM`` y las estadísticas cuentan por mes."""
        org = OrganizadorArchivos(carpeta_origen=carpeta, agrupar=CriterioAgrupacion.FECHA)
        assert org.ejecutar() == 3
        assert contenido(carpeta) == [
            "2020",
            "2020/01",
            "2020/01/informe.pdf",
            "2021",
            "2021/06",
            "2021/06/foto.jpg",
            "2021/06/nota.txt",
        ]
        assert org.estadisticas() == {"2020/01": 1, "2021/06": 2}

    def test_por_tipo_y_fecha(self, carpeta: Path):
        """Cada fichero acaba en ``Categoría/AAAA/MM``."""
        org = OrganizadorArchivos(carpeta_origen=carpeta, agrupar=CriterioAgrupacion.TIPO_FECHA)
        org.ejecutar()
        ficheros = [r for r in contenido(carpeta) if "." in r]
        assert ficheros == [
            "Documentos/2020/01/informe.pdf",
            "Documentos/2021/06/nota.txt",
            "Imagenes/2021/06/foto.jpg",
        ]

    def test_simular_no_mueve_nada(self, carpeta: Path):
        """Simular no toca la carpeta, pero dice adónde iría cada fichero."""
        antes = contenido(carpeta)
        org = OrganizadorArchivos(
            carpeta_origen=carpeta, modo_simulacion=True, agrupar=CriterioAgrupacion.FECHA
        )
        org.ejecutar()
        assert contenido(carpeta) == antes
        destinos = sorted(
            r.destino.relative_to(carpeta).as_posix() for r in org.ultimos_movimientos
        )
        assert destinos == ["2020/01/informe.pdf", "2021/06/foto.jpg", "2021/06/nota.txt"]

    def test_mismo_nombre_en_el_mismo_mes_no_se_pisa(self, tmp_path: Path):
        """Si ya hay un fichero con ese nombre en el mes, el nuevo se llama ``nota_1.txt``."""
        c = tmp_path / "c"
        fichero(c / "2021" / "06" / "nota.txt", JUNIO_2021, b"anterior")
        fichero(c / "nota.txt", JUNIO_2021, b"nueva")
        OrganizadorArchivos(carpeta_origen=c, agrupar=CriterioAgrupacion.FECHA).ejecutar()
        assert (c / "2021" / "06" / "nota.txt").read_bytes() == b"anterior"
        assert (c / "2021" / "06" / "nota_1.txt").read_bytes() == b"nueva"

    def test_en_otra_carpeta_de_destino(self, carpeta: Path, tmp_path: Path):
        """Las carpetas de fecha se crean en el destino y la de origen queda vacía."""
        destino = tmp_path / "archivo"
        org = OrganizadorArchivos(
            carpeta_origen=carpeta, carpeta_destino=destino, agrupar=CriterioAgrupacion.FECHA
        )
        org.ejecutar()
        assert (destino / "2020" / "01" / "informe.pdf").exists()
        assert contenido(carpeta) == []

    def test_un_fichero_que_no_se_puede_leer_no_detiene_el_resto(self, tmp_path: Path):
        """Si leer la fecha de un fichero falla, se anota su error y se siguen organizando los
        demás.
        """
        c = tmp_path / "c"
        fichero(c / "bueno.txt", JUNIO_2021)
        fichero(c / "malo.txt", JUNIO_2021)

        class Clasificador:
            def clasificar(self, ruta: Path) -> str:
                if ruta.name == "malo.txt":
                    raise PermissionError("sin permiso para leerlo")
                return "2021/06"

            def es_carpeta_de_categoria(self, carpeta: PurePath) -> bool:
                return True

        org = OrganizadorArchivos(carpeta_origen=c, clasificador=Clasificador())
        assert org.ejecutar() == 1
        assert [(ruta.name, type(e)) for ruta, e in org.ultimos_errores] == [
            ("malo.txt", PermissionError)
        ]


class TestDeshacerPorFecha:
    """Deshacer devuelve los ficheros y quita solo las carpetas de fecha que quedan vacías."""

    def test_restaura_y_borra_las_carpetas_que_quedan_vacias(self, carpeta: Path):
        """Tras deshacer, la carpeta queda exactamente como estaba, sin ``Categoría/AAAA/MM``."""
        antes = contenido(carpeta)
        OrganizadorArchivos(
            carpeta_origen=carpeta, agrupar=CriterioAgrupacion.TIPO_FECHA
        ).ejecutar()
        resultado = OrganizadorArchivos(carpeta_origen=carpeta).deshacer()
        assert resultado is not None and resultado.exitosos == 3
        assert contenido(carpeta) == antes

    def test_conserva_las_carpetas_que_siguen_teniendo_algo(self, carpeta: Path):
        """Si el usuario guardó algo en la carpeta del año, esa carpeta se queda."""
        OrganizadorArchivos(carpeta_origen=carpeta, agrupar=CriterioAgrupacion.FECHA).ejecutar()
        fichero(carpeta / "2021" / "mio.txt")  # el usuario guarda algo en la carpeta del año
        OrganizadorArchivos(carpeta_origen=carpeta).deshacer()
        assert "2021/06" not in contenido(carpeta)
        assert "2021/mio.txt" in contenido(carpeta)
        assert "2020" not in contenido(carpeta)

    def test_no_borra_otras_carpetas_vacias_del_usuario(self, carpeta: Path):
        """Una carpeta vacía con forma de fecha que ya existía antes no se borra."""
        (carpeta / "2019" / "05").mkdir(parents=True)
        OrganizadorArchivos(carpeta_origen=carpeta, agrupar=CriterioAgrupacion.FECHA).ejecutar()
        OrganizadorArchivos(carpeta_origen=carpeta).deshacer()
        assert (carpeta / "2019" / "05").is_dir()

    def test_no_sube_por_encima_del_destino(self, tmp_path: Path):
        """Al borrar carpetas vacías hacia arriba, se para en la carpeta organizada."""
        c = tmp_path / "c"
        fichero(c / "nota.txt", JUNIO_2021)
        OrganizadorArchivos(carpeta_origen=c, agrupar=CriterioAgrupacion.FECHA).ejecutar()
        OrganizadorArchivos(carpeta_origen=c).deshacer()
        assert c.is_dir() and (c / "nota.txt").exists()

    def test_deshacer_por_pasos_mezclando_criterios(self, tmp_path: Path):
        """Organizado una vez por tipo y otra por fecha, cada deshacer revierte su paso."""
        c = tmp_path / "c"
        fichero(c / "foto.jpg", JUNIO_2021)
        OrganizadorArchivos(carpeta_origen=c).ejecutar()  # paso 1: por tipo
        fichero(c / "nota.txt", JUNIO_2021)
        OrganizadorArchivos(carpeta_origen=c, agrupar=CriterioAgrupacion.FECHA).ejecutar()

        OrganizadorArchivos(carpeta_origen=c).deshacer()  # deshace el paso por fecha
        assert contenido(c) == ["Imagenes", "Imagenes/foto.jpg", "nota.txt"]
        OrganizadorArchivos(carpeta_origen=c).deshacer()  # y después el de por tipo
        assert contenido(c) == ["foto.jpg", "nota.txt"]


# --------------------------------------------------------------------- vigilante


class TestVigilantePorFecha:
    """El vigilante reconoce lo ya organizado por fecha y no lo vuelve a mover."""

    def _manejador(self, carpeta: Path, criterio: CriterioAgrupacion) -> ManejadorEventosCarpeta:
        """Manejador de eventos con un organizador que agrupa según ``criterio``."""
        org = OrganizadorArchivos(carpeta_origen=carpeta, agrupar=criterio)
        return ManejadorEventosCarpeta(org, segundos_espera=1, notificador=lambda n, m: None)

    def test_ignora_lo_que_ya_esta_en_su_carpeta_de_fecha(self, tmp_path: Path):
        """Lo que está en ``AAAA/MM`` se da por organizado; lo que está solo en ``AAAA``, no."""
        manejador = self._manejador(tmp_path, CriterioAgrupacion.FECHA)
        assert manejador._ya_organizado(fichero(tmp_path / "2021" / "06" / "nota.txt"))
        assert not manejador._ya_organizado(fichero(tmp_path / "2021" / "nota.txt"))
        assert not manejador._ya_organizado(fichero(tmp_path / "nota.txt"))

    def test_ignora_lo_que_ya_esta_en_su_carpeta_de_tipo_y_fecha(self, tmp_path: Path):
        """Lo que está en ``Categoría/AAAA/MM`` se da por organizado; solo en la categoría, no."""
        manejador = self._manejador(tmp_path, CriterioAgrupacion.TIPO_FECHA)
        assert manejador._ya_organizado(fichero(tmp_path / "Imagenes" / "2021" / "06" / "f.jpg"))
        assert not manejador._ya_organizado(fichero(tmp_path / "Imagenes" / "f.jpg"))

    def test_vigilancia_recursiva_no_vuelve_a_mover_lo_organizado(self, tmp_path: Path):
        """Vigilando subcarpetas, el fichero que el propio vigilante deja en ``2020/01`` no se
        vuelve a mover como si fuera nuevo.
        """
        avisos: list[tuple[str, str]] = []
        vigilante = VigilanteCarpeta(
            carpeta=tmp_path,
            espera=0.3,
            recursivo=True,
            agrupar=CriterioAgrupacion.FECHA,
            notificador=lambda n, m: avisos.append((n, m)),
        )
        hilo = threading.Thread(target=vigilante.iniciar, daemon=True)
        hilo.start()
        try:
            limite = time.monotonic() + 10
            while not vigilante.observador.is_alive() and time.monotonic() < limite:
                time.sleep(0.05)
            (tmp_path / "doc.pdf").write_bytes(PDF_ENERO_2020)
            organizado = tmp_path / "2020" / "01" / "doc.pdf"
            while not organizado.exists() and time.monotonic() < limite:
                time.sleep(0.05)
            assert organizado.exists()
            time.sleep(1.0)  # margen para que un segundo movimiento (el fallo) ocurriera
        finally:
            vigilante.detener()
            hilo.join(timeout=10)

        assert contenido(tmp_path) == ["2020", "2020/01", "2020/01/doc.pdf"]
        assert [m for n, m in avisos if n == "exito" and "->" in m] == ["doc.pdf -> 2020/01/"]
        assert not [m for n, m in avisos if n == "error"]


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
    """La opción ``--agrupar`` de la consola."""

    def test_agrupar_por_fecha(self, carpeta: Path, monkeypatch, capsys):
        """Mueve por año y mes, lista los meses en orden y dice de dónde salió cada fecha."""
        assert _main(monkeypatch, "--carpeta", str(carpeta), "--agrupar", "fecha") == 0

        assert (carpeta / "2020" / "01" / "informe.pdf").exists()
        assert (carpeta / "2021" / "06" / "nota.txt").exists()
        salida = _sin_colores(capsys.readouterr().out)
        assert salida.index("2020/01: 1 fichero(s)") < salida.index("2021/06: 2 fichero(s)")
        assert (
            "Fechas: 1 de los metadatos (PDF 1) y 2 de la fecha del propio fichero, porque no "
            "tenían metadatos con fecha." in salida
        )

    def test_simular_por_tipo_y_fecha(self, carpeta: Path, monkeypatch, capsys):
        """Con las opciones cortas y ``--simular``, no mueve nada pero da el resumen de fechas."""
        antes = contenido(carpeta)
        assert _main(monkeypatch, "-f", str(carpeta), "-a", "tipo-fecha", "--simular") == 0

        assert contenido(carpeta) == antes
        assert "Fechas: " in capsys.readouterr().out

    def test_por_defecto_agrupa_por_tipo_aunque_la_gui_eligiera_fecha(
        self, carpeta: Path, monkeypatch
    ):
        """La consola no hereda la forma de agrupar que guardó la GUI: sin ``--agrupar`` es por
        tipo.
        """
        cfg = ConfiguracionUsuario().cargar()
        cfg.establecer("agrupar", "fecha")
        cfg.guardar()

        assert _main(monkeypatch, "--carpeta", str(carpeta)) == 0
        assert (carpeta / "Documentos" / "informe.pdf").exists()

    def test_criterio_que_no_existe(self, carpeta: Path, monkeypatch, capsys):
        """Un valor no válido es un error de uso (código 2) que enseña los válidos."""
        with pytest.raises(SystemExit) as salida:
            _main(monkeypatch, "--carpeta", str(carpeta), "--agrupar", "semana")
        assert salida.value.code == 2
        assert "tipo-fecha" in capsys.readouterr().err

    def test_la_ayuda_explica_como_agrupar(self, monkeypatch, capsys):
        """``--help`` explica cada forma de agrupar con un ejemplo de ruta."""
        with pytest.raises(SystemExit):
            _main(monkeypatch, "--help")
        ayuda = capsys.readouterr().out
        assert "Cómo agrupar (--agrupar):" in ayuda
        assert "Imagenes/2024/03/foto.jpg" in ayuda

    def test_vigilar_por_fecha(self, carpeta: Path, monkeypatch):
        """``--vigilar`` recibe la forma de agrupar elegida."""
        capturado: dict[str, object] = {}

        def vigilar_falso(carpeta: Path, **opciones: object) -> int:
            capturado.update(opciones)
            return 0

        monkeypatch.setattr(aplicacion, "ejecutar_vigilar", vigilar_falso)
        assert _main(monkeypatch, "--carpeta", str(carpeta), "--vigilar", "-a", "fecha") == 0
        assert capturado["agrupar"] is CriterioAgrupacion.FECHA

    def test_estadisticas_con_nombres_largos_alineadas(self, carpeta: Path, monkeypatch, capsys):
        """Con nombres como ``Documentos/2021/06``, las barras de las estadísticas siguen empezando
        en la misma columna.
        """
        _main(monkeypatch, "--carpeta", str(carpeta), "--agrupar", "tipo-fecha")
        capsys.readouterr()

        assert _main(monkeypatch, "--carpeta", str(carpeta), "--estadisticas") == 0
        lineas = [
            linea
            for linea in _sin_colores(capsys.readouterr().out).splitlines()
            if "fichero(s) (" in linea
        ]
        assert len(lineas) == 3
        assert len({linea.index("█") for linea in lineas}) == 1  # barras en la misma columna


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
    """La pregunta «Agrupar» del menú interactivo."""

    def test_organizar_por_fecha_no_pregunta_por_las_categorias(self, carpeta: Path, monkeypatch):
        """Por fecha no hacen falta categorías: no se pregunta por su configuración y la elección se
        guarda.
        """
        # 01, carpeta, agrupar=2 (fecha), duplicados=ENTER, backup=n, integridad=ENTER, pausa, salir
        preguntas = _menu(monkeypatch, ["01", str(carpeta), "2", "", "n", "", "", "11"])

        assert (carpeta / "2020" / "01" / "informe.pdf").exists()
        assert not [p for p in preguntas if "config" in p]
        assert ConfiguracionUsuario().cargar().criterio_agrupacion() is CriterioAgrupacion.FECHA

    def test_enter_repite_la_ultima_forma_de_agrupar(self, carpeta: Path, monkeypatch):
        """La pregunta ofrece con ENTER la última forma elegida."""
        cfg = ConfiguracionUsuario().cargar()
        cfg.establecer("agrupar", "tipo-fecha")
        cfg.guardar()

        # 02 (simular), carpeta, agrupar, duplicados y config=ENTER, pausa, salir
        preguntas = _menu(monkeypatch, ["02", str(carpeta), "", "", "", "", "11"])

        pregunta = next(p for p in preguntas if "Agrupar" in p)
        assert "ENTER = Tipo y fecha" in _sin_colores(pregunta)

    def test_una_respuesta_no_valida_mantiene_la_actual(self, carpeta: Path, monkeypatch, capsys):
        """Una opción que no existe avisa y se sigue con la forma actual."""
        _menu(monkeypatch, ["01", str(carpeta), "9", "", "", "n", "", "", "11"])

        assert (carpeta / "Documentos" / "informe.pdf").exists()
        assert "se mantiene «Tipo»" in _sin_colores(capsys.readouterr().out)

    def test_muestra_un_ejemplo_de_cada_forma(self, carpeta: Path, monkeypatch, capsys):
        """Cada forma de agrupar se presenta con su ruta de ejemplo."""
        _menu(monkeypatch, ["02", str(carpeta), "", "", "", "", "11"])

        salida = capsys.readouterr().out
        for descripcion in DESCRIPCIONES_AGRUPACION.values():
            assert descripcion.ejemplo in salida

    def test_vigilar_por_tipo_y_fecha(self, carpeta: Path, monkeypatch):
        """La opción 07 (vigilar) también pregunta cómo agrupar y lo pasa al vigilante."""
        capturado: dict[str, object] = {}

        def vigilar_falso(carpeta: Path, **opciones: object) -> int:
            capturado.update(opciones)
            return 0

        monkeypatch.setattr(interactivo, "ejecutar_vigilar", vigilar_falso)
        # 07, carpeta, agrupar=3, ENTER en duplicados/config, subcarpetas=n, ENTER, pausa, salir
        _menu(monkeypatch, ["07", str(carpeta), "3", "", "", "n", "", "", "11"])

        assert capturado["agrupar"] is CriterioAgrupacion.TIPO_FECHA
