"""Tests del organizador por tipo: organizar una carpeta, deshacerlo y el resumen que muestra la
consola.
"""

from pathlib import Path

import pytest

from file_organizer.cli.salida import imprimir_resumen
from file_organizer.excepciones import (
    ErrorConfiguracionNoEncontrada,
    ErrorHistorialCorrupto,
    ErrorMoverArchivo,
    ErrorOrigenNoEncontrado,
)
from file_organizer.mover_archivos import ResultadoDeshacer
from file_organizer.organizador import OrganizadorArchivos


@pytest.fixture
def carpeta_con_ficheros(tmp_path: Path) -> Path:
    """Una foto, un PDF y un script, más un fichero oculto y
    una subcarpeta que no se deben tocar.
    """
    for nombre in ["foto.jpg", "doc.pdf", "script.py", ".oculto.txt"]:
        (tmp_path / nombre).write_text("contenido")
    (tmp_path / "una_subcarpeta").mkdir()
    return tmp_path


class TestConstructor:
    """Cómo queda preparado el organizador al crearlo."""

    def test_carpeta_destino_por_defecto_es_igual_a_carpeta_origen(self, tmp_path: Path):
        """Sin destino, organiza dentro de la propia carpeta."""
        org = OrganizadorArchivos(carpeta_origen=tmp_path)
        assert org.carpeta_destino == org.carpeta_origen

    def test_carpeta_destino_explicita_se_respeta(self, tmp_path: Path):
        """Con destino, organiza en esa carpeta."""
        destino = tmp_path / "otra_carpeta"
        org = OrganizadorArchivos(carpeta_origen=tmp_path, carpeta_destino=destino)
        assert org.carpeta_destino == destino

    def test_mover_recibe_las_mismas_carpetas_origen_y_destino(self, tmp_path: Path):
        """El movedor trabaja con las mismas carpetas, ya resueltas."""
        destino = tmp_path / "salida"
        org = OrganizadorArchivos(carpeta_origen=tmp_path, carpeta_destino=destino)
        assert org.mover.carpeta_origen == org.carpeta_origen.resolve()
        assert org.mover.carpeta_destino == destino.resolve()

    def test_config_invalida_se_propaga_desde_el_constructor(self, tmp_path: Path):
        """Una configuración que no existe falla al crearlo, antes de tocar nada."""
        with pytest.raises(ErrorConfiguracionNoEncontrada):
            OrganizadorArchivos(carpeta_origen=tmp_path, ruta_config="no_existe.json")

    def test_ultimos_errores_vacio_al_crear(self, tmp_path: Path):
        """Recién creado no tiene errores anotados."""
        org = OrganizadorArchivos(carpeta_origen=tmp_path)
        assert org.ultimos_errores == []


