"""Tests de MoverArchivos: mover un fichero sin pisar otro, anotarlo en el historial, guardar y leer
ese historial (y rechazarlo si está dañado o manipulado) y deshacer.
"""

import json
import stat
import sys
from pathlib import Path

import pytest

from file_organizer import mover_archivos
from file_organizer.excepciones import (
    ErrorHistorialCorrupto,
    ErrorHistorialNoEncontrado,
)
from file_organizer.mover_archivos import (
    NOMBRE_ARCHIVO_HISTORIAL,
    MoverArchivos,
)
from file_organizer.utilidades import resolver_conflicto


@pytest.fixture
def carpetas(tmp_path: Path) -> tuple[Path, Path]:
    """Carpetas de origen y de destino vacías."""
    origen = tmp_path / "origen"
    destino = tmp_path / "destino"
    origen.mkdir()
    destino.mkdir()
    return origen, destino


@pytest.fixture
def mover(carpetas: tuple[Path, Path]) -> MoverArchivos:
    """Movedor de verdad (sin simular) entre esas dos carpetas."""
    origen, destino = carpetas
    return MoverArchivos(modo_simulacion=False, carpeta_origen=origen, carpeta_destino=destino)


class TestConstructor:
    """Lo que queda preparado al crear el movedor."""

    def test_historial_vacio_al_crear(self, mover: MoverArchivos):
        """Recién creado no tiene ningún movimiento anotado."""
        assert mover.obtener_historial() == []

    def test_ruta_historial_es_none_sin_carpeta_destino(self):
        """Sin carpeta de destino no hay dónde guardar el historial."""
        m = MoverArchivos()
        assert m.ruta_historial is None

    def test_ruta_historial_deriva_de_carpeta_destino(self, carpetas: tuple[Path, Path]):
        """El historial se guarda en un fichero oculto dentro del destino."""
        origen, destino = carpetas
        m = MoverArchivos(carpeta_origen=origen, carpeta_destino=destino)
        assert m.ruta_historial == destino / NOMBRE_ARCHIVO_HISTORIAL

    def test_carpetas_origen_y_destino_se_resuelven_a_rutas_absolutas(
        self, carpetas: tuple[Path, Path]
    ):
        """Las carpetas se guardan como rutas absolutas, para comparar rutas sin ambigüedad."""
        origen, destino = carpetas
        m = MoverArchivos(carpeta_origen=origen, carpeta_destino=destino)
        assert m.carpeta_origen.is_absolute()
        assert m.carpeta_destino.is_absolute()


