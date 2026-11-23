"""Fecha de un fichero para agruparlo por año y mes: la de sus metadatos si los tiene y, si no, la
del sistema de ficheros.

El formato se reconoce por el contenido, no por la extensión, y se lee sin dependencias externas:

- Fotos (JPEG, TIFF y RAW basados en TIFF, PNG, WebP, HEIC/HEIF/AVIF): fecha EXIF de la toma.
- Vídeos (MP4, MOV, M4V, 3GP...): fecha de grabación que guarda el contenedor.
- PDF: fecha de creación (diccionario Info o XMP).
- Office (docx, xlsx, pptx) y OpenDocument (odt, ods, odp): fecha de creación.

Unos metadatos dañados, vacíos o con una fecha imposible no dan error: se usa la del sistema.
"""

from __future__ import annotations

import logging
import os
import re
import struct
import sys
import zipfile
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import BinaryIO

logger = logging.getLogger(__name__)

# Origen de la fecha cuando el fichero no tiene metadatos con fecha.
ORIGEN_FICHERO = "fichero"

# Antes de esto, una fecha es casi siempre un campo sin rellenar (los vídeos sin fecha dicen
# 1904) o un reloj sin poner en hora.
_FECHA_MINIMA = datetime(1970, 1, 2)

# Límites de lectura: los metadatos están al principio (o al final, en un PDF), y un fichero
# dañado no debe hacer leer ni reservar más de la cuenta.
_MAX_BYTES_EXIF = 1024 * 1024
_MAX_BYTES_EXTREMO_PDF = 256 * 1024
_MAX_BYTES_XML = 64 * 1024
_MAX_BLOQUES = 10_000

# Errores que puede dar leer metadatos de un fichero dañado o con un formato inesperado.
_ERRORES_DE_LECTURA = (
    OSError,
    EOFError,
    ValueError,
    IndexError,
    KeyError,
    OverflowError,
    struct.error,
    zipfile.BadZipFile,
    RuntimeError,  # zipfile: entrada cifrada
    NotImplementedError,  # zipfile: compresión no soportada
)

# Lectura de datos binarios por posición: (desplazamiento, cantidad) -> exactamente esos bytes.
Lectura = Callable[[int, int], bytes]


@dataclass(frozen=True)
class FechaFichero:
    """Fecha de un fichero y de dónde sale: unos metadatos (``EXIF``, ``PDF``...) o ``fichero``."""

    fecha: datetime
    origen: str

    @property
    def de_metadatos(self) -> bool:
        """Si la fecha sale de los metadatos y no del sistema de ficheros."""
        return self.origen != ORIGEN_FICHERO


def fecha_del_fichero(ruta: Path) -> FechaFichero:
    """Fecha de los metadatos del fichero o, si no la tiene, la del sistema de ficheros.

    Lanza OSError (p. ej. FileNotFoundError) solo si no se puede consultar el fichero.
    """
    metadatos = fecha_de_metadatos(ruta)
    if metadatos is not None:
        return metadatos
    return FechaFichero(fecha_del_sistema(ruta), ORIGEN_FICHERO)


