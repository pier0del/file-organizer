"""Tests del clasificador por tipo: a qué categoría va cada extensión, con las categorías de siempre
o con las de un fichero de configuración, y qué errores da una configuración mal escrita.
"""

import json
from pathlib import Path

import pytest

from file_organizer.clasificador import CATEGORIAS_DEFECTO, SIN_CATEGORIA, Clasificador
from file_organizer.excepciones import ErrorConfiguracionNoEncontrada, ErrorFormatoConfiguracion


@pytest.fixture
def clf() -> Clasificador:
    """Clasificador con las categorías por defecto."""
    return Clasificador()


@pytest.fixture
def config_valido(tmp_path: Path) -> Path:
    """Fichero de categorías propio con solo «Imagenes» y «Codigo»."""
    archivo_config = tmp_path / "categories.json"
    archivo_config.write_text(
        json.dumps(
            {
                "Imagenes": [".jpg", ".png"],
                "Codigo": [".py", ".js"],
            }
        ),
        encoding="utf-8",
    )
    return archivo_config


class TestClasificarPorDefecto:
    """Las categorías por defecto."""

    def test_clasifica_imagen_correctamente(self, clf: Clasificador):
        """``.jpg`` va a «Imagenes»."""
        assert clf.clasificar(Path("foto.jpg")) == "Imagenes"

    def test_clasifica_codigo_python(self, clf: Clasificador):
        """``.py`` va a «Codigo»."""
        assert clf.clasificar(Path("script.py")) == "Codigo"

    def test_clasifica_documento(self, clf: Clasificador):
        """``.pdf`` va a «Documentos»."""
        assert clf.clasificar(Path("informe.pdf")) == "Documentos"

    def test_clasifica_video(self, clf: Clasificador):
        """``.mp4`` va a «Videos»."""
        assert clf.clasificar(Path("pelicula.mp4")) == "Videos"

    def test_extension_desconocida_va_a_otros(self, clf: Clasificador):
        """Una extensión que no conoce va a «Otros»."""
        assert clf.clasificar(Path("archivo.xyz")) == SIN_CATEGORIA

    def test_fichero_sin_extension_va_a_otros(self, clf: Clasificador):
        """Un fichero sin extensión va a «Otros»."""
        assert clf.clasificar(Path("README")) == SIN_CATEGORIA

    def test_extension_en_mayusculas_se_clasifica_igual(self, clf: Clasificador):
        """La extensión no distingue mayúsculas: ``.JPG`` y ``.Jpg`` son imágenes."""
        assert clf.clasificar(Path("FOTO.JPG")) == "Imagenes"
        assert clf.clasificar(Path("Foto.Jpg")) == "Imagenes"

    def test_extension_mixta_dentro_del_nombre_no_afecta(self, clf: Clasificador):
        """Con varios puntos en el nombre, solo cuenta la última extensión."""
        assert clf.clasificar(Path("mi.script.final.py")) == "Codigo"


class TestMetodosDeConsulta:
    """Consultar las categorías que usa el clasificador."""

    def test_obtener_categorias_devuelve_las_categorias_por_defecto(self, clf: Clasificador):
        """Sin configuración, son las de CATEGORIAS_DEFECTO."""
        assert clf.obtener_categorias() == CATEGORIAS_DEFECTO


class TestConfigValida:
    """Un fichero de categorías propio sustituye a las de por defecto."""

    def test_carga_categorias_personalizadas(self, config_valido: Path):
        """Las categorías son exactamente las del fichero."""
        clf = Clasificador(ruta_config=config_valido)
        assert clf.obtener_categorias() == {
            "Imagenes": [".jpg", ".png"],
            "Codigo": [".py", ".js"],
        }

    def test_clasifica_con_categorias_personalizadas(self, config_valido: Path):
        """Cada extensión va a la categoría que le da el fichero."""
        clf = Clasificador(ruta_config=config_valido)
        assert clf.clasificar(Path("foto.jpg")) == "Imagenes"
        assert clf.clasificar(Path("app.js")) == "Codigo"

    def test_extension_no_incluida_en_config_personalizada_va_a_otros(self, config_valido: Path):
        """Lo que el fichero no menciona va a «Otros», aunque esté en las de por defecto."""
        clf = Clasificador(ruta_config=config_valido)
        assert clf.clasificar(Path("informe.pdf")) == SIN_CATEGORIA

    def test_acepta_ruta_config_como_string(self, config_valido: Path):
        """La ruta de la configuración puede ser un texto además de un Path."""
        clf = Clasificador(ruta_config=str(config_valido))
        assert clf.clasificar(Path("foto.jpg")) == "Imagenes"