class TestMover:
    """Mover un fichero a su carpeta de categoría."""

    def test_mueve_el_fichero_correctamente(
        self, mover: MoverArchivos, carpetas: tuple[Path, Path]
    ):
        """El fichero desaparece del origen y aparece, con su contenido, en la categoría."""
        origen, destino = carpetas
        f = origen / "foto.jpg"
        f.write_text("contenido")

        mover.mover(f, destino, "Imagenes")

        assert not f.exists()
        assert (destino / "Imagenes" / "foto.jpg").exists()
        assert (destino / "Imagenes" / "foto.jpg").read_text() == "contenido"

    def test_registra_el_movimiento_en_el_historial(
        self, mover: MoverArchivos, carpetas: tuple[Path, Path]
    ):
        """Cada movimiento queda anotado con su categoría."""
        origen, destino = carpetas
        f = origen / "foto.jpg"
        f.write_text("x")

        mover.mover(f, destino, "Imagenes")

        assert len(mover.obtener_historial()) == 1
        registro = mover.obtener_historial()[0]
        assert registro.categoria == "Imagenes"

    def test_crea_la_carpeta_destino_si_no_existe(
        self, mover: MoverArchivos, carpetas: tuple[Path, Path]
    ):
        """La carpeta de la categoría se crea si falta."""
        origen, destino = carpetas
        f = origen / "doc.pdf"
        f.write_text("x")

        mover.mover(f, destino, "Documentos")

        assert (destino / "Documentos").is_dir()

    def test_simulacion_no_mueve_el_fichero(self, carpetas: tuple[Path, Path]):
        """Simulando, el fichero se queda donde estaba y el mensaje lo dice."""
        origen, destino = carpetas
        m = MoverArchivos(modo_simulacion=True, carpeta_origen=origen, carpeta_destino=destino)
        f = origen / "foto.jpg"
        f.write_text("x")

        resultado = m.mover(f, destino, "Imagenes")

        assert f.exists()
        assert not (destino / "Imagenes" / "foto.jpg").exists()
        assert "[SIMULACIÓN]" in resultado.mensaje

    def test_simulacion_no_registra_en_historial(self, carpetas: tuple[Path, Path]):
        """Simulando no se anota nada, así que no hay nada que deshacer."""
        origen, destino = carpetas
        m = MoverArchivos(modo_simulacion=True, carpeta_origen=origen, carpeta_destino=destino)
        f = origen / "foto.jpg"
        f.write_text("x")

        m.mover(f, destino, "Imagenes")

        assert m.obtener_historial() == []

    def test_fichero_inexistente_lanza_file_not_found_error(
        self, mover: MoverArchivos, carpetas: tuple[Path, Path]
    ):
        """Mover algo que no existe lanza FileNotFoundError."""
        origen, destino = carpetas
        with pytest.raises(FileNotFoundError):
            mover.mover(origen / "no_existe.txt", destino, "Otros")

    def test_ruta_que_es_carpeta_lanza_file_not_found_error(
        self, mover: MoverArchivos, carpetas: tuple[Path, Path]
    ):
        """Una carpeta no se mueve como si fuera un fichero: lanza FileNotFoundError."""
        origen, destino = carpetas
        subcarpeta = origen / "una_carpeta"
        subcarpeta.mkdir()
        with pytest.raises(FileNotFoundError):
            mover.mover(subcarpeta, destino, "Otros")

    def test_no_sobreescribe_fichero_con_mismo_nombre_en_destino(
        self, mover: MoverArchivos, carpetas: tuple[Path, Path]
    ):
        """Si ya hay uno con ese nombre, el nuevo se guarda como ``foto_1.jpg`` y el otro no se
        toca.
        """
        origen, destino = carpetas
        (destino / "Imagenes").mkdir()
        (destino / "Imagenes" / "foto.jpg").write_text("ya estaba aquí")

        f = origen / "foto.jpg"
        f.write_text("nuevo contenido")
        mover.mover(f, destino, "Imagenes")

        assert (destino / "Imagenes" / "foto.jpg").read_text() == "ya estaba aquí"
        assert (destino / "Imagenes" / "foto_1.jpg").read_text() == "nuevo contenido"


class TestGuardarCargarHistorial:
    """Guardar el historial en su fichero y volver a leerlo."""

    def test_ida_y_vuelta_conserva_los_registros(
        self, mover: MoverArchivos, carpetas: tuple[Path, Path]
    ):
        """Lo que se guarda lo lee igual otra instancia."""
        origen, destino = carpetas
        f = origen / "foto.jpg"
        f.write_text("x")
        mover.mover(f, destino, "Imagenes")
        mover.guardar_historial()

        m2 = MoverArchivos(carpeta_origen=origen, carpeta_destino=destino)
        m2.cargar_historial()

        assert len(m2.obtener_historial()) == 1
        assert m2.obtener_historial()[0].categoria == "Imagenes"

    def test_crea_fichero_oculto_en_carpeta_destino(
        self, mover: MoverArchivos, carpetas: tuple[Path, Path]
    ):
        """Guardar crea ``.file-organizer-history.json`` en el destino."""
        _, destino = carpetas
        mover.guardar_historial()
        assert (destino / NOMBRE_ARCHIVO_HISTORIAL).exists()

    @pytest.mark.skipif(sys.platform == "win32", reason="permisos POSIX no aplican en Windows")
    def test_permisos_del_historial_son_0600(self, mover: MoverArchivos):
        """Solo su dueño puede leerlo: lleva las rutas de sus ficheros."""
        mover.guardar_historial()
        modo = stat.S_IMODE(mover.ruta_historial.stat().st_mode)
        assert modo == 0o600

    def test_sin_carpetas_lanza_error_historial_corrupto_al_guardar(self):
        """Sin carpetas configuradas no se escribe nada."""
        m = MoverArchivos()
        with pytest.raises(ErrorHistorialCorrupto):
            m.guardar_historial()

    def test_con_destino_pero_sin_origen_lanza_error_historial_corrupto(
        self, carpetas: tuple[Path, Path]
    ):
        """Hace falta también el origen: sin él no se podría validar al leerlo."""
        _, destino = carpetas
        m = MoverArchivos(carpeta_destino=destino)
        with pytest.raises(ErrorHistorialCorrupto):
            m.guardar_historial()