class ContadorFechas:
    """Da la fecha de cada fichero y cuenta de dónde salió, para explicárselo al usuario.

    Lo comparten la agrupación por fecha y las reglas con ``{año}`` o ``{mes}``, así que el
    resumen cuenta todas las fechas usadas al organizar.
    """

    def __init__(self) -> None:
        # Fechas usadas por origen: «EXIF», «PDF»... o ORIGEN_FICHERO.
        self.origenes: Counter[str] = Counter()

    def fecha(self, ruta: Path) -> FechaFichero:
        """Fecha del fichero (ver ``fecha_del_fichero``), anotando de dónde salió."""
        resultado = fecha_del_fichero(ruta)
        self.origenes[resultado.origen] += 1
        logger.debug(f"{ruta.name}: {resultado.fecha:%Y-%m-%d} (fecha de {resultado.origen})")
        return resultado

    def reiniciar(self) -> None:
        """Olvida lo contado (al empezar a organizar otra vez)."""
        self.origenes.clear()

    def resumen(self) -> str | None:
        """De dónde salieron las fechas, para el usuario; None si no se usó ninguna.

        Por ejemplo: «Fechas: 2 de los metadatos (EXIF 2) y 2 de la fecha del propio fichero,
        porque no tenían metadatos con fecha.»
        """
        del_fichero = self.origenes[ORIGEN_FICHERO]
        metadatos = [(o, n) for o, n in self.origenes.most_common() if o != ORIGEN_FICHERO and n]
        total_metadatos = sum(n for _, n in metadatos)
        partes = []
        if total_metadatos:
            detalle = ", ".join(f"{origen} {cantidad}" for origen, cantidad in metadatos)
            partes.append(f"{total_metadatos} de los metadatos ({detalle})")
        if del_fichero:
            tenian = "tenía" if del_fichero == 1 else "tenían"
            partes.append(
                f"{del_fichero} de la fecha del propio fichero, porque no {tenian} metadatos "
                "con fecha"
            )
        return f"Fechas: {' y '.join(partes)}." if partes else None


def fecha_del_sistema(ruta: Path) -> datetime:
    """La más antigua entre la fecha de creación (si el sistema la da) y la de modificación.

    Al copiar o descargar un fichero, una de las dos suele conservar la original. Lanza OSError
    si no se puede consultar el fichero.
    """
    info = ruta.stat()
    marcas = [info.st_mtime]
    creacion = getattr(info, "st_birthtime", None)
    if creacion is None and sys.platform == "win32":
        creacion = info.st_ctime  # en Windows, antes de Python 3.12, es la de creación
    if creacion:
        marcas.append(creacion)
    try:
        return datetime.fromtimestamp(min(marcas))
    except (OverflowError, ValueError) as e:
        raise OSError(f"Fecha del fichero no válida: {ruta.name}") from e


def fecha_de_metadatos(ruta: Path) -> FechaFichero | None:
    """Fecha guardada dentro del fichero, o None si no tiene, no se reconoce o no se puede leer."""
    try:
        with open(ruta, "rb") as f:
            lector = _lector_para(f.read(16))
            if lector is None:
                return None
            origen, leer_fecha = lector
            f.seek(0)
            fecha = leer_fecha(f)
    except _ERRORES_DE_LECTURA as e:
        logger.debug(f"No se pudieron leer los metadatos de {ruta.name}: {e!r}")
        return None
    if fecha is None or not _FECHA_MINIMA <= fecha <= datetime.now() + timedelta(days=1):
        return None
    return FechaFichero(fecha, origen)


