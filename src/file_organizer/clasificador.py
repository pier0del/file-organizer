"""Decide la carpeta de cada fichero: por su tipo, por su fecha o por las dos.

- Por tipo (extensión): ``Imagenes/``. Las categorías son las de CATEGORIAS_DEFECTO o las de un
  JSON del usuario (``--config``).
- Por fecha: ``2024/03/``, con la fecha de los metadatos si los hay (ver metadatos.py).
- Por tipo y fecha: ``Imagenes/2024/03/``.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePath
from typing import Protocol

from file_organizer.excepciones import ErrorConfiguracionNoEncontrada, ErrorFormatoConfiguracion
from file_organizer.metadatos import ContadorFechas

logger = logging.getLogger(__name__)

CATEGORIAS_DEFECTO: dict[str, list[str]] = {
    "Imagenes": [
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".svg",
        ".bmp",
        ".tif",
        ".tiff",
        ".heic",
        ".heif",
        ".avif",
        ".dng",
        ".cr2",
        ".nef",
        ".arw",
    ],
    "Videos": [".mp4", ".avi", ".mkv", ".mov", ".wmv", ".m4v", ".3gp", ".webm"],
    "Musica": [".mp3", ".wav", ".flac", ".aac", ".ogg"],
    "Documentos": [".pdf", ".docx", ".doc", ".txt", ".xlsx", ".pptx"],
    "Codigo": [".py", ".java", ".js", ".html", ".css", ".cpp"],
    "Datos": [".csv", ".json", ".xml", ".sql"],
    "Comprimidos": [".zip", ".rar", ".tar", ".gz", ".7z"],
    "Ejecutables": [".exe", ".msi", ".bat", ".sh"],
    "Fuentes": [".ttf", ".otf", ".woff", ".woff2"],
    "Ebooks": [".epub", ".mobi", ".azw", ".azw3", ".fb2"],
    "Diseño": [".psd", ".ai", ".xd", ".figma", ".sketch"],
    "Subtitulos": [".srt", ".sub", ".ass", ".vtt"],
    "Torrents": [".torrent", ".magnet"],
}

SIN_CATEGORIA = "Otros"

# Nombre del fichero al exportar las categorías si solo se indica una carpeta.
NOMBRE_EXPORTACION_DEFECTO = "categorias.json"


def ruta_exportacion(ruta_destino: str | Path) -> Path:
    """Ruta final del JSON al exportar las categorías.

    Si es una carpeta existente, el fichero va dentro con el nombre por defecto; si no tiene
    extensión, se le añade ``.json``.
    """
    ruta = Path(ruta_destino)
    if ruta.is_dir():
        return ruta / NOMBRE_EXPORTACION_DEFECTO
    return ruta if ruta.suffix else ruta.with_suffix(".json")


class CriterioAgrupacion(StrEnum):
    """Cómo se agrupan los ficheros en carpetas."""

    TIPO = "tipo"
    FECHA = "fecha"
    TIPO_FECHA = "tipo-fecha"


CRITERIO_DEFECTO = CriterioAgrupacion.TIPO


@dataclass(frozen=True)
class DescripcionCriterio:
    """Textos para presentar un criterio al usuario en la GUI, el menú y la consola."""

    titulo: str
    ejemplo: str


DESCRIPCIONES_AGRUPACION: dict[CriterioAgrupacion, DescripcionCriterio] = {
    CriterioAgrupacion.TIPO: DescripcionCriterio("Tipo", "Imagenes/foto.jpg"),
    CriterioAgrupacion.FECHA: DescripcionCriterio("Fecha", "2024/03/foto.jpg"),
    CriterioAgrupacion.TIPO_FECHA: DescripcionCriterio("Tipo y fecha", "Imagenes/2024/03/foto.jpg"),
}

# De dónde sale la fecha al agrupar por fecha, para explicarlo en la GUI, el menú y la ayuda.
EXPLICACION_FECHA = (
    "La fecha es la de los metadatos (EXIF de las fotos, grabación de los vídeos, creación de "
    "PDF y Office) y, si no los hay, la del fichero."
)


class ClasificadorFicheros(Protocol):
    """Lo que el organizador necesita de un clasificador.

    El organizador y el vigilante dependen de este protocolo, no de una clase: otra forma de
    clasificar (por contenido...) es una clase nueva con estos dos métodos.
    """

    def clasificar(self, ruta_archivo: Path) -> str:
        """Carpeta del fichero relativa al destino, con ``/`` entre niveles (``2024/03``)."""
        ...

    def es_carpeta_de_categoria(self, carpeta: PurePath) -> bool:
        """Si ``carpeta`` (relativa al destino) es una de las que devuelve ``clasificar``."""
        ...


class Clasificador:
    """Clasifica por extensión, con las categorías por defecto o con las de un JSON."""

    def __init__(self, ruta_config: str | Path | None = None):
        self.ruta_config = Path(ruta_config) if ruta_config else None
        self.categorias = (
            self._cargar_desde_json(ruta_config) if ruta_config else CATEGORIAS_DEFECTO
        )

        self._mapa_extensiones = {
            ext.lower(): categoria
            for categoria, extensiones in self.categorias.items()
            for ext in extensiones
        }

    def clasificar(self, ruta_archivo: Path) -> str:
        """Categoría de la extensión del fichero, o SIN_CATEGORIA si no está en ninguna."""
        return self._mapa_extensiones.get(ruta_archivo.suffix.lower(), SIN_CATEGORIA)

    def obtener_categorias(self) -> dict[str, list[str]]:
        """Categorías en uso, con sus extensiones."""
        return self.categorias

    def es_carpeta_de_categoria(self, carpeta: PurePath) -> bool:
        """Si ``carpeta`` (relativa al destino) es la de una categoría o la de SIN_CATEGORIA."""
        nombre = carpeta.as_posix()
        return nombre in self.categorias or nombre == SIN_CATEGORIA

    def exportar_categorias(self, ruta_destino: str | Path, sobrescribir: bool = False) -> Path:
        """Guarda las categorías en uso en un JSON que acepta ``--config`` y devuelve su ruta.

        Sirve de plantilla para crear categorías propias. Sin ``sobrescribir``, lanza
        FileExistsError si el fichero ya existe.
        """
        ruta = ruta_exportacion(ruta_destino)
        if ruta.exists() and not sobrescribir:
            raise FileExistsError(f"Ya existe el fichero: {ruta}")
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(self.categorias, f, indent=4, ensure_ascii=False)
            f.write("\n")
        logger.info(f"Categorías exportadas a: {ruta}")
        return ruta

    def _cargar_desde_json(self, ruta_config: str | Path) -> dict[str, list[str]]:
        """Categorías de un JSON del usuario, validadas.

        Lanza ErrorConfiguracionNoEncontrada o ErrorFormatoConfiguracion si no sirve.
        """
        ruta = Path(ruta_config)

        if not ruta.exists():
            raise ErrorConfiguracionNoEncontrada(f"No se encontró el fichero de categorías: {ruta}")

        try:
            with open(ruta, encoding="utf-8") as f:
                datos = json.load(f)
        except json.JSONDecodeError as e:
            raise ErrorFormatoConfiguracion(
                f"El fichero de categorías no es un JSON válido: {ruta} ({e})"
            ) from e

        if not isinstance(datos, dict):
            raise ErrorFormatoConfiguracion(
                'El fichero de categorías debe tener la forma {"Categoría": [".ext", ...]}: '
                f"{ruta}"
            )

        for categoria, extensiones in datos.items():
            if not isinstance(extensiones, list) or not all(
                isinstance(e, str) for e in extensiones
            ):
                raise ErrorFormatoConfiguracion(
                    f"La categoría «{categoria}» debe ser una lista de extensiones, como "
                    f'[".jpg", ".png"]: {ruta}'
                )
        logger.info(f"Categorías cargadas desde: {ruta}")
        return datos


_PATRON_ANIO = re.compile(r"\d{4}")
_PATRON_MES = re.compile(r"0[1-9]|1[0-2]")


def quitar_carpeta_de_fecha(carpeta: PurePath) -> PurePath | None:
    """Lo que hay delante de ``AAAA/MM`` en ``carpeta`` (``Imagenes/2024/03`` → ``Imagenes``),
    o None si no acaba en una fecha. Para ``2024/03`` sola devuelve una ruta vacía.
    """
    partes = carpeta.parts
    if len(partes) < 2 or not (
        _PATRON_ANIO.fullmatch(partes[-2]) and _PATRON_MES.fullmatch(partes[-1])
    ):
        return None
    return PurePath(*partes[:-2])


class ClasificadorPorFecha:
    """Clasifica por año y mes (``2024/03``); con ``por_tipo``, dentro de la carpeta de su tipo
    (``Imagenes/2024/03``).

    La fecha es la de los metadatos del fichero si los tiene y, si no, la del sistema de
    ficheros (ver metadatos.py). ``fechas`` cuenta de dónde salió cada una, para explicárselo al
    usuario.
    """

    def __init__(
        self, por_tipo: Clasificador | None = None, fechas: ContadorFechas | None = None
    ) -> None:
        self.por_tipo = por_tipo
        self.fechas = fechas if fechas is not None else ContadorFechas()

    def clasificar(self, ruta_archivo: Path) -> str:
        """``AAAA/MM`` de la fecha del fichero, tras su tipo si se agrupa también por tipo.

        Lanza OSError si no se puede consultar el fichero.
        """
        carpeta = self.carpeta_de_fecha(ruta_archivo)
        if self.por_tipo is None:
            return carpeta
        return f"{self.por_tipo.clasificar(ruta_archivo)}/{carpeta}"

    def carpeta_de_fecha(self, ruta_archivo: Path) -> str:
        """``AAAA/MM`` de la fecha del fichero, contando de dónde salió.

        La usan ``clasificar`` y el organizador, para poner la fecha debajo de la carpeta de una
        regla. Lanza OSError si no se puede consultar el fichero.
        """
        fecha = self.fechas.fecha(ruta_archivo).fecha
        return f"{fecha.year:04d}/{fecha.month:02d}"

    def es_carpeta_de_categoria(self, carpeta: PurePath) -> bool:
        """Si ``carpeta`` (relativa al destino) es ``AAAA/MM``, o ``<tipo>/AAAA/MM`` con tipo."""
        tipo = quitar_carpeta_de_fecha(carpeta)
        if tipo is None:
            return False
        if self.por_tipo is None:
            return not tipo.parts
        return bool(tipo.parts) and self.por_tipo.es_carpeta_de_categoria(tipo)


def crear_clasificador(
    criterio: CriterioAgrupacion = CRITERIO_DEFECTO,
    ruta_config: str | Path | None = None,
    fechas: ContadorFechas | None = None,
) -> ClasificadorFicheros:
    """Clasificador para ``criterio``; los tipos son los de ``ruta_config`` o los de por defecto.

    ``fechas``, si se pasa, cuenta de dónde salen las fechas al agrupar por fecha. Lanza
    ErrorConfiguracionNoEncontrada o ErrorFormatoConfiguracion si ``ruta_config`` no sirve
    (agrupando solo por fecha no se usa).
    """
    if criterio is CriterioAgrupacion.FECHA:
        return ClasificadorPorFecha(fechas=fechas)
    por_tipo = Clasificador(ruta_config=ruta_config)
    if criterio is CriterioAgrupacion.TIPO:
        return por_tipo
    return ClasificadorPorFecha(por_tipo, fechas=fechas)
