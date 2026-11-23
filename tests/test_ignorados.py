"""Tests del sistema de ignorados basado en '.file-organizer-ignore'."""

from pathlib import Path

from file_organizer.ignorados import NOMBRE_ARCHIVO_IGNORE, ListaIgnorados


class TestParseo:
    """Cómo se lee el fichero ``.file-organizer-ignore``."""

    def test_carpeta_sin_fichero_no_ignora_nada(self, tmp_path: Path):
        """Sin fichero, la lista está vacía y no ignora nada."""
        lista = ListaIgnorados.desde_carpeta(tmp_path)
        assert len(lista) == 0
        assert not lista.debe_ignorar(tmp_path / "cualquier.txt")

    def test_ignora_comentarios_y_lineas_vacias(self, tmp_path: Path):
        """Las líneas con ``#`` y las vacías no son patrones."""
        (tmp_path / NOMBRE_ARCHIVO_IGNORE).write_text(
            "# comentario\n\n*.log\n   \n# otro\n*.tmp\n", encoding="utf-8"
        )
        lista = ListaIgnorados.desde_carpeta(tmp_path)
        assert len(lista) == 2

    def test_recorta_espacios(self, tmp_path: Path):
        """Los espacios de alrededor no forman parte del patrón."""
        (tmp_path / NOMBRE_ARCHIVO_IGNORE).write_text("  *.log  \n", encoding="utf-8")
        lista = ListaIgnorados.desde_carpeta(tmp_path)
        assert lista.patrones == ["*.log"]


class TestDebeIgnorar:
    """Qué ficheros coinciden con los patrones."""

    def _lista(self, tmp_path: Path, contenido: str) -> ListaIgnorados:
        """Escribe ese contenido en el fichero de ignorados y lo carga."""
        (tmp_path / NOMBRE_ARCHIVO_IGNORE).write_text(contenido, encoding="utf-8")
        return ListaIgnorados.desde_carpeta(tmp_path)

    def test_glob_por_extension(self, tmp_path: Path):
        """``*.log`` ignora los .log y nada más."""
        lista = self._lista(tmp_path, "*.log\n")
        assert lista.debe_ignorar(tmp_path / "salida.log")
        assert not lista.debe_ignorar(tmp_path / "salida.txt")

    def test_nombre_exacto(self, tmp_path: Path):
        """Un nombre sin comodines solo ignora ese fichero."""
        lista = self._lista(tmp_path, "secreto.txt\n")
        assert lista.debe_ignorar(tmp_path / "secreto.txt")
        assert not lista.debe_ignorar(tmp_path / "otro.txt")

    def test_patron_con_interrogacion(self, tmp_path: Path):
        """``?`` vale exactamente por un carácter."""
        lista = self._lista(tmp_path, "doc?.pdf\n")
        assert lista.debe_ignorar(tmp_path / "doc1.pdf")
        assert not lista.debe_ignorar(tmp_path / "doc12.pdf")

    def test_ruta_relativa_en_subcarpeta(self, tmp_path: Path):
        """``temp/*`` ignora lo que hay dentro de la subcarpeta ``temp``."""
        lista = self._lista(tmp_path, "temp/*\n")
        sub = tmp_path / "temp"
        sub.mkdir()
        assert lista.debe_ignorar(sub / "algo.txt")

    def test_sin_patrones_no_ignora(self, tmp_path: Path):
        """Una lista creada sin patrones no ignora nada."""
        lista = ListaIgnorados(base=tmp_path)
        assert not lista.debe_ignorar(tmp_path / "x.log")
