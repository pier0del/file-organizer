"""Mueve ficheros a su carpeta de categoría y los devuelve a su sitio al deshacer.

Lleva en memoria la pila de pasos del historial (ver historial.py): cada paso se deshace entero
y en orden inverso, como Ctrl+Z, para que la carpeta vuelva a como estaba justo antes de él. Se
guardan como mucho MAX_OPERACIONES pasos.

Cada movimiento guarda una huella del fichero (ver integridad.py) para avisar al deshacer si
cambió.
"""

from __future__ import annotations

import logging
import shutil
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from file_organizer.excepciones import ErrorMoverArchivo
from file_organizer.historial import NOMBRE_ARCHIVO_HISTORIAL as NOMBRE_ARCHIVO_HISTORIAL
from file_organizer.historial import TIPO_ORGANIZAR as TIPO_ORGANIZAR
from file_organizer.historial import TIPO_VIGILANCIA as TIPO_VIGILANCIA
from file_organizer.historial import AlmacenHistorial
from file_organizer.historial import Operacion as Operacion
from file_organizer.historial import RegistroMovimiento as RegistroMovimiento
from file_organizer.historial import (
    leer_carpeta_origen_historial as leer_carpeta_origen_historial,
)
from file_organizer.integridad import (
    MODO_INTEGRIDAD_DEFECTO,
    ModoIntegridad,
    calcular_huella,
    ha_cambiado,
)
from file_organizer.utilidades import SOLO_EN_FICHERO, resolver_conflicto

logger = logging.getLogger(__name__)

# Pasos que se guardan como mucho; al pasar de aquí se descarta el más antiguo.
MAX_OPERACIONES = 20


@dataclass
class ResultadoMovimiento:
    """Resultado de mover un fichero: mensaje para el usuario y su registro en el historial."""

    mensaje: str
    registro: RegistroMovimiento
    # Fichero con el mismo contenido, si este es un duplicado (lo rellena el organizador).
    duplicado_de: Path | None = None
    # Patrón de la regla de la carpeta que decidió adónde va, si fue una (lo rellena el
    # organizador).
    regla: str | None = None


@dataclass
class ResultadoDeshacer:
    """Resultado de deshacer un paso: qué se restauró, qué falló y qué paso queda después."""

    exitosos: int = 0
    fallidos: int = 0
    integridad_fallida: int = 0
    errores: list[tuple[RegistroMovimiento, Exception]] = field(default_factory=list)
    # Restaurados con otro nombre porque su sitio original ya estaba ocupado.
    renombrados: list[tuple[RegistroMovimiento, Path]] = field(default_factory=list)
    # Pasos que quedan en el historial y el que revertirá el siguiente «Deshacer».
    pasos_restantes: int = 0
    siguiente: Operacion | None = None


