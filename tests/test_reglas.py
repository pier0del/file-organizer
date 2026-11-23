"""Tests de las reglas de cada carpeta: validación, fichero de reglas, organizador, deshacer,
vigilante, consola y menú."""

import builtins
import json
import logging
import os
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path, PurePath

import pytest

from file_organizer.clasificador import CriterioAgrupacion
from file_organizer.cli import aplicacion, interactivo
from file_organizer.excepciones import ErrorReglas
from file_organizer.organizador import OrganizadorArchivos
from file_organizer.reglas import NOMBRE_ARCHIVO_REGLAS, Regla, ReglasCarpeta
from file_organizer.vigilante import VigilanteCarpeta

MARZO_2024 = datetime(2024, 3, 5, 12, 0)


def fichero(ruta: Path, fecha: datetime | None = None, datos: bytes | None = None) -> Path:
    """Crea el fichero (y su carpeta) con esa fecha; si no se dan datos, su nombre como contenido,
    para que no salgan duplicados.
    """
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(datos if datos is not None else ruta.name.encode())
    if fecha is not None:
        os.utime(ruta, (fecha.timestamp(), fecha.timestamp()))
    return ruta


def contenido(carpeta: Path) -> list[str]:
    """Ficheros bajo ``carpeta`` (sin los ocultos del programa), en formato posix."""
    return sorted(
        p.relative_to(carpeta).as_posix()
        for p in carpeta.rglob("*")
        if p.is_file() and not any(parte.startswith(".") for parte in p.relative_to(carpeta).parts)
    )


def escribir_reglas(carpeta: Path, *reglas: tuple[str, str]) -> Path:
    """Escribe el fichero de reglas de la carpeta con esos pares (patrón, carpeta)."""
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / NOMBRE_ARCHIVO_REGLAS
    datos = {"reglas": [{"patron": p, "carpeta": c} for p, c in reglas]}
    ruta.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
    return ruta


# ------------------------------------------------------------------------ regla


class TestRegla:
    """Una regla por sí sola: su patrón, su carpeta y la fecha en la carpeta."""

    @pytest.mark.parametrize(
        ("patron", "nombre", "coincide"),
        [
            ("*.pdf", "informe.pdf", True),
            ("*.pdf", "INFORME.PDF", True),
            ("factura*", "Factura_marzo.pdf", True),
            ("factura*", "mi factura.pdf", False),
            ("*factura*", "mi factura.pdf", True),
            ("IMG_????.jpg", "IMG_0001.jpg", True),
            ("IMG_????.jpg", "IMG_01.jpg", False),
            ("notas.txt", "notas.txt", True),
            ("[ab]*.txt", "b1.txt", True),
        ],
    )
    def test_patron_sobre_el_nombre(self, patron: str, nombre: str, coincide: bool):
        """El patrón se compara con el nombre entero, sin distinguir mayúsculas, con ``*``, ``?`` y
        ``[...]``.
        """
        assert Regla.crear(patron, "X").coincide(nombre) is coincide

    def test_normaliza_la_carpeta(self):
        """Se quitan los espacios de alrededor y las barras de Windows pasan a ``/``."""
        regla = Regla.crear("  *.pdf ", "Trabajo\\Facturas/ ")
        assert (regla.patron, regla.carpeta) == ("*.pdf", "Trabajo/Facturas")

    @pytest.mark.parametrize(
        "carpeta",
        [
            "",
            "   ",
            "/Facturas",
            "C:/Facturas",
            "C:Facturas",
            "\\\\servidor\\compartida",
            "../fuera",
            "a/../b",
            "a//b",
            ".oculta",
            "Facturas/{semana}",
            "Factu?ras",
            'Mis "cosas"',
        ],
    )
    def test_carpetas_que_no_valen(self, carpeta: str):
        """La carpeta tiene que quedar dentro del destino y con un nombre válido: ni rutas
        absolutas, ni ``..``, ni ocultas, ni marcadores desconocidos.
        """
        with pytest.raises(ErrorReglas):
            Regla.crear("*.pdf", carpeta)

    @pytest.mark.parametrize("patron", ["", "  ", "Docs/*.pdf", "Docs\\*.pdf"])
    def test_patrones_que_no_valen(self, patron: str):
        """El patrón no puede estar vacío ni llevar carpetas: se aplica solo al nombre."""
        with pytest.raises(ErrorReglas):
            Regla.crear(patron, "Documentos")

    def test_fecha_en_la_carpeta(self, tmp_path: Path):
        """``{año}`` (o ``{anio}``) y ``{mes}`` se cambian por la fecha del fichero."""
        ruta = fichero(tmp_path / "factura.pdf", MARZO_2024)
        assert Regla.crear("*", "Facturas/{año}/{mes}").carpeta_para(ruta) == "Facturas/2024/03"
        assert Regla.crear("*", "F/{anio}").carpeta_para(ruta) == "F/2024"
        assert Regla.crear("*", "Facturas").carpeta_para(ruta) == "Facturas"

    @pytest.mark.parametrize(
        ("carpeta_regla", "carpeta", "es_suya"),
        [
            ("Facturas", "Facturas", True),
            ("Facturas", "Facturas/2024", False),
            ("Facturas/{año}", "Facturas/2024", True),
            ("Facturas/{año}", "Facturas/24", False),
            ("Fotos/{año}/{mes}", "Fotos/2024/03", True),
            ("Fotos/{año}/{mes}", "Fotos/2024/13", False),
            ("Mis cosas (1)", "Mis cosas (1)", True),
        ],
    )
    def test_reconoce_sus_carpetas(self, carpeta_regla: str, carpeta: str, es_suya: bool):
        """Una carpeta es de la regla si coincide con ella, con números válidos en lugar de
        ``{año}`` y ``{mes}``.
        """
        assert Regla.crear("*", carpeta_regla).es_su_carpeta(PurePath(carpeta)) is es_suya


