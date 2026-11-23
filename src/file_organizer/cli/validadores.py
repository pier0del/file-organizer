"""Comprueba las rutas de los comandos y del menú antes de usarlas y explica por qué no valen.

Preparar y comprobar cada ruta lo hace el núcleo (rutas.py), igual que en la GUI: aquí solo se
muestra el resultado en la consola. Cada función devuelve la ruta ya preparada (sin comillas, con
``~`` cambiado por la carpeta personal...) o None si no sirve, tras explicar por qué.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from file_organizer.cli.salida import imprimir_error, resaltar
from file_organizer.excepciones import ErrorRuta
from file_organizer.rutas import (
    comprobar_carpeta,
    comprobar_destino,
    comprobar_existe,
    comprobar_fichero_de_categorias,
    comprobar_fichero_nuevo,
    preparar_ruta,
)
from file_organizer.utilidades import SOLO_EN_FICHERO

logger = logging.getLogger(__name__)


def _rechazar(motivo: str, ruta: Path | None = None) -> None:
    """Muestra el error, con la ruta resaltada si la hay, y lo deja en el fichero de log."""
    if ruta is None:
        imprimir_error(motivo)
        logger.error(f"[ERROR] {motivo}", extra=SOLO_EN_FICHERO)
        return
    imprimir_error(f"{motivo}: {resaltar(ruta)}")
    logger.error(f"[ERROR] {motivo}: {ruta}", extra=SOLO_EN_FICHERO)


def _validar(texto: str | None, comprobar: Callable[[Path], Path], vacia: str) -> Path | None:
    """Prepara la ruta y la comprueba; si está vacía o no sirve, lo explica y devuelve None."""
    ruta = preparar_ruta(texto)
    if ruta is None:
        _rechazar(vacia)
        return None
    try:
        return comprobar(ruta)
    except ErrorRuta as e:
        _rechazar(e.motivo, e.ruta)
        return None


def validar_carpeta(
    texto_carpeta: str | None, vacia: str = "No has indicado ninguna carpeta"
) -> Path | None:
    """La carpeta que se organiza, vigila o consulta, si existe y es una carpeta."""
    return _validar(texto_carpeta, comprobar_carpeta, vacia)


def validar_destino(texto_destino: str | None) -> Path | None:
    """La carpeta de destino, si es una carpeta o se puede crear."""
    return _validar(texto_destino, comprobar_destino, "No has indicado la carpeta de destino")


def validar_configuracion(texto_config: str | None) -> Path | None:
    """El fichero de categorías, si existe y es un .json."""
    return _validar(
        texto_config, comprobar_fichero_de_categorias, "No has indicado el fichero de categorías"
    )


def validar_existente(texto_ruta: str | None, vacia: str) -> Path | None:
    """Un fichero o una carpeta que tiene que existir (lo que se cifra, o un fichero de clave)."""
    return _validar(texto_ruta, comprobar_existe, vacia)


def validar_fichero_nuevo(texto_ruta: str | None, vacia: str) -> Path | None:
    """La ruta de un fichero que se va a crear, si se puede crear allí (no es una carpeta...)."""
    return _validar(texto_ruta, comprobar_fichero_nuevo, vacia)
