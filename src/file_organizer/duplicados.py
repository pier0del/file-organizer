"""Detecta ficheros duplicados: con el mismo contenido que otro ya organizado o que otro de la
misma tanda.

Para no leer de más, compara por pasos y solo sigue con los que empatan: primero el tamaño,
después una huella rápida (el principio y el final del fichero) y, por último, el SHA-256
completo. Así la mayoría de los ficheros no se llegan a leer. Los ficheros vacíos no cuentan.

Un duplicado nunca se borra: se avisa y, si se pide, se aparta en la carpeta Duplicados/.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from file_organizer.utilidades import calcular_checksum

# Carpeta (dentro del destino) donde se apartan los duplicados con AccionDuplicados.MOVER.
CARPETA_DUPLICADOS = "Duplicados"

# Bytes del principio y del final que forman la huella rápida.
_BYTES_HUELLA_RAPIDA = 64 * 1024


class AccionDuplicados(StrEnum):
    """Qué se hace con un fichero duplicado."""

    AVISAR = "avisar"  # se organiza como los demás y se avisa
    MOVER = "mover"  # va a Duplicados/ en vez de a la carpeta de su categoría


ACCION_DUPLICADOS_DEFECTO = AccionDuplicados.AVISAR


@dataclass(frozen=True)
class Duplicado:
    """Un fichero con el mismo contenido que ``original``, que es el que se conserva."""

    fichero: Path
    original: Path

    def texto(self, base: Path) -> str:
        """«copia.jpg = Imagenes/foto.jpg», con el original relativo a ``base`` si está dentro."""
        try:
            original = self.original.relative_to(base).as_posix()
        except ValueError:
            original = self.original.name
        return f"{self.fichero.name} = {original}"


# Clave de un grupo de ficheros que podrían ser iguales: (tamaño, huella rápida).
ClaveGrupo = tuple[int, str]


@dataclass
class _Indice:
    """Ficheros por tamaño y, cuando hace falta, por huella rápida.

    La huella de un fichero se calcula solo cuando aparece otro del mismo tamaño, y una sola
    vez: así buscar candidatos cuesta lo mismo haya pocos o miles de ficheros de ese tamaño.
    """

    sin_huella: dict[int, list[Path]] = field(default_factory=dict)
    con_huella: dict[ClaveGrupo, list[Path]] = field(default_factory=dict)
    tamanos: set[int] = field(default_factory=set)

    def agregar(self, ruta: Path, tamano: int) -> None:
        """Añade un fichero (no vacío) de ese tamaño."""
        self.sin_huella.setdefault(tamano, []).append(ruta)
        self.tamanos.add(tamano)

    def candidatos(self, clave: ClaveGrupo, huella_rapida: Callable[[Path], str]) -> list[Path]:
        """Ficheros con ese tamaño y esa huella rápida (los que podrían ser iguales)."""
        tamano = clave[0]
        for ruta in self.sin_huella.pop(tamano, []):
            try:
                self.con_huella.setdefault((tamano, huella_rapida(ruta)), []).append(ruta)
            except OSError:
                continue  # ya no está o no se puede leer: no cuenta
        return self.con_huella.get(clave, [])


@dataclass
class _Carpeta:
    """Ficheros de una carpeta de destino y su fecha de modificación al listarla."""

    marca: int
    indice: _Indice


@dataclass
class _Huellas:
    """Huellas ya calculadas de un fichero, válidas mientras no cambien su tamaño y su fecha."""

    estado: tuple[int, int]
    rapida: str | None = None
    completa: str | None = None


class DetectorDuplicados:
    """Reconoce duplicados durante una organización (o una sesión de vigilancia).

    Compara cada fichero con los que ya hay en su carpeta de destino y con los anotados antes en
    esta tanda, estén donde estén. Cada carpeta se lista una sola vez y solo se vuelve a listar
    si alguien más la cambia; cada huella se calcula una sola vez.
    """

    def __init__(self) -> None:
        self._carpetas: dict[Path, _Carpeta] = {}
        self._anotados = _Indice()
        self._huellas: dict[Path, _Huellas] = {}

    def original_de(self, fichero: Path, carpeta: Path) -> Path | None:
        """Fichero con el mismo contenido que ``fichero`` en ``carpeta`` o entre los anotados.

        Devuelve None si no hay ninguno. Lanza OSError si no se puede consultar ``fichero``.
        """
        tamano = fichero.stat().st_size
        en_carpeta = self._en_carpeta(carpeta)
        if tamano == 0 or (
            tamano not in en_carpeta.tamanos and tamano not in self._anotados.tamanos
        ):
            return None  # ningún otro de ese tamaño: ni siquiera hace falta leerlo
        try:
            clave = (tamano, self._huella_rapida(fichero))
        except OSError:
            return None
        candidatos: dict[Path, None] = dict.fromkeys(
            [
                *en_carpeta.candidatos(clave, self._huella_rapida),
                *self._anotados.candidatos(clave, self._huella_rapida),
            ]
        )
        candidatos.pop(fichero, None)
        for candidato in candidatos:
            try:
                if self._huella_completa(fichero) == self._huella_completa(candidato):
                    return candidato
            except OSError:
                continue  # ya no está o no se puede leer: no cuenta como igual
        return None

    def anotar(self, origen: Path, actual: Path, *, es_duplicado: bool = False) -> None:
        """Recuerda un fichero ya procesado, que estaba en ``origen`` y ahora está en ``actual``.

        Los que no son duplicados se comparan con los siguientes. Si el fichero se movió a una
        carpeta ya listada, se apunta que el cambio es nuestro para no tener que listarla otra
        vez.
        """
        huellas = self._huellas.pop(origen, None)
        if huellas is not None:
            self._huellas[actual] = huellas
        try:
            tamano = actual.stat().st_size
            listada = self._carpetas.get(actual.parent)
            if listada is not None:
                listada.marca = actual.parent.stat().st_mtime_ns
        except OSError:
            return
        if not es_duplicado and tamano:
            self._anotados.agregar(actual, tamano)

    def _en_carpeta(self, carpeta: Path) -> _Indice:
        """Ficheros que hay en ``carpeta`` (ninguno si la carpeta aún no existe)."""
        try:
            marca = carpeta.stat().st_mtime_ns
        except OSError:
            return _Indice()
        listada = self._carpetas.get(carpeta)
        if listada is None or listada.marca != marca:
            listada = _Carpeta(marca, _listar(carpeta))
            self._carpetas[carpeta] = listada
        return listada.indice

    def _huella_rapida(self, ruta: Path) -> str:
        """Huella del principio y del final del fichero, calculada como mucho una vez."""
        huellas = self._huellas_de(ruta)
        if huellas.rapida is None:
            huellas.rapida = _huella_rapida(ruta)
        return huellas.rapida

    def _huella_completa(self, ruta: Path) -> str:
        """SHA-256 de todo el fichero, calculado como mucho una vez."""
        huellas = self._huellas_de(ruta)
        if huellas.completa is None:
            huellas.completa = calcular_checksum(ruta)
        return huellas.completa

    def _huellas_de(self, ruta: Path) -> _Huellas:
        """Huellas guardadas del fichero; se descartan si cambió su tamaño o su fecha."""
        info = ruta.stat()
        estado = (info.st_size, info.st_mtime_ns)
        huellas = self._huellas.get(ruta)
        if huellas is None or huellas.estado != estado:
            huellas = self._huellas[ruta] = _Huellas(estado)
        return huellas


def _listar(carpeta: Path) -> _Indice:
    """Ficheros no vacíos ni ocultos de la carpeta, sin entrar en subcarpetas."""
    indice = _Indice()
    try:
        entradas = list(os.scandir(carpeta))
    except OSError:
        return indice
    for entrada in entradas:
        try:
            if entrada.name.startswith(".") or not entrada.is_file():
                continue
            tamano = entrada.stat().st_size
        except OSError:
            continue
        if tamano:
            indice.agregar(carpeta / entrada.name, tamano)
    return indice


def _huella_rapida(ruta: Path) -> str:
    """SHA-256 del principio y del final del fichero: distingue casi todos sin leerlos enteros."""
    sha = hashlib.sha256()
    with open(ruta, "rb") as f:
        sha.update(f.read(_BYTES_HUELLA_RAPIDA))
        tamano = f.seek(0, os.SEEK_END)
        if tamano > _BYTES_HUELLA_RAPIDA:
            f.seek(max(_BYTES_HUELLA_RAPIDA, tamano - _BYTES_HUELLA_RAPIDA))
            sha.update(f.read(_BYTES_HUELLA_RAPIDA))
    return sha.hexdigest()