# ------------------------------------------------------------ fichero de reglas


class TestReglasCarpeta:
    """El fichero de reglas de una carpeta."""

    def test_sin_fichero_no_hay_reglas(self, tmp_path: Path):
        """Sin fichero, la lista está vacía."""
        assert ReglasCarpeta(tmp_path).cargar() == []

    def test_agregar_quitar_y_mover_se_guardan(self, tmp_path: Path):
        """Cada cambio se guarda en el fichero, en el orden en que quedan las reglas."""
        reglas = ReglasCarpeta(tmp_path)
        reglas.agregar(Regla.crear("*.pdf", "Documentos"))
        reglas.agregar(Regla.crear("factura*", "Facturas/{año}"))
        assert reglas.mover(2, -1) == 1  # la de facturas pasa delante

        guardado = json.loads((tmp_path / NOMBRE_ARCHIVO_REGLAS).read_text(encoding="utf-8"))
        assert guardado == {
            "reglas": [
                {"patron": "factura*", "carpeta": "Facturas/{año}"},
                {"patron": "*.pdf", "carpeta": "Documentos"},
            ]
        }
        assert reglas.quitar(2) == Regla("*.pdf", "Documentos")
        assert ReglasCarpeta(tmp_path).cargar() == [Regla("factura*", "Facturas/{año}")]

    def test_mover_en_un_extremo_no_hace_nada(self, tmp_path: Path):
        """Subir la primera regla la deja donde está."""
        reglas = ReglasCarpeta(tmp_path)
        reglas.agregar(Regla.crear("a*", "A"))
        assert reglas.mover(1, -1) == 1

    def test_numero_que_no_existe(self, tmp_path: Path):
        """Quitar una regla que no hay explica que no existe."""
        reglas = ReglasCarpeta(tmp_path)
        with pytest.raises(ErrorReglas, match="No hay regla 3"):
            reglas.quitar(3)

    def test_gana_la_primera_que_coincide(self, tmp_path: Path):
        """Si varias coinciden, se aplica la primera de la lista; si ninguna, no hay regla."""
        escribir_reglas(tmp_path, ("factura*", "Facturas"), ("*.pdf", "Documentos"))
        reglas = ReglasCarpeta(tmp_path)
        assert reglas.regla_para("factura.pdf") == Regla("factura*", "Facturas")
        assert reglas.regla_para("informe.pdf") == Regla("*.pdf", "Documentos")
        assert reglas.regla_para("foto.jpg") is None

    @pytest.mark.parametrize(
        ("texto", "motivo"),
        [
            ("{no es json", "no son un JSON válido"),
            ('["*.pdf"]', 'lista "reglas"'),
            ('{"reglas": [{"patron": "*.pdf"}]}', 'debe tener "patron" y "carpeta"'),
            ('{"reglas": [{"patron": "*.pdf", "carpeta": "../x"}]}', "La regla 1"),
        ],
    )
    def test_fichero_que_no_vale(self, tmp_path: Path, texto: str, motivo: str):
        """Cada problema del fichero se explica con su motivo."""
        (tmp_path / NOMBRE_ARCHIVO_REGLAS).write_text(texto, encoding="utf-8")
        with pytest.raises(ErrorReglas, match=re.escape(motivo)):
            ReglasCarpeta(tmp_path).cargar()

    def test_se_vuelven_a_leer_si_el_fichero_cambia(self, tmp_path: Path):
        """Si el fichero cambia (p. ej. mientras se vigila), se usan las reglas nuevas."""
        escribir_reglas(tmp_path, ("*.pdf", "Documentos"))
        reglas = ReglasCarpeta(tmp_path)
        assert reglas.regla_para("a.pdf") == Regla("*.pdf", "Documentos")

        ruta = escribir_reglas(tmp_path, ("*.pdf", "PDF"))
        os.utime(ruta, ns=(1, 1))  # la fecha cambia aunque el reloj sea poco preciso
        assert reglas.regla_para("a.pdf") == Regla("*.pdf", "PDF")

    def test_si_deja_de_valer_se_siguen_usando_las_anteriores(self, tmp_path: Path, caplog):
        """Si el fichero se estropea después de leerlo, se avisa y se siguen usando las reglas que
        había.
        """
        escribir_reglas(tmp_path, ("*.pdf", "Documentos"))
        reglas = ReglasCarpeta(tmp_path)
        reglas.cargar()
        ruta = tmp_path / NOMBRE_ARCHIVO_REGLAS
        ruta.write_text("{roto", encoding="utf-8")
        os.utime(ruta, ns=(1, 1))

        with caplog.at_level(logging.WARNING):
            assert reglas.regla_para("a.pdf") == Regla("*.pdf", "Documentos")
        assert "Se siguen usando las reglas anteriores" in caplog.text


