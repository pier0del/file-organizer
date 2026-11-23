"""Tests de la lectura de fechas de los metadatos (metadatos.py).

Cada formato se construye aquí byte a byte, con lo mínimo que tiene un fichero real: así se
prueba el lector sin depender de fotos, vídeos ni documentos de ejemplo.
"""

import os
import struct
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from file_organizer.metadatos import (
    ORIGEN_FICHERO,
    fecha_de_metadatos,
    fecha_del_fichero,
    fecha_del_sistema,
)

FECHA_DATETIME = 0x0132
FECHA_ORIGINAL = 0x9003
FECHA_DIGITALIZADA = 0x9004


# ----------------------------------------------------------------- construcción


def construir_tiff(
    ifd0: dict[int, bytes] | None = None, exif: dict[int, bytes] | None = None, orden: str = "<"
) -> bytes:
    """Bloque TIFF/EXIF con fechas de texto en el IFD0 y en el IFD EXIF."""
    ifd0, exif = ifd0 or {}, exif or {}
    n0 = len(ifd0) + (1 if exif else 0)
    pos_exif = 8 + 2 + 12 * n0 + 4
    pos_datos = pos_exif + (2 + 12 * len(exif) + 4 if exif else 0)
    datos = bytearray()

    def entrada_texto(etiqueta: int, texto: bytes) -> bytes:
        valor = texto + b"\x00"
        posicion = pos_datos + len(datos)
        datos.extend(valor)
        return struct.pack(orden + "HHII", etiqueta, 2, len(valor), posicion)

    entradas0 = [entrada_texto(e, t) for e, t in ifd0.items()]
    if exif:
        entradas0.append(struct.pack(orden + "HHII", 0x8769, 4, 1, pos_exif))
    entradas_exif = [entrada_texto(e, t) for e, t in exif.items()]

    marca = b"II" if orden == "<" else b"MM"
    resultado = marca + struct.pack(orden + "HI", 42, 8)
    resultado += struct.pack(orden + "H", n0) + b"".join(entradas0) + struct.pack(orden + "I", 0)
    if exif:
        resultado += struct.pack(orden + "H", len(exif)) + b"".join(entradas_exif)
        resultado += struct.pack(orden + "I", 0)
    return resultado + bytes(datos)


def construir_jpeg(tiff: bytes) -> bytes:
    """JPEG con un segmento JFIF y el EXIF en APP1."""
    jfif = b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    app0 = b"\xff\xe0" + struct.pack(">H", len(jfif) + 2) + jfif
    exif = b"Exif\x00\x00" + tiff
    app1 = b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif
    return b"\xff\xd8" + app0 + app1 + b"\xff\xda\x00\x02" + b"\x00" * 32 + b"\xff\xd9"


def bloque_png(tipo: bytes, datos: bytes) -> bytes:
    """Un bloque PNG: longitud, tipo, datos y un CRC (que el lector no comprueba)."""
    return struct.pack(">I", len(datos)) + tipo + datos + b"\x00\x00\x00\x00"


def construir_png(tiff: bytes) -> bytes:
    """PNG mínimo con el EXIF en un bloque «eXIf»."""
    return (
        b"\x89PNG\r\n\x1a\n"
        + bloque_png(b"IHDR", b"\x00" * 13)
        + bloque_png(b"eXIf", tiff)
        + bloque_png(b"IDAT", b"\x00" * 10)
        + bloque_png(b"IEND", b"")
    )


def construir_webp(tiff: bytes, con_prefijo: bool = True) -> bytes:
    """WebP extendido (VP8X) con el EXIF en su bloque, con o sin el prefijo ``Exif``, que algunos
    programas ponen y otros no.
    """

    def bloque(tipo: bytes, datos: bytes) -> bytes:
        relleno = b"\x00" if len(datos) % 2 else b""
        return tipo + struct.pack("<I", len(datos)) + datos + relleno

    exif = (b"Exif\x00\x00" if con_prefijo else b"") + tiff
    cuerpo = b"WEBP" + bloque(b"VP8X", b"\x00" * 9) + bloque(b"EXIF", exif)
    return b"RIFF" + struct.pack("<I", len(cuerpo)) + cuerpo


def caja(tipo: bytes, datos: bytes) -> bytes:
    """Una caja ISO-BMFF (MP4, MOV, HEIC): tamaño de 4 bytes, tipo y datos."""
    return struct.pack(">I", 8 + len(datos)) + tipo + datos