class TestCargarHistorialInvalido:
    """Un historial que falta, está dañado o fue manipulado se rechaza al leerlo."""

    def test_historial_inexistente_lanza_error_historial_no_encontrado(self, mover: MoverArchivos):
        """Sin fichero: ErrorHistorialNoEncontrado."""
        with pytest.raises(ErrorHistorialNoEncontrado):
            mover.cargar_historial()

    def test_json_mal_formado_lanza_error_historial_corrupto(self, mover: MoverArchivos):
        """Si no es un JSON válido: ErrorHistorialCorrupto."""
        mover.ruta_historial.write_text("{ esto no es json válido", encoding="utf-8")
        with pytest.raises(ErrorHistorialCorrupto):
            mover.cargar_historial()

    def test_falta_la_clave_version_lanza_error_historial_corrupto(self, mover: MoverArchivos):
        """Sin la versión del formato no se sabe leerlo: ErrorHistorialCorrupto."""
        mover.ruta_historial.write_text(json.dumps({"registros": []}), encoding="utf-8")
        with pytest.raises(ErrorHistorialCorrupto):
            mover.cargar_historial()

    def test_registro_con_ruta_fuera_de_carpeta_origen_es_rechazado(
        self, mover: MoverArchivos, carpetas: tuple[Path, Path], tmp_path: Path
    ):
        """Un registro que llevaría un fichero fuera de las carpetas (historial manipulado) hace que
        se rechace entero.
        """
        origen, destino = carpetas
        fuera_de_lugar = tmp_path / "fuera" / "otro_fichero.txt"

        datos = {
            "version": 2,
            "carpeta_origen": str(origen.resolve()),
            "carpeta_destino": str(destino.resolve()),
            "registros": [
                {
                    "origen": str(fuera_de_lugar),
                    "destino": str(destino / "Otros" / "otro_fichero.txt"),
                    "categoria": "Otros",
                    "fecha": "2026-01-01T00:00:00",
                }
            ],
        }
        mover.ruta_historial.write_text(json.dumps(datos), encoding="utf-8")

        with pytest.raises(ErrorHistorialCorrupto, match="fuera de estas carpetas"):
            mover.cargar_historial()

    def test_version_desconocida_lanza_error_historial_corrupto(self, mover: MoverArchivos):
        """Una versión del formato que el programa no conoce: ErrorHistorialCorrupto."""
        mover.ruta_historial.write_text(
            json.dumps({"version": 99, "registros": []}), encoding="utf-8"
        )
        with pytest.raises(ErrorHistorialCorrupto, match="está dañado"):
            mover.cargar_historial()