# ----------------------------------------------------------- organizar y deshacer


@pytest.fixture
def descargas(tmp_path: Path) -> Path:
    """Carpeta con facturas, un informe, fotos y tres reglas: facturas por año, PDF a
    «Documentos/PDF» y fotos del móvil.
    """
    carpeta = tmp_path / "Descargas"
    fichero(carpeta / "factura_marzo.pdf", MARZO_2024)
    fichero(carpeta / "Factura-Luz.PDF", MARZO_2024)
    fichero(carpeta / "informe.pdf")
    fichero(carpeta / "IMG_0001.jpg")
    fichero(carpeta / "foto.png")
    escribir_reglas(
        carpeta,
        ("factura*", "Facturas/{año}"),
        ("*.pdf", "Documentos/PDF"),
        ("IMG_*", "Fotos del móvil"),
    )
    return carpeta


class TestOrganizar:
    """Organizar con reglas: cada una hace de categoría propia."""

    def test_las_reglas_mandan_y_lo_demas_como_siempre(self, descargas: Path):
        """Lo que coincide va adonde dice su regla y lo demás por tipo; el resumen cuenta cuántos
        movió cada regla.
        """
        org = OrganizadorArchivos(carpeta_origen=descargas)
        org.ejecutar()

        assert contenido(descargas) == [
            "Documentos/PDF/informe.pdf",
            "Facturas/2024/Factura-Luz.PDF",
            "Facturas/2024/factura_marzo.pdf",
            "Fotos del móvil/IMG_0001.jpg",
            "Imagenes/foto.png",
        ]
        assert org.resumen_reglas() == (
            "Reglas de la carpeta aplicadas a 4 fichero(s): «factura*» 2, «*.pdf» 1, «IMG_*» 1."
        )

    @pytest.mark.parametrize(
        ("agrupar", "foto", "informe"),
        [
            (CriterioAgrupacion.TIPO, "Imagenes/foto.png", "Documentos/PDF/informe.pdf"),
            (CriterioAgrupacion.FECHA, "2024/03/foto.png", "Documentos/PDF/2024/03/informe.pdf"),
            (
                CriterioAgrupacion.TIPO_FECHA,
                "Imagenes/2024/03/foto.png",
                "Documentos/PDF/2024/03/informe.pdf",
            ),
        ],
    )
    def test_la_regla_hace_de_categoria_y_la_agrupacion_pone_la_fecha(
        self, descargas: Path, agrupar: CriterioAgrupacion, foto: str, informe: str
    ):
        """Agrupando por fecha, la fecha va debajo de la carpeta de la regla, como con una
        categoría.
        """
        for nombre in ("foto.png", "informe.pdf"):
            os.utime(descargas / nombre, (MARZO_2024.timestamp(), MARZO_2024.timestamp()))
        OrganizadorArchivos(carpeta_origen=descargas, agrupar=agrupar).ejecutar()
        organizados = contenido(descargas)
        assert foto in organizados and informe in organizados

    @pytest.mark.parametrize("agrupar", list(CriterioAgrupacion))
    def test_una_regla_con_su_fecha_no_recibe_otra(
        self, descargas: Path, agrupar: CriterioAgrupacion
    ):
        """«Facturas/{año}» se usa tal cual: nunca «Facturas/2024/2024/03»."""
        OrganizadorArchivos(carpeta_origen=descargas, agrupar=agrupar).ejecutar()
        assert "Facturas/2024/factura_marzo.pdf" in contenido(descargas)

    def test_la_fecha_de_las_reglas_cuenta_en_el_resumen_de_fechas(self, tmp_path: Path):
        """La fecha que se usa para la carpeta de una regla cuenta en el resumen de fechas."""
        carpeta = tmp_path / "c"
        fichero(carpeta / "informe.pdf", MARZO_2024)
        escribir_reglas(carpeta, ("*.pdf", "Documentos"))
        org = OrganizadorArchivos(carpeta_origen=carpeta, agrupar=CriterioAgrupacion.FECHA)
        org.ejecutar()
        assert org.resumen_fechas() == (
            "Fechas: 1 de la fecha del propio fichero, porque no tenía metadatos con fecha."
        )

    @pytest.mark.parametrize("agrupar", list(CriterioAgrupacion))
    def test_cuentan_todas_las_fechas_tambien_las_de_reglas_con_su_fecha(
        self, descargas: Path, agrupar: CriterioAgrupacion
    ):
        """Antes, las fechas de «Facturas/{año}» no se contaban (salía 3 de 4 ficheros)."""
        org = OrganizadorArchivos(carpeta_origen=descargas, agrupar=agrupar)
        org.ejecutar()
        # Dos facturas por su regla con {año} y, al agrupar por fecha, los otros tres.
        esperadas = 2 if agrupar is CriterioAgrupacion.TIPO else 5
        tenian = "tenía" if esperadas == 1 else "tenían"
        assert org.resumen_fechas() == (
            f"Fechas: {esperadas} de la fecha del propio fichero, porque no {tenian} metadatos "
            "con fecha."
        )

    def test_sin_fechas_no_hay_resumen(self, tmp_path: Path):
        """Por tipo y sin reglas con fecha, no hay resumen de fechas."""
        carpeta = tmp_path / "c"
        fichero(carpeta / "informe.pdf")
        org = OrganizadorArchivos(carpeta_origen=carpeta)
        org.ejecutar()
        assert org.resumen_fechas() is None

    def test_deshacer_por_fecha_con_reglas_lo_deja_todo_como_estaba(self, descargas: Path):
        """Tras organizar por tipo y fecha con reglas, deshacer no deja ni una carpeta."""
        antes = contenido(descargas)
        OrganizadorArchivos(
            carpeta_origen=descargas, agrupar=CriterioAgrupacion.TIPO_FECHA
        ).ejecutar()
        OrganizadorArchivos(carpeta_origen=descargas).deshacer()
        assert contenido(descargas) == antes
        assert [p.name for p in descargas.iterdir() if p.is_dir()] == []

    def test_en_otra_carpeta_de_destino(self, descargas: Path, tmp_path: Path):
        """Las carpetas de las reglas se crean en el destino."""
        destino = tmp_path / "Archivo"
        OrganizadorArchivos(carpeta_origen=descargas, carpeta_destino=destino).ejecutar()
        assert (destino / "Facturas" / "2024" / "factura_marzo.pdf").exists()

    def test_simular_no_mueve_nada(self, descargas: Path):
        """Simulando no se mueve nada, pero cada fichero tiene la carpeta de su regla."""
        antes = contenido(descargas)
        org = OrganizadorArchivos(carpeta_origen=descargas, modo_simulacion=True)
        org.ejecutar()
        assert contenido(descargas) == antes
        destinos = {r.origen.name: r.categoria for r in org.ultimos_movimientos}
        assert destinos["factura_marzo.pdf"] == "Facturas/2024"

    def test_sin_reglas_todo_igual_que_antes(self, tmp_path: Path):
        """Sin fichero de reglas, se organiza por tipo y no hay resumen de reglas."""
        carpeta = tmp_path / "c"
        fichero(carpeta / "informe.pdf")
        org = OrganizadorArchivos(carpeta_origen=carpeta)
        org.ejecutar()
        assert contenido(carpeta) == ["Documentos/informe.pdf"]
        assert org.resumen_reglas() is None

    def test_el_fichero_de_reglas_no_se_organiza(self, descargas: Path):
        """El propio fichero de reglas se queda."""
        OrganizadorArchivos(carpeta_origen=descargas).ejecutar()
        assert (descargas / NOMBRE_ARCHIVO_REGLAS).exists()

    def test_reglas_rotas_no_mueven_nada(self, tmp_path: Path):
        """Si el fichero de reglas está mal, no se organiza nada (no se aplicaría lo que el usuario
        quería).
        """
        carpeta = tmp_path / "c"
        fichero(carpeta / "informe.pdf")
        (carpeta / NOMBRE_ARCHIVO_REGLAS).write_text("{roto", encoding="utf-8")
        with pytest.raises(ErrorReglas):
            OrganizadorArchivos(carpeta_origen=carpeta).ejecutar()
        assert contenido(carpeta) == ["informe.pdf"]

    def test_reglas_rotas_no_impiden_deshacer(self, descargas: Path):
        """Con el fichero de reglas roto se puede deshacer igual: no hacen falta para eso."""
        antes = contenido(descargas)
        OrganizadorArchivos(carpeta_origen=descargas).ejecutar()
        (descargas / NOMBRE_ARCHIVO_REGLAS).write_text("{roto", encoding="utf-8")

        resultado = OrganizadorArchivos(carpeta_origen=descargas).deshacer()

        assert resultado is not None and resultado.exitosos == 5
        assert contenido(descargas) == antes

    def test_deshacer_borra_las_carpetas_de_las_reglas(self, descargas: Path):
        """Las carpetas que crearon las reglas se quitan al deshacer si quedan vacías."""
        OrganizadorArchivos(carpeta_origen=descargas).ejecutar()
        OrganizadorArchivos(carpeta_origen=descargas).deshacer()
        assert [p.name for p in descargas.iterdir() if p.is_dir()] == []