def construir_heic(tiff: bytes) -> bytes:
    """HEIC mínimo: «ftyp», «meta» (con iinf e iloc) y el EXIF en «mdat»."""
    ftyp = caja(b"ftyp", b"heic" + b"\x00\x00\x00\x00" + b"mif1heic")
    infe = caja(b"infe", b"\x02\x00\x00\x00" + struct.pack(">HH", 1, 0) + b"Exif" + b"\x00")
    iinf = caja(b"iinf", b"\x00\x00\x00\x00" + struct.pack(">H", 1) + infe)
    hdlr = caja(b"hdlr", b"\x00" * 8 + b"pict" + b"\x00" * 13)
    exif = struct.pack(">I", 6) + b"Exif\x00\x00" + tiff

    def iloc(posicion: int) -> bytes:
        # versión 0: desplazamiento y longitud de 4 bytes, sin base; un elemento, un trozo.
        cuerpo = b"\x00\x00\x00\x00" + bytes([0x44, 0x00]) + struct.pack(">H", 1)
        cuerpo += struct.pack(">HHH", 1, 0, 1) + struct.pack(">II", posicion, len(exif))
        return caja(b"iloc", cuerpo)

    def meta(posicion: int) -> bytes:
        return caja(b"meta", b"\x00\x00\x00\x00" + hdlr + iinf + iloc(posicion))

    posicion_exif = len(ftyp) + len(meta(0)) + 8  # tras la cabecera de «mdat»
    return ftyp + meta(posicion_exif) + caja(b"mdat", exif)


def segundos_1904(fecha: datetime) -> int:
    """Segundos desde 1904, como guardan las fechas MP4 y MOV."""
    return int(fecha.timestamp()) + 2_082_844_800


def construir_mp4(fecha: datetime | None, version: int = 0, mdat_grande: bool = False) -> bytes:
    """MP4 con la fecha de creación en «moov/mvhd» (0 si ``fecha`` es None)."""
    segundos = segundos_1904(fecha) if fecha else 0
    if version == 1:
        tiempos = struct.pack(">QQ", segundos, segundos)
    else:
        tiempos = struct.pack(">II", segundos, segundos)
    mvhd = caja(b"mvhd", bytes([version, 0, 0, 0]) + tiempos + b"\x00" * 80)
    moov = caja(b"moov", mvhd + caja(b"trak", b"\x00" * 16))
    ftyp = caja(b"ftyp", b"isom\x00\x00\x02\x00isomiso2mp41")
    if mdat_grande:  # tamaño de 64 bits, delante de «moov» como en muchas cámaras
        datos = b"\x00" * 64
        mdat = struct.pack(">I", 1) + b"mdat" + struct.pack(">Q", 16 + len(datos)) + datos
    else:
        mdat = caja(b"mdat", b"\x00" * 64)
    return ftyp + mdat + moov


def construir_zip(ruta: Path, entradas: dict[str, str]) -> Path:
    """Un zip con esas entradas, como son por dentro los ficheros Office y ODF."""
    with zipfile.ZipFile(ruta, "w", zipfile.ZIP_DEFLATED) as archivo:
        for nombre, contenido in entradas.items():
            archivo.writestr(nombre, contenido)
    return ruta


def escribir(ruta: Path, datos: bytes) -> Path:
    """Escribe esos bytes en ``ruta`` y la devuelve."""
    ruta.write_bytes(datos)
    return ruta


def poner_fecha(ruta: Path, fecha: datetime) -> Path:
    """Cambia la fecha de modificación del fichero y lo devuelve."""
    marca = fecha.timestamp()
    os.utime(ruta, (marca, marca))
    return ruta


TOMA = b"2023:07:15 18:30:00"
FECHA_TOMA = datetime(2023, 7, 15, 18, 30, 0)


# ------------------------------------------------------------------------ fotos