class TestEjecutar:
    """Organizar la carpeta."""

    def test_organiza_los_ficheros_en_subcarpetas_por_categoria(self, carpeta_con_ficheros: Path):
        """Cada fichero acaba en la carpeta de su categoría."""
        org = OrganizadorArchivos(carpeta_origen=carpeta_con_ficheros)
        org.ejecutar()

        assert (carpeta_con_ficheros / "Imagenes" / "foto.jpg").exists()
        assert (carpeta_con_ficheros / "Documentos" / "doc.pdf").exists()
        assert (carpeta_con_ficheros / "Codigo" / "script.py").exists()

    def test_devuelve_el_numero_de_ficheros_procesados(self, carpeta_con_ficheros: Path):
        """Devuelve cuántos ficheros movió (no cuenta el oculto)."""
        org = OrganizadorArchivos(carpeta_origen=carpeta_con_ficheros)
        procesados = org.ejecutar()
        assert procesados == 3

    def test_ignora_ficheros_ocultos(self, carpeta_con_ficheros: Path):
        """Los ficheros que empiezan por punto se quedan donde están."""
        org = OrganizadorArchivos(carpeta_origen=carpeta_con_ficheros)
        org.ejecutar()
        assert (carpeta_con_ficheros / ".oculto.txt").exists()

    def test_ignora_subcarpetas(self, carpeta_con_ficheros: Path):
        """Las subcarpetas no se mueven."""
        org = OrganizadorArchivos(carpeta_origen=carpeta_con_ficheros)
        org.ejecutar()
        assert (carpeta_con_ficheros / "una_subcarpeta").is_dir()

    def test_carpeta_origen_inexistente_lanza_error_origen_no_encontrado(self, tmp_path: Path):
        """Si la carpeta no existe: ErrorOrigenNoEncontrado."""
        org = OrganizadorArchivos(carpeta_origen=tmp_path / "no_existe")
        with pytest.raises(ErrorOrigenNoEncontrado):
            org.ejecutar()

    def test_carpeta_origen_que_es_un_fichero_lanza_error_origen_no_encontrado(
        self, tmp_path: Path
    ):
        """Si es un fichero y no una carpeta: ErrorOrigenNoEncontrado."""
        fichero = tmp_path / "no_es_carpeta.txt"
        fichero.write_text("x")
        org = OrganizadorArchivos(carpeta_origen=fichero)
        with pytest.raises(ErrorOrigenNoEncontrado):
            org.ejecutar()

    def test_simulacion_no_mueve_ningun_fichero(self, carpeta_con_ficheros: Path):
        """Simulando, todo sigue donde estaba y no se crean carpetas."""
        org = OrganizadorArchivos(carpeta_origen=carpeta_con_ficheros, modo_simulacion=True)
        org.ejecutar()

        assert (carpeta_con_ficheros / "foto.jpg").exists()
        assert not (carpeta_con_ficheros / "Imagenes").exists()

    def test_simulacion_no_guarda_historial(self, carpeta_con_ficheros: Path):
        """Simulando no se escribe el historial."""
        org = OrganizadorArchivos(carpeta_origen=carpeta_con_ficheros, modo_simulacion=True)
        org.ejecutar()
        assert org.mover.ruta_historial is not None
        assert not org.mover.ruta_historial.exists()

    def test_ejecucion_real_guarda_historial_recuperable(self, carpeta_con_ficheros: Path):
        """Organizar guarda el historial y otra instancia lo lee con sus movimientos."""
        org = OrganizadorArchivos(carpeta_origen=carpeta_con_ficheros)
        org.ejecutar()

        org2 = OrganizadorArchivos(carpeta_origen=carpeta_con_ficheros)
        org2.mover.cargar_historial()
        assert len(org2.mover.obtener_historial()) == 3

    def test_ejecuciones_sucesivas_solo_procesan_ficheros_restantes(
        self, carpeta_con_ficheros: Path
    ):
        """Organizar otra vez no mueve lo que ya está en su categoría."""
        org = OrganizadorArchivos(carpeta_origen=carpeta_con_ficheros)
        primera = org.ejecutar()
        segunda = org.ejecutar()

        assert primera == 3
        assert segunda == 0

    def test_fichero_que_desaparece_no_aborta_el_resto(
        self, carpeta_con_ficheros: Path, monkeypatch
    ):
        """Si un fichero desaparece a mitad, se anota su error y se organizan los demás."""
        org = OrganizadorArchivos(carpeta_origen=carpeta_con_ficheros)
        mover_original = org.mover.mover

        def mover_falla_para_doc(elemento, carpeta_base, categoria):
            if elemento.name == "doc.pdf":
                raise FileNotFoundError("simulado: el fichero desapareció")
            return mover_original(elemento, carpeta_base, categoria)

        monkeypatch.setattr(org.mover, "mover", mover_falla_para_doc)

        procesados = org.ejecutar()

        assert procesados == 2
        assert len(org.ultimos_errores) == 1
        assert org.ultimos_errores[0][0].name == "doc.pdf"

    def test_error_mover_archivo_no_interrumpe_el_resto(
        self, carpeta_con_ficheros: Path, monkeypatch
    ):
        """Si un fichero no se puede mover (p. ej. por permisos), se anota y se sigue."""
        org = OrganizadorArchivos(carpeta_origen=carpeta_con_ficheros)
        mover_original = org.mover.mover

        def mover_falla_para_doc(elemento, carpeta_base, categoria):
            if elemento.name == "doc.pdf":
                raise ErrorMoverArchivo("simulado: fallo real de movimientos (ej. permisos)")
            return mover_original(elemento, carpeta_base, categoria)

        monkeypatch.setattr(org.mover, "mover", mover_falla_para_doc)

        procesados = org.ejecutar()

        assert procesados == 2
        assert len(org.ultimos_errores) == 1
        assert org.ultimos_errores[0][0].name == "doc.pdf"
        assert isinstance(org.ultimos_errores[0][1], ErrorMoverArchivo)