class TestFormatoHistorial:
    """El formato del fichero del historial y los anteriores."""

    def test_guarda_en_version_3_por_pasos_con_claves_en_espanol(
        self, mover: MoverArchivos, carpetas: tuple[Path, Path]
    ):
        """Versión 3: una lista de pasos con su tipo e inicio, y cada movimiento con su huella de
        integridad.
        """
        origen, destino = carpetas
        f = origen / "foto.jpg"
        f.write_text("x")
        mover.mover(f, destino, "Imagenes")
        mover.guardar_historial()

        datos = json.loads(mover.ruta_historial.read_text(encoding="utf-8"))
        assert datos["version"] == 3
        assert len(datos["operaciones"]) == 1
        operacion = datos["operaciones"][0]
        assert set(operacion) == {"tipo", "inicio", "registros"}
        assert operacion["tipo"] == "organizar"
        assert set(operacion["registros"][0]) == {
            "origen",
            "destino",
            "categoria",
            "fecha",
            "checksum",
            "tamano",
            "mtime_ns",
        }

    def test_carga_historial_antiguo_version_1(
        self, mover: MoverArchivos, carpetas: tuple[Path, Path]
    ):
        """Un historial de la versión 1, con las claves en inglés, se sigue leyendo."""
        origen, destino = carpetas
        datos = {
            "version": 1,
            "carpeta_origen": str(origen.resolve()),
            "carpeta_destino": str(destino.resolve()),
            "records": [
                {
                    "source": str(origen.resolve() / "foto.jpg"),
                    "destination": str(destino.resolve() / "Imagenes" / "foto.jpg"),
                    "category": "Imagenes",
                    "timestamp": "2026-01-01T00:00:00",
                    "checksum": None,
                }
            ],
        }
        mover.ruta_historial.write_text(json.dumps(datos), encoding="utf-8")

        mover.cargar_historial()

        registro = mover.obtener_historial()[0]
        assert registro.categoria == "Imagenes"
        assert registro.origen.name == "foto.jpg"
        assert registro.fecha.year == 2026