class TestExif:
    """Fotos con EXIF: JPEG, TIFF y RAW, PNG, WebP y HEIC."""

    @pytest.mark.parametrize("orden", ["<", ">"])
    def test_jpeg_usa_la_fecha_de_la_toma(self, tmp_path: Path, orden: str):
        """La fecha de la toma manda sobre la de la imagen, con los bytes en cualquier orden."""
        tiff = construir_tiff(
            ifd0={FECHA_DATETIME: b"2024:01:01 00:00:00"}, exif={FECHA_ORIGINAL: TOMA}, orden=orden
        )
        resultado = fecha_de_metadatos(escribir(tmp_path / "foto.jpg", construir_jpeg(tiff)))
        assert resultado is not None
        assert resultado.fecha == FECHA_TOMA
        assert resultado.origen == "EXIF" and resultado.de_metadatos

    def test_sin_fecha_de_la_toma_usa_la_digitalizada(self, tmp_path: Path):
        """Si falta la de la toma, se usa la de digitalización."""
        tiff = construir_tiff(exif={FECHA_DIGITALIZADA: TOMA})
        resultado = fecha_de_metadatos(escribir(tmp_path / "foto.jpg", construir_jpeg(tiff)))
        assert resultado is not None and resultado.fecha == FECHA_TOMA

    def test_sin_ifd_exif_usa_la_fecha_de_la_imagen(self, tmp_path: Path):
        """Sin bloque EXIF, se usa la fecha del IFD0 (la de la imagen)."""
        tiff = construir_tiff(ifd0={FECHA_DATETIME: TOMA})
        resultado = fecha_de_metadatos(escribir(tmp_path / "foto.jpg", construir_jpeg(tiff)))
        assert resultado is not None and resultado.fecha == FECHA_TOMA

    def test_fecha_sin_rellenar_pasa_a_la_siguiente(self, tmp_path: Path):
        """Una fecha a ceros (cámara sin hora puesta) no vale y se prueba la siguiente."""
        tiff = construir_tiff(
            ifd0={FECHA_DATETIME: TOMA}, exif={FECHA_ORIGINAL: b"0000:00:00 00:00:00"}
        )
        resultado = fecha_de_metadatos(escribir(tmp_path / "foto.jpg", construir_jpeg(tiff)))
        assert resultado is not None and resultado.fecha == FECHA_TOMA

    def test_la_extension_no_importa(self, tmp_path: Path):
        """El formato se reconoce por su contenido, no por la extensión del nombre."""
        tiff = construir_tiff(exif={FECHA_ORIGINAL: TOMA})
        resultado = fecha_de_metadatos(escribir(tmp_path / "FOTO.JPEG.bak", construir_jpeg(tiff)))
        assert resultado is not None and resultado.fecha == FECHA_TOMA

    def test_tiff_y_raw(self, tmp_path: Path):
        """TIFF y los RAW que son TIFF por dentro (DNG, NEF)."""
        tiff = construir_tiff(exif={FECHA_ORIGINAL: TOMA}, orden=">")
        for nombre in ("foto.tif", "foto.dng", "foto.nef"):
            resultado = fecha_de_metadatos(escribir(tmp_path / nombre, tiff))
            assert resultado is not None and resultado.fecha == FECHA_TOMA

    def test_png(self, tmp_path: Path):
        """La fecha del bloque «eXIf» de un PNG."""
        tiff = construir_tiff(exif={FECHA_ORIGINAL: TOMA})
        resultado = fecha_de_metadatos(escribir(tmp_path / "foto.png", construir_png(tiff)))
        assert resultado is not None and resultado.fecha == FECHA_TOMA

    @pytest.mark.parametrize("con_prefijo", [True, False])
    def test_webp(self, tmp_path: Path, con_prefijo: bool):
        """La fecha del EXIF de un WebP, con o sin el prefijo ``Exif``."""
        tiff = construir_tiff(exif={FECHA_ORIGINAL: TOMA})
        datos = construir_webp(tiff, con_prefijo=con_prefijo)
        resultado = fecha_de_metadatos(escribir(tmp_path / "foto.webp", datos))
        assert resultado is not None and resultado.fecha == FECHA_TOMA

    def test_heic(self, tmp_path: Path):
        """La fecha del EXIF de una foto HEIC de iPhone, localizado a través de «iinf» e «iloc»."""
        tiff = construir_tiff(exif={FECHA_ORIGINAL: TOMA})
        resultado = fecha_de_metadatos(escribir(tmp_path / "IMG_0001.HEIC", construir_heic(tiff)))
        assert resultado is not None
        assert resultado.fecha == FECHA_TOMA and resultado.origen == "EXIF"

    def test_png_sin_exif(self, tmp_path: Path):
        """Un PNG sin EXIF (p. ej. una captura) no tiene fecha."""
        datos = b"\x89PNG\r\n\x1a\n" + bloque_png(b"IHDR", b"\x00" * 13) + bloque_png(b"IEND", b"")
        assert fecha_de_metadatos(escribir(tmp_path / "captura.png", datos)) is None


# ------------------------------------------------------------------------ vídeos