def _lector_para(cabecera: bytes) -> tuple[str, Callable[[BinaryIO], datetime | None]] | None:
    """Origen y función que lee la fecha, según los primeros bytes del fichero."""
    if cabecera.startswith(b"\xff\xd8"):
        return "EXIF", _fecha_jpeg
    if cabecera[:4] in (b"II*\x00", b"MM\x00*"):
        return "EXIF", lambda f: _fecha_exif(_lectura_de_fichero(f))
    if cabecera.startswith(b"\x89PNG\r\n\x1a\n"):
        return "EXIF", _fecha_png
    if cabecera[:4] == b"RIFF" and cabecera[8:12] == b"WEBP":
        return "EXIF", _fecha_webp
    if cabecera[4:8] == b"ftyp" and cabecera[8:12] in _MARCAS_HEIF:
        return "EXIF", _fecha_heif
    if cabecera[4:8] in (b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip"):
        return "MP4/MOV", _fecha_video
    if cabecera.startswith(b"%PDF"):
        return "PDF", _fecha_pdf
    if cabecera.startswith(b"PK\x03\x04"):
        return "Office", _fecha_documento
    return None


# ------------------------------------------------------------------ utilidades


def _leer(f: BinaryIO, cantidad: int) -> bytes:
    """Exactamente ``cantidad`` bytes desde la posición actual (EOFError si no los hay)."""
    datos = f.read(cantidad)
    if len(datos) != cantidad:
        raise EOFError("El fichero se acaba antes de lo que indican sus metadatos")
    return datos


def _entero(f: BinaryIO, cantidad: int) -> int:
    """Entero sin signo big-endian de ``cantidad`` bytes (0 si ``cantidad`` es 0)."""
    return int.from_bytes(_leer(f, cantidad), "big")


def _tamano(f: BinaryIO) -> int:
    """Tamaño del fichero abierto, en bytes."""
    return f.seek(0, os.SEEK_END)


def _lectura_de_bytes(datos: bytes) -> Lectura:
    """Lectura por posición dentro de ``datos``."""

    def leer(desplazamiento: int, cantidad: int) -> bytes:
        trozo = datos[desplazamiento : desplazamiento + cantidad]
        if desplazamiento < 0 or len(trozo) != cantidad:
            raise EOFError("Posición fuera de los metadatos")
        return trozo

    return leer


def _lectura_de_fichero(f: BinaryIO, base: int = 0) -> Lectura:
    """Lectura por posición dentro del fichero, contando desde ``base``."""

    def leer(desplazamiento: int, cantidad: int) -> bytes:
        f.seek(base + desplazamiento)
        return _leer(f, cantidad)

    return leer


def _fecha_de_partes(partes: list[int]) -> datetime:
    """Fecha de [año, mes, día, hora, minuto, segundo] (ValueError si no existe)."""
    anio, mes, dia, hora, minuto, segundo = partes
    return datetime(anio, mes, dia, hora, minuto, segundo)


def _fecha_iso(texto: str) -> datetime | None:
    """Fecha ISO 8601 en hora local (``2024-03-15T10:00:00Z``, con o sin zona horaria)."""
    try:
        fecha = datetime.fromisoformat(texto)
    except ValueError:
        fecha = datetime.fromisoformat(texto[:19])  # sin fracciones de segundo ni zona raras
    if fecha.tzinfo is not None:
        fecha = fecha.astimezone().replace(tzinfo=None)
    return fecha


# ------------------------------------------------------------------------ EXIF

_ETIQUETA_FECHA_IMAGEN = 0x0132  # DateTime, en el IFD0: cuándo se guardó la imagen
_ETIQUETA_IFD_EXIF = 0x8769
_ETIQUETAS_FECHA_TOMA = (0x9003, 0x9004)  # DateTimeOriginal, DateTimeDigitized
_TIPO_ASCII = 2
_PATRON_FECHA_EXIF = re.compile(rb"(\d{4})[:-](\d{2})[:-](\d{2})[ T](\d{2}):(\d{2}):(\d{2})")

# Entrada de un IFD: (tipo, cantidad, 4 bytes con el valor o con su posición).
EntradaIfd = tuple[int, int, bytes]


def _fecha_exif(leer: Lectura) -> datetime | None:
    """Fecha de un bloque EXIF (estructura TIFF): la de la toma y, si falta, la de la imagen."""
    marca_orden = leer(0, 2)
    if marca_orden not in (b"II", b"MM"):
        return None
    orden = "<" if marca_orden == b"II" else ">"
    magia, posicion_ifd0 = struct.unpack(orden + "HI", leer(2, 6))
    if magia != 42:
        return None

    ifd0 = _entradas_ifd(leer, posicion_ifd0, orden)
    candidatas: list[EntradaIfd | None] = []
    if _ETIQUETA_IFD_EXIF in ifd0:
        (posicion_exif,) = struct.unpack(orden + "I", ifd0[_ETIQUETA_IFD_EXIF][2])
        ifd_exif = _entradas_ifd(leer, posicion_exif, orden)
        candidatas += [ifd_exif.get(etiqueta) for etiqueta in _ETIQUETAS_FECHA_TOMA]
    candidatas.append(ifd0.get(_ETIQUETA_FECHA_IMAGEN))

    for entrada in candidatas:
        if entrada is None:
            continue
        coincidencia = _PATRON_FECHA_EXIF.match(_texto_ifd(leer, entrada, orden))
        if coincidencia is not None:
            try:
                return _fecha_de_partes([int(parte) for parte in coincidencia.groups()])
            except ValueError:
                continue  # «0000:00:00 00:00:00»: la cámara no la rellenó
    return None


def _entradas_ifd(leer: Lectura, posicion: int, orden: str) -> dict[int, EntradaIfd]:
    """Entradas de un IFD, por etiqueta."""
    (cantidad,) = struct.unpack(orden + "H", leer(posicion, 2))
    datos = leer(posicion + 2, 12 * cantidad)
    entradas: dict[int, EntradaIfd] = {}
    for inicio in range(0, len(datos), 12):
        etiqueta, tipo, cuenta = struct.unpack_from(orden + "HHI", datos, inicio)
        entradas[etiqueta] = (tipo, cuenta, datos[inicio + 8 : inicio + 12])
    return entradas


def _texto_ifd(leer: Lectura, entrada: EntradaIfd, orden: str) -> bytes:
    """Valor de texto de una entrada (vacío si no es de texto o es demasiado largo)."""
    tipo, cuenta, valor = entrada
    if tipo != _TIPO_ASCII or cuenta > 64:
        return b""
    if cuenta <= 4:
        return valor[:cuenta]
    (posicion,) = struct.unpack(orden + "I", valor)
    return leer(posicion, cuenta)


def _fecha_jpeg(f: BinaryIO) -> datetime | None:
    """Fecha EXIF de un JPEG: va en un segmento APP1 «Exif», antes de los datos de la imagen."""
    f.seek(2)
    for _ in range(_MAX_BLOQUES):
        if _leer(f, 1) != b"\xff":
            return None
        marcador = _leer(f, 1)[0]
        while marcador == 0xFF:  # bytes de relleno entre segmentos
            marcador = _leer(f, 1)[0]
        if marcador in (0xD9, 0xDA):  # fin de la imagen o empiezan sus datos: ya no hay EXIF
            return None
        if 0xD0 <= marcador <= 0xD7 or marcador == 0x01:  # marcadores sin datos
            continue
        longitud = _entero(f, 2) - 2
        if marcador == 0xE1:
            datos = _leer(f, longitud)
            if datos.startswith(b"Exif\x00\x00"):
                return _fecha_exif(_lectura_de_bytes(datos[6:]))
        else:
            f.seek(longitud, os.SEEK_CUR)
    return None


def _fecha_png(f: BinaryIO) -> datetime | None:
    """Fecha EXIF de un PNG: va en el bloque «eXIf»."""
    f.seek(8)
    for _ in range(_MAX_BLOQUES):
        longitud, tipo = struct.unpack(">I4s", _leer(f, 8))
        if tipo == b"eXIf":
            if longitud > _MAX_BYTES_EXIF:
                return None
            return _fecha_exif(_lectura_de_bytes(_leer(f, longitud)))
        if tipo == b"IEND":
            return None
        f.seek(longitud + 4, os.SEEK_CUR)  # datos y CRC
    return None


def _fecha_webp(f: BinaryIO) -> datetime | None:
    """Fecha EXIF de un WebP: va en el bloque «EXIF», a veces precedida de «Exif\\0\\0»."""
    f.seek(12)
    for _ in range(_MAX_BLOQUES):
        tipo, longitud = struct.unpack("<4sI", _leer(f, 8))
        if tipo == b"EXIF":
            if longitud > _MAX_BYTES_EXIF:
                return None
            datos = _leer(f, longitud)
            return _fecha_exif(_lectura_de_bytes(datos.removeprefix(b"Exif\x00\x00")))
        f.seek(longitud + (longitud & 1), os.SEEK_CUR)  # los bloques ocupan un número par
    return None


# ------------------------------------------------- vídeos y HEIF (cajas ISO BMFF)

# Marcas del bloque «ftyp» de las fotos HEIF (HEIC del iPhone, AVIF...).
_MARCAS_HEIF = {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"mif1", b"msf1", b"avif"}
# Segundos entre 1904-01-01 (origen de las fechas de MP4 y MOV) y 1970-01-01.
_SEGUNDOS_1904_A_1970 = 2_082_844_800


def _cajas(f: BinaryIO, inicio: int, fin: int) -> Iterator[tuple[bytes, int, int]]:
    """Cajas entre ``inicio`` y ``fin``: (tipo, inicio de sus datos, fin de la caja)."""
    posicion = inicio
    for _ in range(_MAX_BLOQUES):
        if posicion + 8 > fin:
            return
        f.seek(posicion)
        tamano, tipo = struct.unpack(">I4s", _leer(f, 8))
        cabecera = 8
        if tamano == 1:  # tamaño de 64 bits
            tamano, cabecera = _entero(f, 8), 16
        elif tamano == 0:  # llega hasta el final
            tamano = fin - posicion
        if tamano < cabecera:
            return  # caja dañada
        yield tipo, posicion + cabecera, posicion + tamano
        posicion += tamano


def _caja(f: BinaryIO, buscada: bytes, inicio: int, fin: int) -> tuple[int, int] | None:
    """(inicio de los datos, fin) de la primera caja ``buscada`` entre ``inicio`` y ``fin``."""
    for tipo, inicio_datos, fin_caja in _cajas(f, inicio, fin):
        if tipo == buscada:
            return inicio_datos, fin_caja
    return None


def _fecha_video(f: BinaryIO) -> datetime | None:
    """Fecha de grabación de un MP4 o MOV: la de creación de la caja «moov/mvhd» (en UTC)."""
    moov = _caja(f, b"moov", 0, _tamano(f))
    mvhd = _caja(f, b"mvhd", *moov) if moov else None
    if mvhd is None:
        return None
    f.seek(mvhd[0])
    version = _leer(f, 4)[0]  # versión y flags
    segundos = _entero(f, 8 if version == 1 else 4)
    if segundos == 0:
        return None  # sin rellenar
    return datetime.fromtimestamp(segundos - _SEGUNDOS_1904_A_1970)


def _fecha_heif(f: BinaryIO) -> datetime | None:
    """Fecha EXIF de una foto HEIF: el EXIF es un «elemento» que se localiza en la caja «meta»."""
    meta = _caja(f, b"meta", 0, _tamano(f))
    if meta is None:
        return None
    inicio_meta = meta[0] + 4  # «meta» empieza con versión y flags
    iinf = _caja(f, b"iinf", inicio_meta, meta[1])
    iloc = _caja(f, b"iloc", inicio_meta, meta[1])
    id_exif = _id_elemento_exif(f, *iinf) if iinf else None
    if iloc is None or id_exif is None:
        return None
    posicion = _posicion_elemento(f, *iloc, id_exif)
    if posicion is None:
        return None
    f.seek(posicion)
    salto = _entero(f, 4)  # hasta la cabecera TIFF (normalmente salta «Exif\0\0»)
    return _fecha_exif(_lectura_de_fichero(f, posicion + 4 + salto))


def _id_elemento_exif(f: BinaryIO, inicio: int, fin: int) -> int | None:
    """Identificador del elemento EXIF según la caja «iinf», o None si no hay."""
    f.seek(inicio)
    version = _leer(f, 4)[0]
    inicio_entradas = inicio + 4 + (2 if version == 0 else 4)  # tras el número de entradas
    for tipo, inicio_infe, _fin in _cajas(f, inicio_entradas, fin):
        if tipo != b"infe":
            continue
        f.seek(inicio_infe)
        version_infe = _leer(f, 4)[0]
        if version_infe < 2:
            continue  # las versiones antiguas no indican el tipo de elemento
        id_elemento = _entero(f, 2 if version_infe == 2 else 4)
        _leer(f, 2)  # índice de protección
        if _leer(f, 4) == b"Exif":
            return id_elemento
    return None


def _posicion_elemento(f: BinaryIO, inicio: int, fin: int, buscado: int) -> int | None:
    """Posición en el fichero del elemento ``buscado`` según la caja «iloc», o None."""
    f.seek(inicio)
    version = _leer(f, 4)[0]
    tamanos = _leer(f, 2)
    tam_desplazamiento, tam_longitud = tamanos[0] >> 4, tamanos[0] & 0x0F
    tam_base = tamanos[1] >> 4
    tam_indice = tamanos[1] & 0x0F if version in (1, 2) else 0
    tam_id = 2 if version < 2 else 4
    for _ in range(_entero(f, tam_id)):
        if f.tell() >= fin:
            return None
        id_elemento = _entero(f, tam_id)
        metodo = _entero(f, 2) & 0x0F if version in (1, 2) else 0
        _entero(f, 2)  # índice de referencia de datos
        base = _entero(f, tam_base)
        posiciones = []
        for _ in range(_entero(f, 2)):
            _entero(f, tam_indice)
            posiciones.append(base + _entero(f, tam_desplazamiento))
            _entero(f, tam_longitud)
        if id_elemento == buscado:
            # Solo elementos guardados tal cual en el fichero (método 0) y en un solo trozo.
            return posiciones[0] if metodo == 0 and len(posiciones) == 1 else None
    return None


# ------------------------------------------------------------- PDF y documentos

_PATRON_FECHA_PDF = re.compile(
    rb"/CreationDate\s*\(\s*D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?"
)
_PATRON_FECHA_XMP = re.compile(rb"xmp:CreateDate(?:>\s*|=[\"'])([0-9][0-9T:+\-.Z]*)")
_PATRON_CREADO_OOXML = re.compile(rb"<(?:\w+:)?created\b[^>]*>\s*([^<\s]+)")
_PATRON_CREADO_ODF = re.compile(rb"<(?:\w+:)?creation-date\b[^>]*>\s*([^<\s]+)")


def _fecha_pdf(f: BinaryIO) -> datetime | None:
    """Fecha de creación de un PDF, del diccionario Info o de los metadatos XMP.

    Se busca al principio y al final del fichero, donde suelen estar; si están comprimidos o en
    medio de un PDF grande, no se encuentran.
    """
    tamano = _tamano(f)
    f.seek(0)
    if tamano <= 2 * _MAX_BYTES_EXTREMO_PDF:
        datos = f.read()
    else:
        datos = f.read(_MAX_BYTES_EXTREMO_PDF)
        f.seek(tamano - _MAX_BYTES_EXTREMO_PDF)
        datos += b"\n" + f.read()

    info = _PATRON_FECHA_PDF.search(datos)
    if info is not None:
        # D:AAAA[MM[DD[HH[mm[SS]]]]]: lo que falta, al principio del periodo.
        defectos = (0, 1, 1, 0, 0, 0)
        partes = [int(g) if g else d for g, d in zip(info.groups(), defectos, strict=True)]
        return _fecha_de_partes(partes)
    xmp = _PATRON_FECHA_XMP.search(datos)
    return _fecha_iso(xmp.group(1).decode("ascii")) if xmp else None


def _fecha_documento(f: BinaryIO) -> datetime | None:
    """Fecha de creación de un documento de Office (docx, xlsx...) o de OpenDocument (odt...)."""
    with zipfile.ZipFile(f) as archivo:
        nombres = set(archivo.namelist())
        for nombre, patron in (
            ("docProps/core.xml", _PATRON_CREADO_OOXML),
            ("meta.xml", _PATRON_CREADO_ODF),
        ):
            if nombre in nombres:
                with archivo.open(nombre) as entrada:
                    coincidencia = patron.search(entrada.read(_MAX_BYTES_XML))
                if coincidencia is not None:
                    return _fecha_iso(coincidencia.group(1).decode("ascii"))
    return None
