"""Utilidades del núcleo que usan varios módulos.

- Ficheros: checksum SHA-256, crear uno que solo pueda leer su dueño, escribir un JSON sin
  dejarlo a medias y nombres que no pisan otro fichero.
- Rutas: si una ruta está dentro de una carpeta.
- Log: la marca SOLO_EN_FICHERO y el filtro que la aplica en la consola.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import os
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_TAMANO_BLOQUE_HASH = 65536

# Sufijo de los ficheros cifrados. Vive aquí, y no en cifrador.py, porque lo necesita
# resolver_conflicto y cifrador.py ya importa este módulo.
SUFIJO_CIFRADO = ".enc"

# Permisos de lo que solo debe poder leer el dueño (POSIX; en Windows no hay equivalente y el
# fichero queda como cualquier otro): claves, historial, log, backups y el temporal al descifrar.
PERMISOS_PRIVADOS = 0o600

# Fecha en los nombres de backups y de copias del historial.
FORMATO_MARCA_TIEMPO = "%Y%m%d_%H%M%S"

# Marca para mensajes del log que la interfaz ya muestra a su manera: van al fichero de log
# pero no a la consola, donde saldrían dos veces. Uso: logger.info(..., extra=SOLO_EN_FICHERO).
SOLO_EN_FICHERO = {"solo_en_fichero": True}


class FiltroConsola(logging.Filter):
    """Deja fuera de la consola los mensajes del log marcados con ``SOLO_EN_FICHERO``."""

    def filter(self, record: logging.LogRecord) -> bool:
        """False (no mostrar) si el mensaje lleva la marca SOLO_EN_FICHERO."""
        return not getattr(record, "solo_en_fichero", False)


def calcular_checksum(ruta: Path) -> str:
    """SHA-256 del contenido de un fichero, en hexadecimal.

    Lee por bloques, así que la memoria no depende del tamaño del fichero.
    """
    sha = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(_TAMANO_BLOQUE_HASH), b""):
            sha.update(bloque)
    return sha.hexdigest()


def crear_fichero_nuevo(ruta: Path, permisos: int = PERMISOS_PRIVADOS) -> int:
    """Crea ``ruta``, que no debe existir (si no, FileExistsError), y da su descriptor para escribir.

    Los permisos se fijan al crearlo, no después: con un ``chmod`` posterior, otro usuario del
    equipo podría abrirlo en ese instante y seguir leyendo todo lo que se escribiera luego. Se usa
    con ``open(descriptor, "wb")`` (o ``"w"``).
    """
    # O_BINARY (solo existe en Windows): que no se conviertan los saltos de línea al escribir.
    banderas = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    return os.open(ruta, banderas, permisos)


def marca_tiempo(momento: datetime) -> str:
    """Fecha para un nombre de fichero, por ejemplo ``20261117_103045``."""
    return momento.strftime(FORMATO_MARCA_TIEMPO)


def escribir_json_atomico(ruta: Path, datos: object, permisos: int | None = None) -> None:
    """Escribe ``datos`` como JSON en ``ruta`` sin dejar nunca un fichero a medias.

    Escribe en ``<ruta>.tmp`` y lo pone en su sitio de una vez (``os.replace``): si el programa
    se corta a mitad, queda el fichero anterior. Con ``permisos`` (p. ej. PERMISOS_PRIVADOS), el
    temporal ya se crea con ellos, así que los datos nunca están a la vista de otros usuarios.
    """
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_suffix(ruta.suffix + ".tmp")
    destino: Path | int = temporal
    if permisos is not None:
        # El que quedara de una escritura cortada no sirve: se crea de nuevo con los permisos.
        temporal.unlink(missing_ok=True)
        destino = crear_fichero_nuevo(temporal, permisos)
    with open(destino, "w", encoding="utf-8") as f:
        json.dump(datos, f, indent=4, ensure_ascii=False)
    os.replace(temporal, ruta)


def esta_dentro(ruta: Path, carpeta: Path | None) -> bool:
    """Si ``ruta`` está dentro de ``carpeta`` (False si no hay carpeta).

    Compara rutas reales: un ``..`` o un enlace no sacan la ruta de la carpeta sin que se note.
    """
    if carpeta is None:
        return False
    try:
        ruta.resolve().relative_to(carpeta.resolve())
    except ValueError:
        return False
    return True


def _nombre_y_extension(ruta: Path) -> tuple[str, str]:
    """Separa el nombre de la extensión para poder numerarlo.

    En un cifrado la extensión incluye la original (``foto.jpg.enc`` → ``foto`` + ``.jpg.enc``),
    para que el número quede delante de ella.
    """
    nombre_base, extension = ruta.stem, ruta.suffix
    if extension == SUFIJO_CIFRADO and Path(nombre_base).suffix:
        return Path(nombre_base).stem, Path(nombre_base).suffix + SUFIJO_CIFRADO
    return nombre_base, extension


def rutas_numeradas(ruta: Path, desde: int) -> Iterator[Path]:
    """``nombre_N.ext`` en la misma carpeta, para N = desde, desde + 1... (sin fin)."""
    nombre_base, extension = _nombre_y_extension(ruta)
    for numero in itertools.count(desde):
        yield ruta.parent / f"{nombre_base}_{numero}{extension}"


def nombres_sin_pisar(ruta: Path) -> Iterator[Path]:
    """``ruta`` tal cual y después ``nombre_2.ext``, ``nombre_3.ext``... (backups y copias del
    historial).
    """
    yield ruta
    yield from rutas_numeradas(ruta, desde=2)


def resolver_conflicto(destino: Path) -> Path:
    """``destino`` si está libre; si no, el primer ``nombre_N.ext`` libre (N = 1, 2...).

    Se usa al mover y al cifrar para no sobrescribir nada. En un cifrado el número va antes de
    la extensión original (``foto_1.jpg.enc``), para que al descifrarlo salga ``foto_1.jpg``.
    """
    if not destino.exists():
        return destino

    candidatos = rutas_numeradas(destino, desde=1)
    while True:
        nueva_ruta = next(candidatos)
        if not nueva_ruta.exists():
            logger.debug(f"Conflicto resuelto: {destino.name} -> {nueva_ruta.name}")
            return nueva_ruta