class TestVideo:
    """Vídeos MP4 y MOV, con la fecha en «moov/mvhd»."""

    FECHA = datetime(2022, 12, 31, 20, 15, 0)

    @pytest.mark.parametrize("version", [0, 1])
    def test_mp4_usa_la_fecha_de_grabacion(self, tmp_path: Path, version: int):
        """Con las fechas de 32 bits (versión 0) y de 64 (versión 1)."""
        datos = construir_mp4(self.FECHA, version=version)
        resultado = fecha_de_metadatos(escribir(tmp_path / "video.mp4", datos))
        assert resultado is not None
        assert resultado.fecha == self.FECHA and resultado.origen == "MP4/MOV"

    def test_caja_de_64_bits_antes_de_moov(self, tmp_path: Path):
        """Una caja «mdat» con tamaño de 64 bits delante de «moov» se salta bien."""
        datos = construir_mp4(self.FECHA, mdat_grande=True)
        resultado = fecha_de_metadatos(escribir(tmp_path / "video.mov", datos))
        assert resultado is not None and resultado.fecha == self.FECHA

    def test_quicktime_sin_ftyp(self, tmp_path: Path):
        """Un MOV antiguo que empieza por «wide» y no por «ftyp» también se reconoce."""
        mvhd = caja(b"mvhd", b"\x00" * 4 + struct.pack(">II", segundos_1904(self.FECHA), 0))
        datos = caja(b"wide", b"") + caja(b"moov", mvhd)
        resultado = fecha_de_metadatos(escribir(tmp_path / "antiguo.mov", datos))
        assert resultado is not None and resultado.fecha == self.FECHA

    def test_fecha_sin_rellenar(self, tmp_path: Path):
        """Una fecha a cero (1904) cuenta como que no hay fecha."""
        datos = construir_mp4(None)
        assert fecha_de_metadatos(escribir(tmp_path / "video.mp4", datos)) is None


# ------------------------------------------------------------------- documentos


class TestDocumentos:
    """Documentos PDF, Office y OpenDocument."""

    def test_pdf_diccionario_info(self, tmp_path: Path):
        """La CreationDate del diccionario Info de un PDF, con su zona horaria."""
        datos = (
            b"%PDF-1.7\n1 0 obj\n<< /Producer (x) /CreationDate (D:20210304050607+01'00') >>\n"
            b"endobj\n%%EOF"
        )
        resultado = fecha_de_metadatos(escribir(tmp_path / "doc.pdf", datos))
        assert resultado is not None
        assert resultado.fecha == datetime(2021, 3, 4, 5, 6, 7) and resultado.origen == "PDF"

    def test_pdf_fecha_incompleta(self, tmp_path: Path):
        """Una CreationDate con solo año y mes vale: el día es el 1."""
        datos = b"%PDF-1.4\n<< /CreationDate (D:202106) >>\n%%EOF"
        resultado = fecha_de_metadatos(escribir(tmp_path / "doc.pdf", datos))
        assert resultado is not None and resultado.fecha == datetime(2021, 6, 1)

    def test_pdf_xmp(self, tmp_path: Path):
        """La CreateDate de los metadatos XMP de un PDF."""
        datos = b"%PDF-1.6\n<x:xmpmeta><xmp:CreateDate>2020-02-29T12:00:00</xmp:CreateDate>\n%%EOF"
        resultado = fecha_de_metadatos(escribir(tmp_path / "doc.pdf", datos))
        assert resultado is not None and resultado.fecha == datetime(2020, 2, 29, 12, 0, 0)

    def test_pdf_grande_con_la_fecha_al_final(self, tmp_path: Path):
        """En un PDF de 2 MB con la fecha al final, se busca también en el final del fichero."""
        relleno = b"0" * (2 * 1024 * 1024)
        datos = b"%PDF-1.4\n" + relleno + b"\n<< /CreationDate (D:20190101000000Z) >>\n%%EOF"
        resultado = fecha_de_metadatos(escribir(tmp_path / "doc.pdf", datos))
        assert resultado is not None and resultado.fecha == datetime(2019, 1, 1)

    def test_pdf_sin_fecha(self, tmp_path: Path):
        """Un PDF sin fecha de creación no tiene fecha."""
        datos = b"%PDF-1.4\n<< /Producer (x) >>\n%%EOF"
        assert fecha_de_metadatos(escribir(tmp_path / "doc.pdf", datos)) is None

    def test_office(self, tmp_path: Path):
        """La fecha de creación de un .docx, que está en UTC, se pasa a la hora local."""
        core = (
            '<?xml version="1.0"?><cp:coreProperties xmlns:dcterms="http://purl.org/dc/terms/">'
            '<dcterms:created xsi:type="dcterms:W3CDTF">2018-05-20T08:00:00Z</dcterms:created>'
            "</cp:coreProperties>"
        )
        ruta = construir_zip(tmp_path / "informe.docx", {"docProps/core.xml": core})
        resultado = fecha_de_metadatos(ruta)
        # Está en UTC y se pasa a la hora local.
        esperada = datetime(2018, 5, 20, 8, 0, tzinfo=UTC).astimezone()
        assert resultado is not None and resultado.origen == "Office"
        assert resultado.fecha == esperada.replace(tzinfo=None)

    def test_opendocument(self, tmp_path: Path):
        """La fecha de creación de un .ods, con más decimales de los que caben en un datetime."""
        meta = (
            "<office:meta><meta:creation-date>2017-11-02T09:30:00.123456789"
            "</meta:creation-date></office:meta>"
        )
        ruta = construir_zip(tmp_path / "hoja.ods", {"mimetype": "x", "meta.xml": meta})
        resultado = fecha_de_metadatos(ruta)
        assert resultado is not None and resultado.fecha == datetime(2017, 11, 2, 9, 30, 0, 123456)

    def test_zip_normal_no_tiene_fecha(self, tmp_path: Path):
        """Un zip que no es un documento no tiene fecha."""
        ruta = construir_zip(tmp_path / "fotos.zip", {"a.txt": "hola"})
        assert fecha_de_metadatos(ruta) is None