class TestConfigInvalida:
    """Una configuración que falta o está mal escrita da un error claro al crearlo."""

    def test_config_inexistente_lanza_error_configuracion_no_encontrada(self, tmp_path: Path):
        """Si el fichero no existe: ErrorConfiguracionNoEncontrada."""
        no_existe = tmp_path / "no_existe.json"
        with pytest.raises(ErrorConfiguracionNoEncontrada):
            Clasificador(ruta_config=no_existe)

    def test_json_mal_formado_lanza_error_formato_configuracion(self, tmp_path: Path):
        """Si no es un JSON válido: ErrorFormatoConfiguracion."""
        malo = tmp_path / "malo.json"
        malo.write_text("{ esto no es un json válido", encoding="utf-8")
        with pytest.raises(ErrorFormatoConfiguracion):
            Clasificador(ruta_config=malo)

    def test_json_que_no_es_diccionario_lanza_error_formato_configuracion(self, tmp_path: Path):
        """Si es una lista en lugar de categorías con sus extensiones: ErrorFormatoConfiguracion."""
        lista = tmp_path / "lista.json"
        lista.write_text('[".jpg", ".png"]', encoding="utf-8")
        with pytest.raises(ErrorFormatoConfiguracion):
            Clasificador(ruta_config=lista)

    def test_categoria_que_no_es_lista_lanza_error_formato_configuracion(self, tmp_path: Path):
        """Si una categoría no tiene una lista de extensiones: ErrorFormatoConfiguracion."""
        formato_incorrecto = tmp_path / "formato_incorrecto.json"
        formato_incorrecto.write_text(json.dumps({"Imagenes": "no es una lista"}), encoding="utf-8")
        with pytest.raises(ErrorFormatoConfiguracion):
            Clasificador(ruta_config=formato_incorrecto)

    def test_categoria_con_elementos_no_string_lanza_error_formato_configuracion(
        self, tmp_path: Path
    ):
        """Si la lista de una categoría tiene algo que no es texto: ErrorFormatoConfiguracion."""
        formato_incorrecto = tmp_path / "numeros.json"
        formato_incorrecto.write_text(
            json.dumps({"Imagenes": [".jpg", 123]}),
            encoding="utf-8",
        )
        with pytest.raises(ErrorFormatoConfiguracion):
            Clasificador(ruta_config=formato_incorrecto)

    def test_una_categoria_valida_no_oculta_el_error_de_otra(self, tmp_path: Path):
        """Se revisan todas las categorías, no solo la primera."""
        dos_categorias = tmp_path / "dos_categorias.json"
        dos_categorias.write_text(
            json.dumps(
                {
                    "Imagenes": [".jpg", ".png"],
                    "Codigo": "no es una lista",
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(ErrorFormatoConfiguracion):
            Clasificador(ruta_config=dos_categorias)


class TestSinConfig:
    """Sin fichero de configuración se usan las categorías por defecto."""

    def test_sin_ruta_config_usa_categorias_por_defecto(self):
        """Pasando ``None`` explícitamente."""
        clf = Clasificador(ruta_config=None)
        assert clf.obtener_categorias() == CATEGORIAS_DEFECTO

    def test_sin_argumentos_usa_categorias_por_defecto(self):
        """Sin pasar ningún argumento."""
        clf = Clasificador()
        assert clf.obtener_categorias() == CATEGORIAS_DEFECTO