class MoverArchivos:
    """Mueve ficheros sin sobrescribir ninguno y mantiene el historial para deshacerlo."""

    def __init__(
        self,
        modo_simulacion: bool = False,
        carpeta_origen: Path | None = None,
        carpeta_destino: Path | None = None,
        modo_integridad: ModoIntegridad = MODO_INTEGRIDAD_DEFECTO,
    ):
        self.modo_simulacion = modo_simulacion
        self.modo_integridad = modo_integridad
        # Movimientos del paso en curso (el último, el que se deshace primero) y, debajo, los
        # pasos anteriores, del más antiguo al más reciente.
        self.historial: list[RegistroMovimiento] = []
        self.operaciones_anteriores: list[Operacion] = []
        self.tipo_operacion = TIPO_ORGANIZAR
        self.inicio_operacion = datetime.now()
        # Pasos antiguos descartados por el límite en el último guardado.
        self.descartadas = 0
        self._bloqueo_historial = threading.Lock()
        self.carpeta_origen = carpeta_origen.resolve() if carpeta_origen else None
        self.carpeta_destino = carpeta_destino.resolve() if carpeta_destino else None

    @property
    def _almacen(self) -> AlmacenHistorial:
        """Almacén del historial para las carpetas actuales."""
        return AlmacenHistorial(self.carpeta_origen, self.carpeta_destino)

    @property
    def ruta_historial(self) -> Path | None:
        """Ruta del fichero de historial, o None si no hay carpeta de destino."""
        return self._almacen.ruta

    def mover(self, ruta_archivo: Path, carpeta_base: Path, categoria: str) -> ResultadoMovimiento:
        """Mueve el fichero a ``<carpeta_base>/<categoria>/`` y lo anota en el paso en curso.

        ``categoria`` puede tener varios niveles separados por ``/`` (``Imagenes/2024/03``).

        Si ya existe un fichero con ese nombre, usa ``nombre_1.ext``. En una simulación no mueve
        nada. Lanza FileNotFoundError si no es un fichero, y ErrorMoverArchivo si no se puede
        mover.
        """
        if not ruta_archivo.exists():
            raise FileNotFoundError(f"El fichero no existe: {ruta_archivo}")

        if not ruta_archivo.is_file():
            raise FileNotFoundError(f"La ruta no es un fichero: {ruta_archivo}")

        carpeta_categoria = carpeta_base / categoria
        archivo_destino = resolver_conflicto(carpeta_categoria / ruta_archivo.name)

        # En el historial, siempre rutas completas (ver AlmacenHistorial._completar_rutas).
        registro = RegistroMovimiento(
            origen=ruta_archivo.absolute(), destino=archivo_destino.absolute(), categoria=categoria
        )

        if self.modo_simulacion:
            renombrado = archivo_destino.name if archivo_destino.name != ruta_archivo.name else ""
            logger.info(f"[SIMULACIÓN] {ruta_archivo.name} -> {categoria}/{renombrado}")
            return ResultadoMovimiento(
                mensaje=f"[SIMULACIÓN] Se movería a {archivo_destino}", registro=registro
            )

        # Huella para avisar al deshacer si cambió; si no se puede calcular, se mueve igual.
        try:
            huella = calcular_huella(ruta_archivo, self.modo_integridad)
            registro.checksum = huella.checksum
            registro.tamano = huella.tamano
            registro.mtime_ns = huella.mtime_ns
        except OSError as e:
            logger.debug(f"No se pudo calcular la huella de {ruta_archivo.name}: {e}")

        try:
            carpeta_categoria.mkdir(parents=True, exist_ok=True)
            shutil.move(ruta_archivo, archivo_destino)
        except PermissionError as e:
            raise ErrorMoverArchivo(f"Sin permisos para mover: {ruta_archivo.name}") from e
        except OSError as e:
            raise ErrorMoverArchivo(f"Error al mover {ruta_archivo.name}: {e}") from e

        with self._bloqueo_historial:
            self.historial.append(registro)
        return ResultadoMovimiento(mensaje=f"Movido a {archivo_destino}", registro=registro)

    def deshacer_todo(
        self,
        carpeta_base: Path | None = None,
        progreso: Callable[[int, int], None] | None = None,
    ) -> ResultadoDeshacer:
        """Devuelve a su sitio los ficheros del último paso, en orden inverso.

        - Si el sitio está ocupado, restaura con otro nombre (``nombre_1.ext``) y lo indica en
          ``renombrados``.
        - Si un fichero ya no está donde se movió, lo informa y lo quita del historial, para no
          bloquear los pasos anteriores.
        - Si falla por otro motivo (permisos...), lo deja para reintentarlo.

        Si el paso queda vacío, el siguiente «Deshacer» revertirá el anterior. Con
        ``carpeta_base``, borra las carpetas de categoría que se queden vacías.
        ``progreso(procesados, total)``, si se pasa, se llama tras cada fichero.
        """
        resultado = ResultadoDeshacer()
        carpetas_vaciadas: set[Path] = set()
        reintentables: set[int] = set()
        with self._bloqueo_historial:
            registros = list(self.historial)
        for indice, registro in enumerate(reversed(registros), start=1):
            try:
                self._restaurar(registro, resultado)
                carpetas_vaciadas.add(registro.destino.parent)
            except FileNotFoundError as e:  # ya no está: no se podrá restaurar nunca
                resultado.errores.append((registro, e))
                resultado.fallidos += 1
            except OSError as e:  # permisos, disco...: se queda para reintentarlo
                resultado.errores.append((registro, e))
                resultado.fallidos += 1
                reintentables.add(id(registro))
            if progreso is not None:
                progreso(indice, len(registros))

        self._quitar_paso_deshecho(reintentables, resultado)
        self.guardar_historial()
        if carpeta_base:
            self._limpiar_carpetas_vacias(carpeta_base, carpetas_vaciadas)
        return resultado

    def _restaurar(self, registro: RegistroMovimiento, resultado: ResultadoDeshacer) -> None:
        """Devuelve un fichero a su sitio original, con otro nombre si está ocupado."""
        if not registro.destino.exists():
            raise FileNotFoundError(
                f"{registro.destino.name} ya no está en {registro.destino.parent} (¿se borró o "
                "se movió?): no se puede devolver a su sitio"
            )
        self._anotar_si_ha_cambiado(registro, resultado)

        restaurado = resolver_conflicto(registro.origen)
        restaurado.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(registro.destino, restaurado)
        resultado.exitosos += 1
        if restaurado != registro.origen:
            resultado.renombrados.append((registro, restaurado))
            # Lo avisa cada interfaz (ResultadoDeshacer.renombrados).
            logger.info(
                f"{registro.origen.name} ya existía: restaurado como {restaurado.name}",
                extra=SOLO_EN_FICHERO,
            )

    def _anotar_si_ha_cambiado(
        self, registro: RegistroMovimiento, resultado: ResultadoDeshacer
    ) -> None:
        """Anota en ``resultado`` si el fichero cambió tras moverlo (se restaura igualmente)."""
        try:
            if self._ha_cambiado(registro):
                resultado.integridad_fallida += 1
                logger.warning(
                    f"{registro.destino.name} ha cambiado desde que se organizó; se devuelve a "
                    "su sitio igualmente"
                )
        except OSError as e:
            logger.debug(f"No se pudo verificar la integridad de {registro.destino.name}: {e}")

    def _quitar_paso_deshecho(self, reintentables: set[int], resultado: ResultadoDeshacer) -> None:
        """Deja en el paso solo lo que se puede reintentar; si queda vacío, el anterior pasa a ser
        el último.

        Anota en ``resultado`` cuántos pasos quedan y cuál es el siguiente.
        """
        with self._bloqueo_historial:
            self.historial = [r for r in self.historial if id(r) in reintentables]
            if not self.historial and self.operaciones_anteriores:
                anterior = self.operaciones_anteriores.pop()
                self.historial = anterior.registros
                self.tipo_operacion = anterior.tipo
                self.inicio_operacion = anterior.inicio
            resultado.pasos_restantes = len(self.operaciones_anteriores) + bool(self.historial)
            if self.historial:
                resultado.siguiente = Operacion(
                    self.tipo_operacion, self.inicio_operacion, list(self.historial)
                )

    @staticmethod
    def _limpiar_carpetas_vacias(carpeta_base: Path, candidatas: set[Path]) -> None:
        """Borra las carpetas de categoría que se quedaron vacías al restaurar.

        Solo mira las carpetas de los ficheros restaurados y, si se vacían, las que las contienen
        dentro de ``carpeta_base`` (``2024/03`` y después ``2024``): nunca borra otras carpetas
        vacías del usuario.
        """
        base = carpeta_base.resolve()
        for candidata in sorted(candidatas):
            carpeta = candidata.resolve()
            while carpeta != base and carpeta.is_relative_to(base) and carpeta.is_dir():
                try:
                    carpeta.rmdir()
                except OSError:
                    logger.debug(
                        f"Carpeta no vacía, se mantiene: {carpeta.relative_to(base).as_posix()}"
                    )
                    break
                logger.info(f"Carpeta vacía borrada: {carpeta.relative_to(base).as_posix()}")
                carpeta = carpeta.parent

    def guardar_historial(self) -> None:
        """Guarda todos los pasos del historial en disco.

        Si hay más de MAX_OPERACIONES, descarta los más antiguos y lo indica en ``descartadas``.
        """
        almacen = self._almacen
        almacen.ruta_requerida()  # sin carpetas configuradas no se toca nada
        with self._bloqueo_historial:
            sobran = max(0, len(self.operaciones_anteriores) + 1 - MAX_OPERACIONES)
            if not self.historial:
                sobran = max(0, sobran - 1)
            del self.operaciones_anteriores[:sobran]
            self.descartadas = sobran
            operaciones = self._operaciones_sin_bloqueo()
        if sobran:
            logger.info(f"Historial: se descartan los {sobran} paso(s) más antiguos")
        almacen.guardar(operaciones)

    def cargar_historial(self) -> None:
        """Carga el historial de disco como pila de pasos.

        Lanza ErrorHistorialNoEncontrado si no hay y ErrorHistorialCorrupto si no es de estas
        carpetas o no se puede leer.
        """
        operaciones = self._almacen.cargar()
        with self._bloqueo_historial:
            self.operaciones_anteriores = operaciones[:-1]
            if operaciones:
                self.historial = operaciones[-1].registros
                self.tipo_operacion = operaciones[-1].tipo
                self.inicio_operacion = operaciones[-1].inicio
            else:
                self.historial = []

        total = sum(len(o.registros) for o in operaciones)
        logger.info(
            f"Historial cargado: {total} movimiento(s) en {len(operaciones)} paso(s)",
            extra=SOLO_EN_FICHERO,
        )

    def nueva_operacion(self, tipo: str) -> None:
        """Empieza un paso nuevo: lo que se mueva después se deshará aparte.

        El paso en curso, si tiene movimientos, pasa a ser el anterior.
        """
        with self._bloqueo_historial:
            if self.historial:
                self.operaciones_anteriores.append(
                    Operacion(self.tipo_operacion, self.inicio_operacion, self.historial)
                )
            self.historial = []
            self.tipo_operacion = tipo
            self.inicio_operacion = datetime.now()

    def obtener_operaciones(self) -> list[Operacion]:
        """Pasos del historial, del más antiguo al más reciente (el que se deshará primero)."""
        with self._bloqueo_historial:
            return self._operaciones_sin_bloqueo()

    def _operaciones_sin_bloqueo(self) -> list[Operacion]:
        operaciones = list(self.operaciones_anteriores)
        if self.historial:
            operaciones.append(
                Operacion(self.tipo_operacion, self.inicio_operacion, list(self.historial))
            )
        return operaciones

    def obtener_historial(self) -> list[RegistroMovimiento]:
        """Copia de los movimientos del último paso (el que se deshará primero)."""
        with self._bloqueo_historial:
            return list(self.historial)

    def obtener_estadisticas(self) -> dict[str, int]:
        """Ficheros movidos a cada categoría en el último paso."""
        with self._bloqueo_historial:
            return dict(Counter(registro.categoria for registro in self.historial))

    def apartar_historial(self) -> Path | None:
        """Conserva aparte el historial que no se puede continuar; ver AlmacenHistorial.apartar."""
        return self._almacen.apartar()

    def limpiar_historial(self) -> None:
        """Vacía todos los pasos en memoria (``guardar_historial`` lo lleva a disco)."""
        with self._bloqueo_historial:
            self.historial = []
            self.operaciones_anteriores = []

    @staticmethod
    def _ha_cambiado(registro: RegistroMovimiento) -> bool:
        """Si el fichero movido ya no coincide con su huella (False si no tiene huella)."""
        cambio = ha_cambiado(
            registro.destino, registro.checksum, registro.tamano, registro.mtime_ns
        )
        return bool(cambio)