# ------------------------------------------- sin metadatos, dañados o imposibles


class TestSinMetadatos:
    """Ficheros sin metadatos, dañados o con fechas imposibles."""

    def test_fichero_sin_metadatos_usa_la_fecha_del_fichero(self, tmp_path: Path):
        """Sin metadatos, la fecha es la del propio fichero y se dice de dónde sale."""
        ruta = poner_fecha(escribir(tmp_path / "notas.txt", b"hola"), datetime(2016, 8, 9, 10, 0))
        resultado = fecha_del_fichero(ruta)
        assert resultado.origen == ORIGEN_FICHERO and not resultado.de_metadatos
        assert resultado.fecha == datetime(2016, 8, 9, 10, 0)

    def test_fecha_del_sistema_es_la_de_modificacion_o_creacion(self, tmp_path: Path):
        """La fecha del sistema es la más antigua entre la de modificación y la de creación."""
        ruta = poner_fecha(escribir(tmp_path / "a.bin", b"x"), datetime(2015, 1, 1))
        assert fecha_del_sistema(ruta) <= datetime(2015, 1, 1)

    def test_los_metadatos_tienen_prioridad(self, tmp_path: Path):
        """Si hay metadatos, mandan sobre la fecha del fichero."""
        tiff = construir_tiff(exif={FECHA_ORIGINAL: TOMA})
        ruta = escribir(tmp_path / "foto.jpg", construir_jpeg(tiff))
        poner_fecha(ruta, datetime(2024, 5, 5))
        assert fecha_del_fichero(ruta).fecha == FECHA_TOMA

    @pytest.mark.parametrize(
        "datos",
        [
            b"\xff\xd8\xff\xe1\x00\x40Exif\x00\x00II*\x00",  # JPEG cortado
            b"\xff\xd8\xff\xe1\x00\x10Exif\x00\x00XX*\x00\x08\x00",  # TIFF que no es TIFF
            b"II*\x00\xff\xff\xff\xff",  # IFD fuera del fichero
            b"\x00\x00\x00\x18ftypisom" + b"\xff" * 20,  # caja con tamaño imposible
            b"PK\x03\x04no es un zip",
            b"%PDF-1.4 /CreationDate (D:20211399000000)",  # mes 13
            b"",
        ],
    )
    def test_datos_danados_no_dan_error(self, tmp_path: Path, datos: bytes):
        """Un fichero cortado o con datos absurdos no lanza nada: se usa la fecha del fichero."""
        ruta = poner_fecha(escribir(tmp_path / "danado.bin", datos), datetime(2014, 3, 3))
        assert fecha_de_metadatos(ruta) is None
        assert fecha_del_fichero(ruta).fecha == datetime(2014, 3, 3)

    @pytest.mark.parametrize("texto", [b"2099:01:01 00:00:00", b"1950:06:01 00:00:00"])
    def test_fechas_imposibles_se_descartan(self, tmp_path: Path, texto: bytes):
        """Una fecha en el futuro o anterior a 1970 (un reloj sin poner en hora) se descarta."""
        tiff = construir_tiff(exif={FECHA_ORIGINAL: texto})
        assert fecha_de_metadatos(escribir(tmp_path / "foto.jpg", construir_jpeg(tiff))) is None

    def test_fichero_que_no_existe(self, tmp_path: Path):
        """Sin fichero no hay metadatos; pedir la fecha del fichero lanza FileNotFoundError."""
        assert fecha_de_metadatos(tmp_path / "no.jpg") is None
        with pytest.raises(FileNotFoundError):
            fecha_del_fichero(tmp_path / "no.jpg")
