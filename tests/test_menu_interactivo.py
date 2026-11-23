"""Tests de integración del menú interactivo, simulando lo que se teclea.

Prueban las preguntas del backup, de vigilar subcarpetas y del modo de integridad, y cómo el
menú recuerda las últimas respuestas. Lo que se teclea se simula sustituyendo ``input``.
"""

import builtins
from pathlib import Path

import pytest

from file_organizer.cli import interactivo
from file_organizer.config_usuario import ConfiguracionUsuario
from file_organizer.integridad import DESCRIPCIONES_INTEGRIDAD, ModoIntegridad
from file_organizer.organizador import NOMBRE_CARPETA_BACKUPS, OrganizadorArchivos


@pytest.fixture(autouse=True)
def _entorno_aislado(tmp_path, monkeypatch):
    """Aísla el logging (cwd temporal) y la config persistente en un fichero temporal."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FILE_ORGANIZER_CONFIG", str(tmp_path / "uc.json"))


def _simular_entrada(monkeypatch, respuestas: list[str]) -> None:
    """Hace que cada llamada a input() devuelva la siguiente respuesta de la lista."""
    it = iter(respuestas)

    def input_falso(prompt: str = "") -> str:
        return next(it)

    monkeypatch.setattr(builtins, "input", input_falso)


@pytest.fixture
def carpeta_con_ficheros(tmp_path: Path) -> Path:
    """Una carpeta con una foto y un PDF sin organizar."""
    carpeta = tmp_path / "origen"
    carpeta.mkdir()
    (carpeta / "a.jpg").write_text("x")
    (carpeta / "b.pdf").write_text("x")
    return carpeta


class TestOrganizarConBackup:
    """La pregunta del backup al organizar desde el menú."""

    def test_backup_si_organiza_y_crea_zip(self, monkeypatch, carpeta_con_ficheros: Path):
        """Con «s», organiza y deja un zip del antes."""
        # 01, carpeta, ENTER en agrupar/duplicados/config, backup=s, ENTER, pausa, salir
        _simular_entrada(
            monkeypatch, ["01", str(carpeta_con_ficheros), "", "", "", "s", "", "", "11"]
        )

        codigo = interactivo.ejecutar_menu()

        assert codigo == 0
        assert (carpeta_con_ficheros / "Imagenes" / "a.jpg").exists()
        assert (carpeta_con_ficheros / "Documentos" / "b.pdf").exists()
        backups = list((carpeta_con_ficheros / NOMBRE_CARPETA_BACKUPS).glob("*.zip"))
        assert len(backups) == 1

    def test_backup_no_organiza_sin_zip(self, monkeypatch, carpeta_con_ficheros: Path):
        """Con «n», organiza sin crear el zip."""
        _simular_entrada(
            monkeypatch, ["01", str(carpeta_con_ficheros), "", "", "", "n", "", "", "11"]
        )

        interactivo.ejecutar_menu()

        assert (carpeta_con_ficheros / "Imagenes" / "a.jpg").exists()
        assert not (carpeta_con_ficheros / NOMBRE_CARPETA_BACKUPS).exists()

    def test_organizar_guarda_ultima_carpeta_en_config(
        self, monkeypatch, carpeta_con_ficheros: Path
    ):
        """La carpeta organizada se guarda para proponerla la próxima vez."""
        _simular_entrada(
            monkeypatch, ["01", str(carpeta_con_ficheros), "", "", "", "n", "", "", "11"]
        )

        interactivo.ejecutar_menu()

        from file_organizer.config_usuario import ConfiguracionUsuario

        cfg = ConfiguracionUsuario().cargar()
        assert cfg.obtener("ultima_carpeta") == str(carpeta_con_ficheros)


class TestVigilanteRecursivo:
    """La pregunta de vigilar también las subcarpetas."""

    def test_opcion_vigilar_pasa_recursivo_true(self, monkeypatch, carpeta_con_ficheros: Path):
        """Con «s», se vigila con subcarpetas."""
        capturado: dict = {}

        def vigilar_falso(
            carpeta,
            config=None,
            destino=None,
            recursivo=False,
            modo_integridad=None,
            agrupar=None,
            duplicados=None,
        ) -> int:
            capturado["carpeta"] = carpeta
            capturado["recursivo"] = recursivo
            return 0

        monkeypatch.setattr(interactivo, "ejecutar_vigilar", vigilar_falso)
        # 07, carpeta, ENTER en agrupar/duplicados/config, recursivo=s, ENTER, pausa, salir
        _simular_entrada(
            monkeypatch, ["07", str(carpeta_con_ficheros), "", "", "", "s", "", "", "11"]
        )

        interactivo.ejecutar_menu()

        assert capturado["recursivo"] is True

    def test_opcion_vigilar_recursivo_false_por_defecto(
        self, monkeypatch, carpeta_con_ficheros: Path
    ):
        """Con «n», solo la carpeta."""
        capturado: dict = {}

        def vigilar_falso(
            carpeta,
            config=None,
            destino=None,
            recursivo=False,
            modo_integridad=None,
            agrupar=None,
            duplicados=None,
        ) -> int:
            capturado["recursivo"] = recursivo
            return 0

        monkeypatch.setattr(interactivo, "ejecutar_vigilar", vigilar_falso)
        _simular_entrada(
            monkeypatch, ["07", str(carpeta_con_ficheros), "", "", "", "n", "", "", "11"]
        )

        interactivo.ejecutar_menu()

        assert capturado["recursivo"] is False


class TestSalir:
    """Salir del menú."""

    def test_opcion_11_sale_con_codigo_0(self, monkeypatch):
        """La opción 11 sale con el código 0."""
        _simular_entrada(monkeypatch, ["11"])
        assert interactivo.ejecutar_menu() == 0


class TestModoIntegridad:
    """La pregunta del modo de integridad."""

    def test_elegir_un_modo_lo_usa_y_lo_recuerda(self, monkeypatch, carpeta_con_ficheros: Path):
        """El modo elegido se usa y queda guardado en las preferencias."""
        capturado: dict = {}

        def vigilar_falso(
            carpeta,
            config=None,
            destino=None,
            recursivo=False,
            modo_integridad=None,
            agrupar=None,
            duplicados=None,
        ):
            capturado["modo"] = modo_integridad
            return 0

        monkeypatch.setattr(interactivo, "ejecutar_vigilar", vigilar_falso)
        # 07, carpeta, ENTER en agrupar/duplicados/config, recursivo=n, integridad=3, pausa, salir
        _simular_entrada(
            monkeypatch, ["07", str(carpeta_con_ficheros), "", "", "", "n", "3", "", "11"]
        )

        interactivo.ejecutar_menu()

        assert capturado["modo"] is ModoIntegridad.RAPIDA
        assert ConfiguracionUsuario().cargar().modo_integridad() is ModoIntegridad.RAPIDA

    def test_enter_mantiene_el_ultimo_modo(self, monkeypatch, carpeta_con_ficheros: Path):
        """Con ENTER se usa el último modo guardado."""
        cfg = ConfiguracionUsuario().cargar()
        cfg.establecer("modo_integridad", "completa")
        cfg.guardar()
        capturado: dict = {}

        def vigilar_falso(
            carpeta,
            config=None,
            destino=None,
            recursivo=False,
            modo_integridad=None,
            agrupar=None,
            duplicados=None,
        ):
            capturado["modo"] = modo_integridad
            return 0

        monkeypatch.setattr(interactivo, "ejecutar_vigilar", vigilar_falso)
        _simular_entrada(
            monkeypatch, ["07", str(carpeta_con_ficheros), "", "", "", "n", "", "", "11"]
        )

        interactivo.ejecutar_menu()

        assert capturado["modo"] is ModoIntegridad.COMPLETA

    def test_muestra_los_tres_modos_con_su_descripcion(
        self, monkeypatch, capsys, carpeta_con_ficheros: Path
    ):
        """La pregunta presenta los tres modos con su título y su explicación."""
        monkeypatch.setattr(interactivo, "ejecutar_vigilar", lambda *a, **k: 0)
        _simular_entrada(
            monkeypatch, ["07", str(carpeta_con_ficheros), "", "", "", "n", "", "", "11"]
        )

        interactivo.ejecutar_menu()

        salida = capsys.readouterr().out
        for modo in ModoIntegridad:
            assert DESCRIPCIONES_INTEGRIDAD[modo].titulo in salida
            assert DESCRIPCIONES_INTEGRIDAD[modo].detalle.split()[0] in salida

    def test_organizar_usa_el_modo_elegido(self, monkeypatch, carpeta_con_ficheros: Path):
        """Al organizar con la completa, cada movimiento guarda su SHA-256."""
        # 01, carpeta, ENTER en agrupar/duplicados/config, backup=n, integridad=1, pausa, salir
        _simular_entrada(
            monkeypatch, ["01", str(carpeta_con_ficheros), "", "", "", "n", "1", "", "11"]
        )

        interactivo.ejecutar_menu()

        org = OrganizadorArchivos(carpeta_origen=carpeta_con_ficheros)
        org.mover.cargar_historial()
        assert all(r.checksum is not None for r in org.mover.obtener_historial())


def _menu_con_preguntas(monkeypatch, respuestas: list[str]) -> list[str]:
    """Ejecuta el menú con esas respuestas y devuelve los textos que mostró al preguntar."""
    pendientes = iter(respuestas)
    preguntas: list[str] = []

    def input_falso(prompt: str = "") -> str:
        preguntas.append(prompt)
        return next(pendientes)

    monkeypatch.setattr(builtins, "input", input_falso)
    assert interactivo.ejecutar_menu() == 0
    return preguntas


def _crear_carpeta(base: Path, nombre: str) -> Path:
    """Crea una carpeta con una foto dentro."""
    carpeta = base / nombre
    carpeta.mkdir()
    (carpeta / "a.jpg").write_text("x")
    return carpeta


@pytest.fixture
def config_fotos(tmp_path: Path) -> Path:
    """Fichero de categorías propio con una sola: «Fotos» para los .jpg."""
    ruta = tmp_path / "fotos.json"
    ruta.write_text('{"Fotos": [".jpg"]}', encoding="utf-8")
    return ruta


class TestRecordarUltimaEleccion:
    """El menú propone con ENTER lo que se respondió la última vez, también desde la GUI."""

    def test_enter_reutiliza_la_ultima_carpeta(self, monkeypatch, carpeta_con_ficheros: Path):
        """Tras organizar una carpeta, ENTER al pedir la carpeta para deshacer la reutiliza."""
        # Organizar la carpeta y después deshacer pulsando ENTER al pedir la carpeta.
        preguntas = _menu_con_preguntas(
            monkeypatch,
            ["01", str(carpeta_con_ficheros), "", "", "", "n", "", "", "03", "", "", "11"],
        )

        assert (carpeta_con_ficheros / "a.jpg").exists()  # se deshizo en la misma carpeta
        assert not (carpeta_con_ficheros / "Imagenes").exists()
        pregunta_carpeta = [p for p in preguntas if "carpeta" in p][-1]
        assert str(carpeta_con_ficheros) in pregunta_carpeta

    def test_no_ofrece_una_carpeta_que_ya_no_existe(self, monkeypatch, tmp_path: Path):
        """Si la última carpeta se borró, no se propone."""
        cfg = ConfiguracionUsuario().cargar()
        cfg.establecer("ultima_carpeta", str(tmp_path / "borrada"))
        cfg.guardar()

        preguntas = _menu_con_preguntas(monkeypatch, ["03", "", "", "11"])

        assert "borrada" not in next(p for p in preguntas if "carpeta" in p)

    def test_enter_reutiliza_el_ultimo_fichero_de_categorias(
        self, monkeypatch, tmp_path: Path, config_fotos: Path
    ):
        """ENTER al pedir las categorías reutiliza el último fichero indicado."""
        primera = _crear_carpeta(tmp_path, "primera")
        segunda = _crear_carpeta(tmp_path, "segunda")

        _menu_con_preguntas(
            monkeypatch,
            [
                "01",
                str(primera),
                "",
                "",
                str(config_fotos),
                "n",
                "",
                "",
                "01",
                str(segunda),
                "",
                "",
                "",
                "n",
                "",
                "",
                "11",
            ],
        )

        assert (segunda / "Fotos" / "a.jpg").exists()

    def test_guion_usa_las_categorias_por_defecto(
        self, monkeypatch, tmp_path: Path, config_fotos: Path
    ):
        """«-» vuelve a las categorías por defecto y olvida el fichero guardado."""
        cfg = ConfiguracionUsuario().cargar()
        cfg.establecer("ultima_config", str(config_fotos))
        cfg.guardar()
        carpeta = _crear_carpeta(tmp_path, "carpeta")

        _menu_con_preguntas(monkeypatch, ["01", str(carpeta), "", "", "-", "n", "", "", "11"])

        assert (carpeta / "Imagenes" / "a.jpg").exists()
        assert ConfiguracionUsuario().cargar().obtener("ultima_config") == ""

    def test_un_fichero_de_categorias_invalido_cancela_la_operacion(
        self, monkeypatch, carpeta_con_ficheros: Path
    ):
        """Antes avisaba del error pero organizaba igualmente con las categorías por defecto."""
        _menu_con_preguntas(
            monkeypatch, ["01", str(carpeta_con_ficheros), "", "", "no_existe.json", "", "11"]
        )

        assert (carpeta_con_ficheros / "a.jpg").exists()
        assert not (carpeta_con_ficheros / "Imagenes").exists()

    def test_el_backup_por_defecto_es_la_ultima_respuesta(self, monkeypatch, tmp_path: Path):
        """Tras responder «s», la siguiente vez la pregunta propone «S» con ENTER."""
        primera = _crear_carpeta(tmp_path, "primera")
        segunda = _crear_carpeta(tmp_path, "segunda")

        preguntas = _menu_con_preguntas(
            monkeypatch,
            [
                "01",
                str(primera),
                "",
                "",
                "",
                "s",
                "",
                "",
                "01",
                str(segunda),
                "",
                "",
                "",
                "",
                "",
                "",
                "11",
            ],
        )

        assert list((segunda / NOMBRE_CARPETA_BACKUPS).glob("*.zip"))  # ENTER = sí
        preguntas_backup = [p for p in preguntas if "¿Crear un backup" in p]
        assert "(s/N)" in preguntas_backup[0] and "(S/n)" in preguntas_backup[1]

    def test_comparte_las_preferencias_con_la_gui(self, monkeypatch, carpeta_con_ficheros: Path):
        """Lo que se marcó en la GUI (el backup) es lo que el menú propone."""
        cfg = ConfiguracionUsuario().cargar()
        cfg.establecer("backup", True)  # marcado en la GUI
        cfg.guardar()

        _menu_con_preguntas(
            monkeypatch, ["01", str(carpeta_con_ficheros), "", "", "", "", "", "", "11"]
        )

        assert list((carpeta_con_ficheros / NOMBRE_CARPETA_BACKUPS).glob("*.zip"))

    def test_vigilar_subcarpetas_por_defecto_es_la_ultima_respuesta(
        self, monkeypatch, carpeta_con_ficheros: Path
    ):
        """Tras vigilar con subcarpetas, ENTER vuelve a vigilarlas."""
        recursivos: list[bool] = []

        def vigilar_falso(
            carpeta,
            config=None,
            destino=None,
            recursivo=False,
            modo_integridad=None,
            agrupar=None,
            duplicados=None,
        ):
            recursivos.append(recursivo)
            return 0

        monkeypatch.setattr(interactivo, "ejecutar_vigilar", vigilar_falso)
        carpeta = str(carpeta_con_ficheros)
        _menu_con_preguntas(
            monkeypatch,
            ["07", carpeta, "", "", "", "s", "", "", "07", carpeta, "", "", "", "", "", "", "11"],
        )

        assert recursivos == [True, True]

    def test_los_comandos_sin_config_no_borran_el_ultimo_fichero_de_categorias(
        self, monkeypatch, carpeta_con_ficheros: Path, config_fotos: Path
    ):
        """Usar los comandos sin ``--config`` no hace olvidar al menú el fichero de categorías, pero
        sí recuerda la carpeta.
        """
        import sys

        from file_organizer.cli import aplicacion

        cfg = ConfiguracionUsuario().cargar()
        cfg.establecer("ultima_config", str(config_fotos))
        cfg.guardar()
        monkeypatch.setattr(sys, "argv", ["file-organizer", "--carpeta", str(carpeta_con_ficheros)])

        assert aplicacion.main() == 0

        guardada = ConfiguracionUsuario().cargar()
        assert guardada.obtener("ultima_config") == str(config_fotos)
        assert guardada.obtener("ultima_carpeta") == str(carpeta_con_ficheros)

    def test_recuerda_rutas_relativas_como_completas(
        self, monkeypatch, tmp_path: Path, config_fotos: Path
    ):
        """Una ruta relativa se guarda completa, así que sirve aunque el menú se abra luego desde
        otra carpeta.
        """
        primera = _crear_carpeta(tmp_path, "primera")
        segunda = _crear_carpeta(tmp_path, "segunda")
        monkeypatch.chdir(tmp_path)
        _menu_con_preguntas(monkeypatch, ["01", "primera", "", "", "fotos.json", "n", "", "", "11"])

        # Más tarde, abriendo el menú desde otra carpeta, ENTER sigue encontrando el fichero.
        otra = tmp_path / "otra"
        otra.mkdir()
        monkeypatch.chdir(otra)
        _menu_con_preguntas(monkeypatch, ["01", str(segunda), "", "", "", "n", "", "", "11"])

        assert (primera / "Fotos" / "a.jpg").exists()
        assert (segunda / "Fotos" / "a.jpg").exists()
        guardada = ConfiguracionUsuario().cargar()
        assert guardada.obtener("ultima_config") == str(config_fotos.resolve())
        assert guardada.obtener("ultima_carpeta") == str(segunda.resolve())
