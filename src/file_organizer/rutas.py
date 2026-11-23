"""Preparar y comprobar las rutas que escribe o pega el usuario, igual en la consola y en la GUI.

Primero ``preparar_ruta`` deja el texto listo para usar:

- quita los espacios de alrededor y las comillas que añaden «Copiar como ruta de acceso» en
  Windows o arrastrar una carpeta a la terminal;
- en macOS y Linux, quita los escapes de la terminal (``Mis\\ Documentos``);
- cambia ``~`` por la carpeta personal;
- y devuelve None si no queda nada. ``Path("")`` sería la carpeta actual, y por eso pulsar
  ENTER sin escribir nada en el menú organizaba la carpeta desde la que se abrió el programa.

Después, las funciones ``comprobar_*`` lanzan ErrorRuta, con el motivo, si la ruta no sirve
para lo que se quiere hacer con ella.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from file_organizer.excepciones import ErrorRuta

_COMILLAS = "\"'"
# Un carácter escapado con «\», como los escribe la terminal de macOS o Linux al arrastrar.
_ESCAPE_DE_TERMINAL = re.compile(r"\\(.)")


def limpiar_texto_ruta(texto: str) -> str:
    """El texto de una ruta sin espacios alrededor ni las comillas o escapes de quien la copió.

    Las comillas solo se quitan si rodean la ruta entera. Los escapes con ``\\`` solo se quitan
    en macOS y Linux (en Windows ``\\`` separa carpetas) y si la ruta tal cual no existe, por si
    algún nombre lleva de verdad una barra invertida.
    """
    texto = texto.strip()
    if len(texto) >= 2 and texto[0] == texto[-1] and texto[0] in _COMILLAS:
        return texto[1:-1]
    if os.sep == "/" and "\\" in texto and not Path(texto).exists():
        return _ESCAPE_DE_TERMINAL.sub(r"\1", texto)
    return texto


def preparar_ruta(texto: str | None) -> Path | None:
    """La ruta que escribió o pegó el usuario, lista para usar; None si no indicó ninguna."""
    if texto is None:
        return None
    limpio = limpiar_texto_ruta(texto)
    if not limpio.strip():  # nada, o solo espacios entre comillas
        return None
    ruta = Path(limpio)
    try:
        return ruta.expanduser()
    except RuntimeError:  # no se sabe cuál es la carpeta personal: se deja tal cual
        return ruta


def _fichero_por_encima(ruta: Path) -> Path | None:
    """La primera ruta que existe al subir desde ``ruta``, si es un fichero (no una carpeta)."""
    existente = next((p for p in (ruta, *ruta.parents) if p.exists()), None)
    if existente is not None and not existente.is_dir():
        return existente
    return None


def comprobar_carpeta(ruta: Path) -> Path:
    """La carpeta, si existe y es una carpeta (la que se organiza, vigila o consulta)."""
    if not ruta.exists():
        raise ErrorRuta("La carpeta no existe", ruta)
    if not ruta.is_dir():
        raise ErrorRuta("La ruta no es una carpeta, es un fichero", ruta)
    return ruta


def comprobar_destino(ruta: Path) -> Path:
    """La carpeta de destino, si ya es una carpeta o se puede crear.

    No vale si es un fichero ni si lo es alguna de las carpetas por encima, porque entonces no se
    podría crear.
    """
    if ruta.exists() and not ruta.is_dir():
        raise ErrorRuta("El destino es un fichero, no una carpeta", ruta)
    fichero = _fichero_por_encima(ruta)
    if fichero is not None:
        raise ErrorRuta(f"El destino no vale: «{fichero}» es un fichero, no una carpeta", ruta)
    return ruta


def comprobar_existe(ruta: Path) -> Path:
    """La ruta, si existe (un fichero o una carpeta, como lo que se cifra o se descifra)."""
    if not ruta.exists():
        raise ErrorRuta("No existe", ruta)
    return ruta


def comprobar_fichero_de_categorias(ruta: Path) -> Path:
    """El fichero de categorías, si existe y es un .json."""
    if not ruta.exists():
        raise ErrorRuta("El fichero de categorías no existe", ruta)
    if not ruta.is_file() or ruta.suffix.lower() != ".json":
        raise ErrorRuta("El fichero de categorías debe ser un fichero .json", ruta)
    return ruta


def comprobar_fichero_nuevo(ruta: Path) -> Path:
    """La ruta de un fichero que se va a crear (como una clave nueva), si se puede crear allí.

    Si ya existe un fichero, lo decide quien lo crea (la clave, por ejemplo, no se sobrescribe).
    """
    if ruta.is_dir():
        raise ErrorRuta("Es una carpeta: indica también el nombre del fichero", ruta)
    fichero = _fichero_por_encima(ruta.parent)
    if fichero is not None:
        raise ErrorRuta(f"No se puede crear: «{fichero}» es un fichero, no una carpeta", ruta)
    return ruta
