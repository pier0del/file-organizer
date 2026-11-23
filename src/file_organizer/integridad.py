"""Huellas de integridad: comprobar, al deshacer, si un fichero cambió desde que se movió.

Tres modos, según cuánto disco se quiera leer:
- completa: SHA-256 de todo el contenido;
- hibrida: SHA-256 hasta 16 MB; tamaño y fecha para los ficheros más grandes;
- rapida: solo tamaño y fecha, sin leer el contenido.

Mover dentro de un disco es instantáneo, pero el SHA-256 obliga a leer el fichero entero al
organizar y al deshacer: con vídeos son minutos. Por eso el modo por defecto es el híbrido.
Tamaño y fecha se guardan siempre (no cuestan nada); al comprobar se usa lo que haya guardado.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from file_organizer.utilidades import calcular_checksum

# Hasta este tamaño, el modo híbrido calcula el SHA-256 completo.
UMBRAL_HIBRIDO_BYTES = 16 * 1024 * 1024
# FAT32/exFAT guardan la fecha con 2 s de resolución: al mover entre discos puede redondearse,
# así que diferencias menores no cuentan como cambio.
TOLERANCIA_FECHA_NS = 2_000_000_000


class ModoIntegridad(StrEnum):
    """Cómo se calcula la huella de cada fichero al organizarlo."""

    COMPLETA = "completa"
    HIBRIDA = "hibrida"
    RAPIDA = "rapida"


MODO_INTEGRIDAD_DEFECTO = ModoIntegridad.HIBRIDA


@dataclass(frozen=True)
class DescripcionModo:
    """Textos para presentar un modo al usuario en la GUI, el menú y la consola."""

    titulo: str
    resumen: str
    detalle: str


DESCRIPCIONES_INTEGRIDAD: dict[ModoIntegridad, DescripcionModo] = {
    ModoIntegridad.COMPLETA: DescripcionModo(
        titulo="Completa (SHA-256)",
        resumen="Lee cada fichero entero y guarda su SHA-256.",
        detalle=(
            "Detecta cualquier cambio en el contenido, por pequeño que sea. Es la opción más "
            "segura y también la más lenta: lee todos los datos al organizar y otra vez al "
            "deshacer (50 GB de vídeos son varios minutos en un disco duro)."
        ),
    ),
    ModoIntegridad.HIBRIDA: DescripcionModo(
        titulo="Híbrida (recomendada)",
        resumen="SHA-256 hasta 16 MB; tamaño y fecha para los ficheros más grandes.",
        detalle=(
            "Verificación completa para documentos, fotos y código, donde apenas cuesta, y "
            "huella rápida para vídeos, discos virtuales y otros ficheros grandes. Casi tan "
            "rápida como la rápida y casi tan segura como la completa."
        ),
    ),
    ModoIntegridad.RAPIDA: DescripcionModo(
        titulo="Rápida",
        resumen="Solo tamaño y fecha de modificación; no lee el contenido.",
        detalle=(
            "Organizar y deshacer son instantáneos aunque los ficheros ocupen mucho. Detecta "
            "las ediciones normales (cambian el tamaño o la fecha), pero no es una "
            "verificación criptográfica del contenido."
        ),
    ),
}


@dataclass(frozen=True)
class Huella:
    """Lo que se guarda de un fichero para saber después si ha cambiado."""

    checksum: str | None
    tamano: int
    mtime_ns: int


def calcular_huella(ruta: Path, modo: ModoIntegridad) -> Huella:
    """Huella del fichero según el modo; solo lee el contenido si el modo lo pide."""
    info = ruta.stat()
    usar_sha256 = modo is ModoIntegridad.COMPLETA or (
        modo is ModoIntegridad.HIBRIDA and info.st_size <= UMBRAL_HIBRIDO_BYTES
    )
    checksum = calcular_checksum(ruta) if usar_sha256 else None
    return Huella(checksum=checksum, tamano=info.st_size, mtime_ns=info.st_mtime_ns)


def ha_cambiado(
    ruta: Path, checksum: str | None, tamano: int | None, mtime_ns: int | None
) -> bool | None:
    """Si el fichero ya no coincide con su huella guardada.

    True si cambió, False si no, None si no hay datos para saberlo (registros antiguos sin
    huella). Si el tamaño ya difiere, no lee el contenido.
    """
    info = ruta.stat()
    if tamano is not None and info.st_size != tamano:
        return True
    if checksum is not None:
        return calcular_checksum(ruta) != checksum
    if mtime_ns is None:
        return None
    return abs(info.st_mtime_ns - mtime_ns) > TOLERANCIA_FECHA_NS
