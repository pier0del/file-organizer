"""Formato del historial de movimientos y su lectura y escritura en disco.

El historial es una pila de pasos: cada organización y cada sesión de vigilancia es uno, con los
movimientos que hizo. Este módulo los representa y los persiste; deshacerlos y limitar cuántos
se guardan es cosa de MoverArchivos.

Formato (versión 3):
  {"version": 3, "carpeta_origen": ..., "carpeta_destino": ...,
   "operaciones": [{"tipo": "organizar" | "vigilancia", "inicio": ...,
                    "registros": [{"origen", "destino", "categoria", "fecha",
                                   "checksum", "tamano", "mtime_ns"}, ...]}, ...]}

Las versiones 1 y 2 (una sola lista de registros) se siguen leyendo, como un único paso.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from file_organizer.excepciones import ErrorHistorialCorrupto, ErrorHistorialNoEncontrado
from file_organizer.utilidades import (
    PERMISOS_PRIVADOS,
    escribir_json_atomico,
    esta_dentro,
    marca_tiempo,
    nombres_sin_pisar,
)

logger = logging.getLogger(__name__)

NOMBRE_ARCHIVO_HISTORIAL = ".file-organizer-history.json"
VERSION_HISTORIAL = 3

# Tipos de paso del historial.
TIPO_ORGANIZAR = "organizar"
TIPO_VIGILANCIA = "vigilancia"
_TIPOS_OPERACION = (TIPO_ORGANIZAR, TIPO_VIGILANCIA)

# Claves de cada registro según la versión del formato (la 3 usa las de la 2).
_CLAVES_REGISTRO = {
    1: {
        "lista": "records",
        "origen": "source",
        "destino": "destination",
        "categoria": "category",
        "fecha": "timestamp",
    },
    2: {
        "lista": "registros",
        "origen": "origen",
        "destino": "destino",
        "categoria": "categoria",
        "fecha": "fecha",
    },
}


@dataclass
class RegistroMovimiento:
    """Un fichero movido: de dónde, adónde, a qué categoría y su huella."""

    origen: Path
    destino: Path
    categoria: str
    fecha: datetime = field(default_factory=datetime.now)
    checksum: str | None = None
    tamano: int | None = None
    mtime_ns: int | None = None

    def __str__(self) -> str:
        return f"{self.origen.name} -> {self.destino} [{self.categoria}]"


@dataclass
class Operacion:
    """Un paso del historial: una organización o una sesión de vigilancia."""

    tipo: str
    inicio: datetime
    registros: list[RegistroMovimiento] = field(default_factory=list)

    def descripcion(self) -> str:
        """Texto para el usuario, p. ej. «organización del 13/11/2026 10:32»."""
        nombre = "vigilancia" if self.tipo == TIPO_VIGILANCIA else "organización"
        return f"{nombre} del {self.inicio:%d/%m/%Y %H:%M}"


def leer_carpeta_origen_historial(carpeta: Path) -> Path | None:
    """Carpeta de origen que figura en el historial de ``carpeta``, o None.

    El historial se guarda en la carpeta de destino. Esto permite consultarlo o limpiarlo
    indicando solo esa carpeta; para deshacer hay que indicar las dos, que es lo que protege de
    un historial manipulado.
    """
    try:
        with open(Path(carpeta) / NOMBRE_ARCHIVO_HISTORIAL, encoding="utf-8") as f:
            datos = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    origen = datos.get("carpeta_origen") if isinstance(datos, dict) else None
    return Path(origen) if isinstance(origen, str) and origen else None


class AlmacenHistorial:
    """Lee y escribe el historial de un par de carpetas (origen y destino).

    Se guarda en la carpeta de destino. Al leerlo se exige que sea de esas carpetas y que todas
    sus rutas estén dentro: un historial copiado o manipulado nunca mueve ficheros fuera de
    ellas.
    """

    def __init__(self, carpeta_origen: Path | None, carpeta_destino: Path | None) -> None:
        self.carpeta_origen = carpeta_origen
        self.carpeta_destino = carpeta_destino

    @property
    def ruta(self) -> Path | None:
        """Ruta del fichero de historial, o None si no hay carpeta de destino."""
        return self.carpeta_destino / NOMBRE_ARCHIVO_HISTORIAL if self.carpeta_destino else None

    def guardar(self, operaciones: list[Operacion]) -> None:
        """Guarda los pasos sin dejar nunca el fichero a medias (permisos 0o600)."""
        archivo_historial = self.ruta_requerida()
        datos = {
            "version": VERSION_HISTORIAL,
            "carpeta_origen": str(self.carpeta_origen),
            "carpeta_destino": str(self.carpeta_destino),
            "operaciones": [
                {
                    "tipo": operacion.tipo,
                    "inicio": operacion.inicio.isoformat(),
                    "registros": [
                        {
                            "origen": str(r.origen),
                            "destino": str(r.destino),
                            "categoria": r.categoria,
                            "fecha": r.fecha.isoformat(),
                            "checksum": r.checksum,
                            "tamano": r.tamano,
                            "mtime_ns": r.mtime_ns,
                        }
                        for r in operacion.registros
                    ],
                }
                for operacion in operaciones
            ],
        }

        escribir_json_atomico(archivo_historial, datos, permisos=PERMISOS_PRIVADOS)

    def cargar(self) -> list[Operacion]:
        """Pasos guardados, del más antiguo al más reciente.

        Lanza ErrorHistorialNoEncontrado si no hay historial y ErrorHistorialCorrupto si no se
        puede leer, el formato es desconocido o no es de estas carpetas.
        """
        archivo_historial = self.ruta_requerida()
        datos = self._leer_json(archivo_historial)
        version = self._comprobar_cabecera(datos, archivo_historial)
        operaciones = self._leer_pasos(datos, version, archivo_historial)
        self._completar_rutas(operaciones)
        self._comprobar_rutas(operaciones)
        return operaciones

    @staticmethod
    def _leer_json(archivo_historial: Path) -> Any:
        if not archivo_historial.exists():
            raise ErrorHistorialNoEncontrado(f"No hay historial guardado en: {archivo_historial}")
        try:
            with open(archivo_historial, encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            raise ErrorHistorialCorrupto(
                f"El historial no es un JSON válido: {archivo_historial} ({e})"
            ) from e

    def _comprobar_cabecera(self, datos: Any, archivo_historial: Path) -> int:
        """Comprueba la versión del formato y que el historial sea de estas carpetas; devuelve la
        versión.
        """
        version = datos.get("version") if isinstance(datos, dict) else None
        if version not in (*_CLAVES_REGISTRO, VERSION_HISTORIAL):
            raise ErrorHistorialCorrupto(f"El historial está dañado: {archivo_historial}")
        if datos.get("carpeta_origen") != str(self.carpeta_origen) or datos.get(
            "carpeta_destino"
        ) != str(self.carpeta_destino):
            raise ErrorHistorialCorrupto(
                "El historial es de otras carpetas (¿se copió o se movió la carpeta?); por "
                "seguridad no se usa"
            )
        return int(version)

    def _leer_pasos(
        self, datos: dict[str, Any], version: int, archivo_historial: Path
    ) -> list[Operacion]:
        """Pasos del historial; en las versiones 1 y 2, toda la lista es un único paso."""
        try:
            if version == VERSION_HISTORIAL:
                return [self._leer_operacion(o) for o in datos["operaciones"]]
            claves = _CLAVES_REGISTRO[version]
            registros = [self._leer_registro(r, claves) for r in datos[claves["lista"]]]
            if not registros:
                return []
            # También min() puede fallar (fechas con y sin zona horaria): es un formato inválido.
            return [Operacion(TIPO_ORGANIZAR, min(r.fecha for r in registros), registros)]
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            raise ErrorHistorialCorrupto(
                f"El historial está dañado: {archivo_historial} ({e})"
            ) from e

    def _completar_rutas(self, operaciones: list[Operacion]) -> None:
        """Completa las rutas relativas de historiales antiguos, sin saber desde dónde se guardaron.

        Antes, organizar con una ruta relativa («--carpeta Descargas») guardaba la de cada fichero
        también relativa, y el historial solo valía desde la carpeta en que se abrió el programa:
        desde otra, o desde la GUI, se rechazaba. No hace falta saber cuál era esa carpeta:

        - el destino de un fichero es siempre ``carpeta de destino / categoría / nombre``;
        - su origen, si origen y destino son la misma carpeta, es lo que sigue en su ruta al
          mismo comienzo que la del destino (así se recuperan también las subcarpetas de una
          vigilancia); si no, el fichero estaba suelto en la carpeta de origen, que es lo que
          organiza «organizar».

        Lo completado se comprueba después como cualquier otra ruta, y al guardar el historial
        ya quedan rutas completas.
        """
        if self.carpeta_origen is None or self.carpeta_destino is None:
            return
        for operacion in operaciones:
            for registro in operacion.registros:
                if registro.origen.is_absolute() and registro.destino.is_absolute():
                    continue
                comienzo: tuple[str, ...] | None = None
                if not registro.destino.is_absolute():
                    final = len(Path(registro.categoria).parts) + 1
                    comienzo = registro.destino.parts[:-final]
                    registro.destino = (
                        self.carpeta_destino / registro.categoria / registro.destino.name
                    )
                if not registro.origen.is_absolute():
                    partes = registro.origen.parts
                    if (
                        comienzo is not None
                        and self.carpeta_origen == self.carpeta_destino
                        and partes[: len(comienzo)] == comienzo
                        and len(partes) > len(comienzo)
                    ):
                        registro.origen = self.carpeta_origen.joinpath(*partes[len(comienzo) :])
                    else:
                        registro.origen = self.carpeta_origen / registro.origen.name

    def _comprobar_rutas(self, operaciones: list[Operacion]) -> None:
        """Exige que todas las rutas estén dentro de las carpetas (protege de historiales
        manipulados).
        """
        for operacion in operaciones:
            for registro in operacion.registros:
                if not esta_dentro(registro.origen, self.carpeta_origen) or not esta_dentro(
                    registro.destino, self.carpeta_destino
                ):
                    raise ErrorHistorialCorrupto(
                        "El historial tiene rutas fuera de estas carpetas; por seguridad no se usa"
                    )

    def apartar(self) -> Path | None:
        """Renombra el historial a ``.file-organizer-history.<fecha>.json`` y devuelve la copia.

        Se usa cuando no se puede continuar (dañado, o de otras carpetas): se conserva aparte en
        vez de mezclarlo o perderlo. None si no había historial.
        """
        archivo_historial = self.ruta_requerida()
        if not archivo_historial.exists():
            return None
        nombre = (
            f"{archivo_historial.stem}.{marca_tiempo(datetime.now())}{archivo_historial.suffix}"
        )
        candidatos = nombres_sin_pisar(archivo_historial.with_name(nombre))
        while True:
            copia = next(candidatos)
            if not copia.exists():
                archivo_historial.rename(copia)
                logger.info(f"Historial anterior guardado aparte: {copia}")
                return copia

    def ruta_requerida(self) -> Path:
        """Ruta del historial; lanza ErrorHistorialCorrupto si faltan las carpetas."""
        if not self.ruta or not self.carpeta_origen or not self.carpeta_destino:
            raise ErrorHistorialCorrupto("No se ha configurado una carpeta para el historial")
        return self.ruta

    @staticmethod
    def _leer_registro(datos: dict[str, Any], claves: dict[str, str]) -> RegistroMovimiento:
        """RegistroMovimiento de un registro guardado (KeyError o ValueError si no vale)."""
        return RegistroMovimiento(
            origen=Path(datos[claves["origen"]]),
            destino=Path(datos[claves["destino"]]),
            categoria=datos[claves["categoria"]],
            fecha=datetime.fromisoformat(datos[claves["fecha"]]),
            checksum=datos.get("checksum"),
            tamano=datos.get("tamano"),
            mtime_ns=datos.get("mtime_ns"),
        )

    @classmethod
    def _leer_operacion(cls, datos: dict[str, Any]) -> Operacion:
        """Operacion a partir de un paso guardado (versión 3)."""
        if datos["tipo"] not in _TIPOS_OPERACION:
            raise ValueError(f"tipo de paso desconocido: {datos['tipo']!r}")
        return Operacion(
            tipo=datos["tipo"],
            inicio=datetime.fromisoformat(datos["inicio"]),
            registros=[cls._leer_registro(r, _CLAVES_REGISTRO[2]) for r in datos["registros"]],
        )