# --------------------------------------------------------------------- vigilante


class TestVigilante:
    """El vigilante con reglas."""

    def test_lo_que_esta_en_carpetas_de_reglas_ya_esta_organizado(self, descargas: Path):
        """Lo que está en la carpeta de una regla se da por organizado; en una a medias, no."""
        org = OrganizadorArchivos(carpeta_origen=descargas)
        assert org.esta_en_carpeta_de_categoria(descargas / "Facturas" / "2024" / "f.pdf")
        assert org.esta_en_carpeta_de_categoria(descargas / "Documentos" / "PDF" / "a.pdf")
        assert not org.esta_en_carpeta_de_categoria(descargas / "Facturas" / "f.pdf")

    @pytest.mark.parametrize(
        ("agrupar", "reconoce"),
        [
            (CriterioAgrupacion.TIPO, False),
            (CriterioAgrupacion.FECHA, True),
            (CriterioAgrupacion.TIPO_FECHA, True),
        ],
    )
    def test_reconoce_la_carpeta_de_la_regla_con_fecha_debajo(
        self, descargas: Path, agrupar: CriterioAgrupacion, reconoce: bool
    ):
        """Agrupando por fecha, también lo que está en la fecha bajo la carpeta de la regla."""
        org = OrganizadorArchivos(carpeta_origen=descargas, agrupar=agrupar)
        assert (
            org.esta_en_carpeta_de_categoria(
                descargas / "Documentos" / "PDF" / "2024" / "03" / "a.pdf"
            )
            is reconoce
        )
        # Una regla con su propia fecha no crea «Facturas/2024/2024/03».
        assert not org.esta_en_carpeta_de_categoria(
            descargas / "Facturas" / "2024" / "2024" / "03" / "f.pdf"
        )

    def test_reglas_rotas_no_dejan_empezar(self, tmp_path: Path):
        """Si el fichero de reglas está mal, el vigilante no empieza."""
        (tmp_path / NOMBRE_ARCHIVO_REGLAS).write_text("{roto", encoding="utf-8")
        vigilante = VigilanteCarpeta(carpeta=tmp_path, notificador=lambda n, m: None)
        with pytest.raises(ErrorReglas):
            vigilante.iniciar()

    def test_aplica_las_reglas_y_lo_dice(self, tmp_path: Path):
        """Un fichero que llega va adonde dice su regla, el aviso dice qué regla se aplicó y no se
        vuelve a mover.
        """
        escribir_reglas(tmp_path, ("factura*", "Facturas/{año}"))
        avisos: list[tuple[str, str]] = []
        vigilante = VigilanteCarpeta(
            carpeta=tmp_path,
            espera=0.3,
            recursivo=True,
            notificador=lambda nivel, mensaje: avisos.append((nivel, mensaje)),
        )
        hilo = threading.Thread(target=vigilante.iniciar, daemon=True)
        hilo.start()
        try:
            limite = time.monotonic() + 10
            while not vigilante.observador.is_alive() and time.monotonic() < limite:
                time.sleep(0.05)
            fichero(tmp_path / "factura.pdf", MARZO_2024)
            organizado = tmp_path / "Facturas" / "2024" / "factura.pdf"
            while not organizado.exists() and time.monotonic() < limite:
                time.sleep(0.05)
            assert organizado.exists()
            time.sleep(0.8)  # margen para que un segundo movimiento (el fallo) ocurriera
        finally:
            vigilante.detener()
            hilo.join(timeout=10)

        assert contenido(tmp_path) == ["Facturas/2024/factura.pdf"]
        assert ("exito", "factura.pdf -> Facturas/2024/ (regla «factura*»)") in avisos


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
    """Las opciones de reglas de la consola."""

    def test_agregar_listar_y_quitar(self, tmp_path: Path, monkeypatch, capsys):
        """``--agregar-regla`` añade al final, ``--reglas`` las lista numeradas y alineadas y
        ``--quitar-regla`` quita por número.
        """
        carpeta = tmp_path / "c"
        carpeta.mkdir()
        assert _main(monkeypatch, "-f", str(carpeta), "--agregar-regla", "*.pdf", "Documentos") == 0
        assert (
            _main(monkeypatch, "-f", str(carpeta), "--agregar-regla", "factura*", "Facturas/{año}")
            == 0
        )
        capsys.readouterr()

        assert _main(monkeypatch, "-f", str(carpeta), "--reglas") == 0
        salida = _sin_colores(capsys.readouterr().out)
        assert "1. *.pdf     →  Documentos" in salida
        assert "2. factura*  →  Facturas/{año}" in salida

        assert _main(monkeypatch, "-f", str(carpeta), "--quitar-regla", "1") == 0
        assert "Regla quitada: *.pdf  →  Documentos" in _sin_colores(capsys.readouterr().out)
        assert ReglasCarpeta(carpeta).cargar() == [Regla("factura*", "Facturas/{año}")]

    def test_sin_reglas_dice_como_anadir_una(self, tmp_path: Path, monkeypatch, capsys):
        """Si no hay reglas, ``--reglas`` explica cómo añadir una."""
        assert _main(monkeypatch, "-f", str(tmp_path), "--reglas") == 0
        assert "--agregar-regla" in capsys.readouterr().out

    def test_regla_que_no_vale(self, tmp_path: Path, monkeypatch, capsys):
        """Una regla que no vale termina con error, explica por qué y no se guarda."""
        codigo = _main(monkeypatch, "-f", str(tmp_path), "--agregar-regla", "*.pdf", "C:/Docs")
        assert codigo == 1
        assert "dentro del destino" in capsys.readouterr().out
        assert not (tmp_path / NOMBRE_ARCHIVO_REGLAS).exists()

    def test_quitar_una_que_no_existe(self, tmp_path: Path, monkeypatch, capsys):
        """Quitar un número que no existe termina con error y lo explica."""
        assert _main(monkeypatch, "-f", str(tmp_path), "--quitar-regla", "4") == 1
        assert "No hay regla 4" in capsys.readouterr().out

    def test_organizar_aplica_las_reglas_y_lo_resume(self, descargas: Path, monkeypatch, capsys):
        """Organizar aplica las reglas y el resumen dice a cuántos ficheros."""
        assert _main(monkeypatch, "--carpeta", str(descargas)) == 0
        assert (descargas / "Fotos del móvil" / "IMG_0001.jpg").exists()
        assert "Reglas de la carpeta aplicadas a 4 fichero(s)" in capsys.readouterr().out

    def test_reglas_rotas_se_explican_sin_traza(self, descargas: Path, monkeypatch, capsys):
        """Con el fichero de reglas roto, organizar y simular dan un mensaje claro, sin la traza de
        Python.
        """
        (descargas / NOMBRE_ARCHIVO_REGLAS).write_text("{roto", encoding="utf-8")
        assert _main(monkeypatch, "--carpeta", str(descargas)) == 1
        assert _main(monkeypatch, "--carpeta", str(descargas), "--simular") == 1
        salida = _sin_colores(capsys.readouterr().out)
        assert salida.count("Reglas: Las reglas de") == 2
        assert "Traceback" not in salida

    def test_vigilar_con_reglas_rotas(self, descargas: Path, monkeypatch, capsys):
        """Vigilar con el fichero de reglas roto termina con error y dice qué le pasa."""
        (descargas / NOMBRE_ARCHIVO_REGLAS).write_text("{roto", encoding="utf-8")
        assert _main(monkeypatch, "--carpeta", str(descargas), "--vigilar") == 1
        assert "no son un JSON válido" in capsys.readouterr().out

    def test_la_ayuda_explica_las_reglas(self, monkeypatch, capsys):
        """``--help`` tiene una sección sobre las reglas."""
        with pytest.raises(SystemExit):
            _main(monkeypatch, "--help")
        ayuda = capsys.readouterr().out
        assert "Reglas de la carpeta (--reglas, --agregar-regla, --quitar-regla):" in ayuda


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
    """La opción 10 del menú (reglas de una carpeta)."""

    def test_anadir_y_quitar_reglas(self, tmp_path: Path, monkeypatch, capsys):
        """Desde el submenú se añaden y se quitan reglas, y se confirma cada cambio."""
        carpeta = tmp_path / "c"
        carpeta.mkdir()
        # 10, carpeta, [1] añadir *.pdf → Documentos, [1] añadir factura* → Facturas,
        # [2] quitar la 1, [3] volver, 11 salir
        _menu(
            monkeypatch,
            [
                "10",
                str(carpeta),
                "1",
                "*.pdf",
                "Documentos",
                "1",
                "factura*",
                "Facturas",
                "2",
                "1",
                "3",
                "11",
            ],
        )

        assert ReglasCarpeta(carpeta).cargar() == [Regla("factura*", "Facturas")]
        salida = _sin_colores(capsys.readouterr().out)
        assert "Regla añadida: *.pdf  →  Documentos" in salida
        assert "Regla quitada: *.pdf  →  Documentos" in salida

    def test_regla_que_no_vale_se_explica(self, tmp_path: Path, monkeypatch, capsys):
        """Una regla que no vale se explica y no se guarda."""
        _menu(monkeypatch, ["10", str(tmp_path), "1", "*.pdf", "../fuera", "3", "11"])
        assert "no puede llevar «..»" in _sin_colores(capsys.readouterr().out)
        assert ReglasCarpeta(tmp_path).cargar() == []

    def test_la_opcion_de_salir_es_la_11(self, monkeypatch, capsys):
        """Con las reglas en la 10, salir pasa a ser la 11."""
        _menu(monkeypatch, ["11"])
        salida = _sin_colores(capsys.readouterr().out)
        assert "[10] Reglas de una carpeta" in salida
        assert "[11] Salir del programa" in salida