class TestDeshacer:
    """Deshacer la organización desde el organizador."""

    def test_sin_organizacion_previa_devuelve_none(self, tmp_path: Path):
        """Sin historial no hay nada que deshacer: devuelve None."""
        org = OrganizadorArchivos(carpeta_origen=tmp_path)
        assert org.deshacer() is None

    def test_tras_organizar_devuelve_resultado_deshacer(self, carpeta_con_ficheros: Path):
        """Tras organizar, devuelve cuántos ficheros se restauraron y cuántos fallaron."""
        org = OrganizadorArchivos(carpeta_origen=carpeta_con_ficheros)
        org.ejecutar()

        resultado = org.deshacer()

        assert isinstance(resultado, ResultadoDeshacer)
        assert resultado.exitosos == 3
        assert resultado.fallidos == 0

    def test_deshacer_restaura_los_ficheros_a_la_raiz(self, carpeta_con_ficheros: Path):
        """Los ficheros vuelven a la raíz de la carpeta."""
        org = OrganizadorArchivos(carpeta_origen=carpeta_con_ficheros)
        org.ejecutar()
        org.deshacer()

        assert (carpeta_con_ficheros / "foto.jpg").exists()
        assert (carpeta_con_ficheros / "doc.pdf").exists()
        assert (carpeta_con_ficheros / "script.py").exists()

    def test_historial_corrupto_se_propaga_como_error_historial_corrupto(
        self, carpeta_con_ficheros: Path
    ):
        """Si el historial está dañado, deshacer lanza ErrorHistorialCorrupto."""
        org = OrganizadorArchivos(carpeta_origen=carpeta_con_ficheros)
        org.ejecutar()

        org.mover.ruta_historial.write_text("{ esto no es json válido", encoding="utf-8")

        with pytest.raises(ErrorHistorialCorrupto):
            org.deshacer()


class TestImprimirResumen:
    """El resumen que la consola muestra al acabar."""

    def test_sin_movimientos_imprime_mensaje_generico(self, tmp_path: Path, capsys):
        """Sin nada que organizar, lo dice en lugar de mostrar un resumen vacío."""
        org = OrganizadorArchivos(carpeta_origen=tmp_path)
        imprimir_resumen(org)
        salida = capsys.readouterr().out
        assert "No había ficheros que organizar." in salida

    def test_simulacion_resume_lo_que_se_moveria(self, carpeta_con_ficheros: Path, capsys):
        """Antes, una simulación decía «los movimientos simulados no se registran» en vez de
        resumir adónde iría cada fichero."""
        org = OrganizadorArchivos(carpeta_origen=carpeta_con_ficheros, modo_simulacion=True)
        org.ejecutar()
        capsys.readouterr()

        imprimir_resumen(org)
        salida = capsys.readouterr().out
        assert "RESUMEN DE LA SIMULACIÓN (no se ha movido nada)" in salida
        assert "Imagenes: 1 fichero(s)" in salida

    def test_con_movimientos_imprime_las_categorias(self, carpeta_con_ficheros: Path, capsys):
        """Tras organizar, cuenta los ficheros de cada categoría."""
        org = OrganizadorArchivos(carpeta_origen=carpeta_con_ficheros)
        org.ejecutar()
        capsys.readouterr()

        imprimir_resumen(org)
        salida = capsys.readouterr().out

        assert "RESUMEN DE ORGANIZACIÓN" in salida
        assert "Imagenes" in salida
        assert "Documentos" in salida

    def test_con_errores_los_incluye_en_el_resumen(
        self, carpeta_con_ficheros: Path, monkeypatch, capsys
    ):
        """Los ficheros que no se pudieron organizar salen en el resumen con su nombre."""
        org = OrganizadorArchivos(carpeta_origen=carpeta_con_ficheros)
        mover_original = org.mover.mover

        def mover_falla_para_doc(elemento, carpeta_base, categoria):
            if elemento.name == "doc.pdf":
                raise FileNotFoundError("simulado")
            return mover_original(elemento, carpeta_base, categoria)

        monkeypatch.setattr(org.mover, "mover", mover_falla_para_doc)
        org.ejecutar()
        capsys.readouterr()

        imprimir_resumen(org)
        salida = capsys.readouterr().out

        assert "No se pudieron organizar 1 fichero(s):" in salida
        assert "doc.pdf" in salida