class TestDeshacerTodo:
    """Deshacer el último paso: devolver cada fichero a su sitio."""

    def test_restaura_los_ficheros_a_su_ubicacion_original(
        self, mover: MoverArchivos, carpetas: tuple[Path, Path]
    ):
        """El fichero vuelve a su sitio con su contenido y desaparece de la categoría."""
        origen, destino = carpetas
        f = origen / "foto.jpg"
        f.write_text("contenido original")
        mover.mover(f, destino, "Imagenes")

        resultado_deshacer = mover.deshacer_todo(carpeta_base=destino)

        assert resultado_deshacer.exitosos == 1
        assert resultado_deshacer.fallidos == 0
        assert f.exists()
        assert f.read_text() == "contenido original"
        assert not (destino / "Imagenes" / "foto.jpg").exists()

    def test_si_el_origen_esta_ocupado_restaura_con_otro_nombre_sin_sobrescribir(
        self, mover: MoverArchivos, carpetas: tuple[Path, Path]
    ):
        """Si el usuario creó otro fichero en el sitio original, se restaura como ``foto_1.jpg`` y
        se dice.
        """
        origen, destino = carpetas
        f = origen / "foto.jpg"
        f.write_text("original")
        mover.mover(f, destino, "Imagenes")

        f.write_text("fichero nuevo del usuario, no tocar")

        resultado_deshacer = mover.deshacer_todo(carpeta_base=destino)

        assert resultado_deshacer.exitosos == 1
        assert resultado_deshacer.fallidos == 0
        assert f.read_text() == "fichero nuevo del usuario, no tocar"
        restaurado = origen / "foto_1.jpg"
        assert restaurado.read_text() == "original"
        assert [(r.origen.name, ruta) for r, ruta in resultado_deshacer.renombrados] == [
            ("foto.jpg", restaurado)
        ]

    def test_fichero_movido_a_mano_no_interrumpe_el_resto(
        self, mover: MoverArchivos, carpetas: tuple[Path, Path]
    ):
        """Si uno ya no está en la categoría, se cuenta como fallido y los demás se restauran."""
        origen, destino = carpetas
        f1 = origen / "a.txt"
        f2 = origen / "b.txt"
        f1.write_text("a")
        f2.write_text("b")
        mover.mover(f1, destino, "Otros")
        mover.mover(f2, destino, "Otros")

        (destino / "Otros" / "a.txt").unlink()

        resultado_deshacer = mover.deshacer_todo(carpeta_base=destino)

        assert resultado_deshacer.exitosos == 1
        assert resultado_deshacer.fallidos == 1
        assert f2.exists()
        assert not f1.exists()

    def test_un_fichero_que_ya_no_esta_sale_del_historial(
        self, mover: MoverArchivos, carpetas: tuple[Path, Path]
    ):
        """No se podrá restaurar nunca: si se quedara, bloquearía los pasos anteriores."""
        origen, destino = carpetas
        f1 = origen / "a.txt"
        f2 = origen / "b.txt"
        f1.write_text("a")
        f2.write_text("b")
        mover.mover(f1, destino, "Otros")
        mover.mover(f2, destino, "Otros")

        (destino / "Otros" / "a.txt").unlink()

        resultado = mover.deshacer_todo(carpeta_base=destino)

        assert resultado.fallidos == 1
        assert isinstance(resultado.errores[0][1], FileNotFoundError)
        assert mover.obtener_historial() == []
        assert resultado.pasos_restantes == 0

    def test_un_fallo_que_se_puede_reintentar_se_queda_en_el_historial(
        self, mover: MoverArchivos, carpetas: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ):
        """Si falla por permisos, ese fichero sigue en el historial como paso pendiente para volver
        a intentarlo.
        """
        origen, destino = carpetas
        for nombre in ("a.txt", "b.txt"):
            (origen / nombre).write_text(nombre)
            mover.mover(origen / nombre, destino, "Otros")
        mover_original = mover_archivos.shutil.move

        def mover_sin_permiso(fuente, destino_final):
            if Path(fuente).name == "a.txt":
                raise PermissionError("sin permiso")
            return mover_original(fuente, destino_final)

        monkeypatch.setattr(mover_archivos.shutil, "move", mover_sin_permiso)
        resultado = mover.deshacer_todo(carpeta_base=destino)

        assert resultado.exitosos == 1 and resultado.fallidos == 1
        assert [r.origen.name for r in mover.obtener_historial()] == ["a.txt"]
        assert resultado.pasos_restantes == 1
        assert resultado.siguiente is not None
        assert [r.origen.name for r in resultado.siguiente.registros] == ["a.txt"]

    def test_deshacer_todo_limpia_carpetas_vacias(
        self, mover: MoverArchivos, carpetas: tuple[Path, Path]
    ):
        """La carpeta de categoría que queda vacía se borra."""
        origen, destino = carpetas
        f = origen / "foto.jpg"
        f.write_text("x")
        mover.mover(f, destino, "Imagenes")

        mover.deshacer_todo(carpeta_base=destino)

        assert not (destino / "Imagenes").exists()

    def test_deshacer_todo_conserva_carpetas_no_vacias(
        self, mover: MoverArchivos, carpetas: tuple[Path, Path]
    ):
        """Una categoría donde el usuario guardó algo se queda, con lo suyo dentro."""
        origen, destino = carpetas
        f = origen / "foto.jpg"
        f.write_text("x")
        mover.mover(f, destino, "Imagenes")
        (destino / "Imagenes" / "ajeno.txt").write_text("no lo toques")

        mover.deshacer_todo(carpeta_base=destino)

        assert (destino / "Imagenes").exists()
        assert (destino / "Imagenes" / "ajeno.txt").exists()

    def test_deshacer_todo_sin_carpeta_destino_lanza_error_historial_corrupto(
        self, carpetas: tuple[Path, Path]
    ):
        """Sin carpeta de destino no se sabe qué carpetas se pueden limpiar: no se deshace."""
        origen, destino = carpetas
        m = MoverArchivos(carpeta_origen=origen, carpeta_destino=destino)
        f = origen / "foto.jpg"
        f.write_text("x")
        m.mover(f, destino, "Imagenes")
        m.carpeta_destino = None
        with pytest.raises(ErrorHistorialCorrupto):
            m.deshacer_todo()

    def test_deshacer_todo_sin_historial_previo_devuelve_resultado_vacio(
        self, mover: MoverArchivos, carpetas: tuple[Path, Path]
    ):
        """Sin nada anotado, deshacer no hace nada ni falla."""
        _, destino = carpetas
        resultado_deshacer = mover.deshacer_todo(carpeta_base=destino)
        assert resultado_deshacer.exitosos == 0
        assert resultado_deshacer.fallidos == 0


class TestConsultas:
    """Consultar el historial y resolver conflictos de nombres."""

    def test_obtener_estadisticas_agrupa_por_categoria(
        self, mover: MoverArchivos, carpetas: tuple[Path, Path]
    ):
        """Las estadísticas cuentan los ficheros de cada categoría."""
        origen, destino = carpetas
        for nombre, cat in [("a.jpg", "Imagenes"), ("b.jpg", "Imagenes"), ("c.pdf", "Documentos")]:
            f = origen / nombre
            f.write_text("x")
            mover.mover(f, destino, cat)

        estadisticas = mover.obtener_estadisticas()
        assert estadisticas == {"Imagenes": 2, "Documentos": 1}

    def test_obtener_historial_devuelve_una_copia_no_la_referencia_interna(
        self, mover: MoverArchivos, carpetas: tuple[Path, Path]
    ):
        """Cambiar la lista devuelta no cambia el historial."""
        origen, destino = carpetas
        f = origen / "a.jpg"
        f.write_text("x")
        mover.mover(f, destino, "Imagenes")

        copia = mover.obtener_historial()
        copia.clear()

        assert len(mover.obtener_historial()) == 1

    def test_resolver_conflicto_sin_conflicto_devuelve_la_misma_ruta(
        self, mover: MoverArchivos, tmp_path: Path
    ):
        """Si el nombre está libre, se usa tal cual."""
        destino = tmp_path / "libre.txt"
        assert resolver_conflicto(destino) == destino

    def test_resolver_conflicto_con_conflicto_anade_sufijo_numerico(
        self, mover: MoverArchivos, tmp_path: Path
    ):
        """Si está ocupado, se añade ``_1`` antes de la extensión."""
        ocupado = tmp_path / "ocupado.txt"
        ocupado.write_text("x")
        resultado = resolver_conflicto(ocupado)
        assert resultado == tmp_path / "ocupado_1.txt"

    def test_resolver_conflicto_incrementa_hasta_encontrar_hueco_libre(
        self, mover: MoverArchivos, tmp_path: Path
    ):
        """Si ``_1`` y ``_2`` también lo están, se usa ``_3``."""
        (tmp_path / "doc.txt").write_text("x")
        (tmp_path / "doc_1.txt").write_text("x")
        (tmp_path / "doc_2.txt").write_text("x")
        resultado = resolver_conflicto(tmp_path / "doc.txt")
        assert resultado == tmp_path / "doc_3.txt"


class TestDeshacerRespetaCarpetasDelUsuario:
    """Deshacer solo borra las carpetas vacías de las que sacó algo."""

    def test_no_borra_carpetas_vacias_del_usuario(
        self, mover: MoverArchivos, carpetas: tuple[Path, Path]
    ):
        """Una carpeta vacía que creó el usuario se queda."""
        origen, destino = carpetas
        (destino / "Proyectos 2027").mkdir()  # carpeta vacía creada por el usuario
        f = origen / "foto.jpg"
        f.write_text("x")
        mover.mover(f, destino, "Imagenes")

        mover.deshacer_todo(carpeta_base=destino)

        assert (destino / "Proyectos 2027").is_dir()
        assert not (destino / "Imagenes").exists()

    def test_no_borra_una_categoria_vacia_que_no_toco(
        self, mover: MoverArchivos, carpetas: tuple[Path, Path]
    ):
        """Una categoría vacía de la que este deshacer no sacó nada se queda."""
        origen, destino = carpetas
        (destino / "Musica").mkdir()  # vacía, pero este deshacer no sacó nada de ella
        f = origen / "foto.jpg"
        f.write_text("x")
        mover.mover(f, destino, "Imagenes")

        mover.deshacer_todo(carpeta_base=destino)

        assert (destino / "Musica").is_dir()
