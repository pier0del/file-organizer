"""Organiza una carpeta: clasifica cada fichero (por tipo, por fecha o por las dos), lo mueve y
guarda el historial para deshacerlo. Las reglas de la carpeta (ver reglas.py) mandan sobre la
clasificación, y por el camino se reconocen los duplicados (ver duplicados.py).

Es la pieza central: la consola, el menú, la GUI y el vigilante hablan con OrganizadorArchivos.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from file_organizer.backup import NOMBRE_CARPETA_BACKUPS as NOMBRE_CARPETA_BACKUPS
from file_organizer.backup import crear_backup_zip
from file_organizer.bloqueo import BloqueoCarpeta
from file_organizer.clasificador import (
    CRITERIO_DEFECTO,
    ClasificadorFicheros,
    ClasificadorPorFecha,
    CriterioAgrupacion,
    crear_clasificador,
)
from file_organizer.duplicados import (
    ACCION_DUPLICADOS_DEFECTO,
    CARPETA_DUPLICADOS,
    AccionDuplicados,
    DetectorDuplicados,
    Duplicado,
)
from file_organizer.excepciones import (
    ErrorHistorialCorrupto,
    ErrorHistorialNoEncontrado,
    ErrorMoverArchivo,
    ErrorOrigenNoEncontrado,
)
from file_organizer.historial import (
    NOMBRE_ARCHIVO_HISTORIAL,
    TIPO_ORGANIZAR,
    Operacion,
    RegistroMovimiento,
)
from file_organizer.ignorados import ListaIgnorados
from file_organizer.integridad import MODO_INTEGRIDAD_DEFECTO, ModoIntegridad
from file_organizer.metadatos import ContadorFechas
from file_organizer.mover_archivos import (
    MAX_OPERACIONES,
    MoverArchivos,
    ResultadoDeshacer,
    ResultadoMovimiento,
)
from file_organizer.reglas import Regla, ReglasCarpeta
from file_organizer.utilidades import SOLO_EN_FICHERO

logger = logging.getLogger(__name__)

# Log del propio programa (y sus copias .1, .2...): nunca se organiza.
NOMBRE_ARCHIVO_LOG = "file-organizer.log"

# Callback de progreso: recibe (procesados, total).
CallbackProgreso = Callable[[int, int], None]


class OrganizadorArchivos:
    """Organiza los ficheros de una carpeta en subcarpetas por categoría, con deshacer.

    Coordina el clasificador, la lista de ignorados, MoverArchivos y el bloqueo de la carpeta,
    sin hacer el trabajo de ninguno. El clasificador es el de ``agrupar`` (por defecto, por
    tipo) o el que se le pase (cualquier ClasificadorFicheros).
    """

    def __init__(
        self,
        carpeta_origen: str | Path,
        modo_simulacion: bool = False,
        carpeta_destino: str | Path | None = None,
        ruta_config: str | Path | None = None,
        backup: bool = False,
        modo_integridad: ModoIntegridad = MODO_INTEGRIDAD_DEFECTO,
        *,
        agrupar: CriterioAgrupacion = CRITERIO_DEFECTO,
        duplicados: AccionDuplicados = ACCION_DUPLICADOS_DEFECTO,
        clasificador: ClasificadorFicheros | None = None,
    ):
        # Rutas completas: el historial guarda las de los ficheros, y con una relativa
        # («Descargas») solo valdrían desde la carpeta en que se abrió el programa.
        self.carpeta_origen = Path(carpeta_origen).absolute()
        self.carpeta_destino = (
            Path(carpeta_destino).absolute() if carpeta_destino else self.carpeta_origen
        )
        self.modo_simulacion = modo_simulacion
        self.backup = backup
        self.ruta_config = Path(ruta_config) if ruta_config else None
        # De dónde salen las fechas (agrupar por fecha y reglas con {año}), para el resumen.
        self.fechas = ContadorFechas()
        self.clasificador: ClasificadorFicheros = (
            crear_clasificador(agrupar, ruta_config, self.fechas)
            if clasificador is None
            else clasificador
        )
        self.lista_ignorados = ListaIgnorados.desde_carpeta(self.carpeta_origen)
        # Las reglas se leen al organizar o vigilar: un fichero de reglas roto no debe impedir
        # deshacer ni consultar el historial.
        self.reglas = ReglasCarpeta(self.carpeta_origen)
        self._reglas_aplicadas: Counter[Regla] = Counter()
        self.duplicados = duplicados
        self.detector = DetectorDuplicados()
        self.mover = MoverArchivos(
            modo_simulacion=modo_simulacion,
            carpeta_origen=self.carpeta_origen,
            carpeta_destino=self.carpeta_destino,
            modo_integridad=modo_integridad,
        )
        # Lo respetan todos los procesos del programa (ver bloqueo.py).
        self.bloqueo = BloqueoCarpeta(self.carpeta_destino)
        self.ultimos_errores: list[tuple[Path, Exception]] = []
        # Avisos de la última ejecución para el usuario (p. ej. historial apartado).
        self.avisos: list[str] = []
        # Movimientos de la última ejecución (en una simulación, los que se harían).
        self.ultimos_movimientos: list[RegistroMovimiento] = []
        # Duplicados encontrados en la última ejecución.
        self.ultimos_duplicados: list[Duplicado] = []

    def esta_ignorado(self, elemento: Path) -> bool:
        """Si el fichero no se organiza: oculto, del propio programa (historial, backups, log) o
        ignorado por el usuario.
        """
        nombre = elemento.name
        if nombre.startswith("."):
            return True
        if nombre in (NOMBRE_ARCHIVO_HISTORIAL, NOMBRE_CARPETA_BACKUPS):
            return True
        if nombre == NOMBRE_ARCHIVO_LOG or nombre.startswith(NOMBRE_ARCHIVO_LOG + "."):
            return True
        return self.lista_ignorados.debe_ignorar(elemento)

    def _ficheros_pendientes(self) -> list[Path]:
        """Ficheros de la carpeta de origen que se organizarían (sin carpetas ni ignorados)."""
        return [
            elemento
            for elemento in self.carpeta_origen.iterdir()
            if elemento.is_file() and not self.esta_ignorado(elemento)
        ]

    def crear_backup(self) -> Path | None:
        """Guarda en un .zip los ficheros que se van a organizar y devuelve su ruta.

        Va a ``<destino>/.file-organizer-backups/``. Devuelve None si no hay ficheros.
        """
        ficheros = self._ficheros_pendientes()
        if not ficheros:
            logger.info("Backup: no hay ficheros que respaldar")
            return None

        ruta_zip = crear_backup_zip(
            ficheros, self.carpeta_destino / NOMBRE_CARPETA_BACKUPS, datetime.now()
        )
        logger.info(f"Backup creado: {ruta_zip} ({len(ficheros)} fichero(s))")
        return ruta_zip

    def ejecutar(self, progreso: CallbackProgreso | None = None) -> int:
        """Organiza la carpeta y devuelve cuántos ficheros se procesaron.

        ``progreso(procesados, total)``, si se pasa, se llama tras cada fichero. Salvo en una
        simulación, bloquea la carpeta mientras dura: lanza ErrorCarpetaEnUso si otra operación
        la usa, ErrorOrigenNoEncontrado si la carpeta no existe y ErrorReglas si sus reglas no
        son válidas (antes de mover nada).
        """
        if not self.carpeta_origen.exists() or not self.carpeta_origen.is_dir():
            raise ErrorOrigenNoEncontrado(f"La carpeta de origen no existe: {self.carpeta_origen}")
        self.reglas.cargar()
        if self.modo_simulacion:
            return self._ejecutar(progreso)
        with self.bloqueo:
            return self._ejecutar(progreso)

    def preparar_historial(self, tipo: str) -> str | None:
        """Carga los pasos guardados y empieza uno nuevo encima, para deshacerlo por separado.

        Si el historial guardado no se puede usar (dañado, o de otras carpetas), se aparta y se
        empieza otro. Devuelve el aviso para el usuario, o None.
        """
        aviso = None
        try:
            self.mover.cargar_historial()
        except ErrorHistorialNoEncontrado:
            pass
        except (ErrorHistorialCorrupto, OSError) as e:
            try:
                copia = self.mover.apartar_historial()
            except OSError as error_copia:
                logger.warning(f"No se pudo apartar el historial: {error_copia}")
                aviso = f"No se puede continuar el historial anterior ({e})."
            else:
                guardado = f"; se ha guardado aparte como {copia.name}" if copia else ""
                aviso = f"No se puede continuar el historial anterior ({e}){guardado}. Se empieza uno nuevo."
        self.mover.nueva_operacion(tipo)
        return aviso

    def _ejecutar(self, progreso: CallbackProgreso | None) -> int:
        self.avisos = []
        if not self.modo_simulacion:
            aviso = self.preparar_historial(TIPO_ORGANIZAR)
            if aviso is not None:
                self.avisos.append(aviso)
        if self.modo_simulacion:
            logger.info(f"Simulando la organización de {self.carpeta_origen} (no se mueve nada)")
        else:
            logger.info(f"Organizando {self.carpeta_origen}")

        if self.backup and not self.modo_simulacion:
            self.crear_backup()

        # Los nombres más cortos primero: entre ficheros iguales, el que se conserva como
        # original es «foto.jpg» y no «foto (1).jpg» ni «copia de foto.jpg».
        pendientes = sorted(self._ficheros_pendientes(), key=lambda f: (len(f.name), f.name))
        total = len(pendientes)

        archivos_procesados = 0
        self.ultimos_errores = []
        self.ultimos_movimientos = []
        self.ultimos_duplicados = []
        self.detector = DetectorDuplicados()
        self._reglas_aplicadas = Counter()
        self.fechas.reiniciar()

        for indice, elemento in enumerate(pendientes, start=1):
            try:
                resultado = self.organizar_fichero(elemento)
                self.ultimos_movimientos.append(resultado.registro)
                if resultado.duplicado_de is not None:
                    self.ultimos_duplicados.append(Duplicado(elemento, resultado.duplicado_de))
                archivos_procesados += 1
            except (OSError, ErrorMoverArchivo) as e:
                self.ultimos_errores.append((elemento, e))
                logger.warning(f"No se pudo organizar {elemento.name}: {e}")

            if progreso is not None:
                progreso(indice, total)

        hechos = "se organizarían" if self.modo_simulacion else "organizados"
        logger.info(f"Terminado: {archivos_procesados} de {total} fichero(s) {hechos}")

        if not self.modo_simulacion:
            self.mover.guardar_historial()
            if self.mover.descartadas:
                self.avisos.append(
                    f"El historial guarda como mucho {MAX_OPERACIONES} pasos: se ha descartado "
                    "el más antiguo, que ya no se podrá deshacer."
                )

        return archivos_procesados

    def organizar_fichero(
        self, ruta_archivo: Path, *, anunciado_aparte: bool = False
    ) -> ResultadoMovimiento:
        """Clasifica un fichero y lo mueve a su carpeta (en una simulación, no lo mueve).

        Si una regla de la carpeta coincide, va a la carpeta de la regla (y se indica en
        ``regla``); si se agrupa por fecha, debajo va ``AAAA/MM``, salvo que la regla ya lleve
        ``{año}`` o ``{mes}``. Si es un duplicado, lo indica en ``duplicado_de`` y, con
        AccionDuplicados.MOVER, lo aparta en Duplicados/. Lo usan ``ejecutar`` con cada fichero y
        el vigilante con cada uno que llega. Lanza OSError (p. ej. FileNotFoundError) o
        ErrorMoverArchivo si no se puede mover, y ErrorReglas si las reglas no son válidas.

        Cada movimiento se anota en el log. Con ``anunciado_aparte`` (el vigilante, que ya lo
        avisa por su notificador) va solo al fichero de log, para no verlo dos veces en la
        consola.
        """
        regla = self.reglas.regla_para(ruta_archivo.name)
        if regla is not None:
            categoria = regla.carpeta_para(ruta_archivo, fecha_de=self.fechas.fecha)
            if isinstance(self.clasificador, ClasificadorPorFecha) and not regla.lleva_fecha:
                categoria = f"{categoria}/{self.clasificador.carpeta_de_fecha(ruta_archivo)}"
            self._reglas_aplicadas[regla] += 1
        else:
            categoria = self.clasificador.clasificar(ruta_archivo)
        original = self.detector.original_de(ruta_archivo, self.carpeta_destino / categoria)
        if original is not None:
            logger.info(f"Duplicado: {ruta_archivo.name} = {original}", extra=SOLO_EN_FICHERO)
            if self.duplicados is AccionDuplicados.MOVER:
                categoria = CARPETA_DUPLICADOS
        resultado = self.mover.mover(ruta_archivo, self.carpeta_destino, categoria)
        resultado.duplicado_de = original
        resultado.regla = regla.patron if regla is not None else None
        actual = ruta_archivo if self.modo_simulacion else resultado.registro.destino
        self.detector.anotar(ruta_archivo, actual, es_duplicado=original is not None)
        if not self.modo_simulacion:
            destino = resultado.registro.destino
            renombrado = destino.name if destino.name != ruta_archivo.name else ""
            logger.info(
                f"Movido: {ruta_archivo.name} -> {resultado.registro.categoria}/{renombrado}",
                extra=SOLO_EN_FICHERO if anunciado_aparte else None,
            )
        return resultado

    def esta_en_carpeta_de_categoria(self, ruta_archivo: Path) -> bool:
        """Si el fichero ya está en una de las carpetas que crea al organizar (``Imagenes/``,
        ``2024/03/``, ``Duplicados/``, las de las reglas...) dentro del destino.
        """
        try:
            carpeta = ruta_archivo.parent.resolve().relative_to(self.carpeta_destino.resolve())
        except (OSError, ValueError):
            return False
        return (
            carpeta == Path(CARPETA_DUPLICADOS)
            or self.reglas.es_carpeta_de_regla(
                carpeta, agrupa_por_fecha=isinstance(self.clasificador, ClasificadorPorFecha)
            )
            or self.clasificador.es_carpeta_de_categoria(carpeta)
        )

    def resumen_reglas(self) -> str | None:
        """Qué reglas de la carpeta se aplicaron en la última ejecución, para el usuario; None si
        ninguna.

        Por ejemplo: «Reglas de la carpeta aplicadas a 5 fichero(s): «factura*» 3, «*.pdf» 2.»
        """
        if not self._reglas_aplicadas:
            return None
        total = sum(self._reglas_aplicadas.values())
        detalle = ", ".join(
            f"«{regla.patron}» {cantidad}"
            for regla, cantidad in self._reglas_aplicadas.most_common()
        )
        return f"Reglas de la carpeta aplicadas a {total} fichero(s): {detalle}."

    def resumen_fechas(self) -> str | None:
        """De dónde salieron las fechas usadas en la última ejecución (al agrupar por fecha o en
        reglas con ``{año}``), para el usuario; None si no se usó ninguna.
        """
        return self.fechas.resumen()

    def cargar_historial(self) -> list[Operacion]:
        """Lee el historial guardado y devuelve sus pasos, del más antiguo al más reciente.

        Lanza ErrorHistorialNoEncontrado si no hay y ErrorHistorialCorrupto si no se puede usar.
        """
        self.mover.cargar_historial()
        return self.mover.obtener_operaciones()

    def operaciones(self) -> list[Operacion]:
        """Pasos del historial en memoria, del más antiguo al más reciente."""
        return self.mover.obtener_operaciones()

    def estadisticas(self) -> dict[str, int]:
        """Ficheros movidos a cada categoría en el último paso del historial."""
        return self.mover.obtener_estadisticas()

    def guardar_historial(self) -> None:
        """Guarda en disco el historial que hay en memoria."""
        self.mover.guardar_historial()

    def deshacer(self, progreso: CallbackProgreso | None = None) -> ResultadoDeshacer | None:
        """Deshace el último paso del historial; None si no hay historial.

        ``progreso(procesados, total)``, si se pasa, se llama tras cada fichero. Bloquea la
        carpeta mientras dura: lanza ErrorCarpetaEnUso si otra operación la usa (por ejemplo, una
        vigilancia en otra ventana).
        """
        if not self.carpeta_destino.is_dir():
            logger.info("No hay historial guardado para deshacer", extra=SOLO_EN_FICHERO)
            return None
        with self.bloqueo:
            return self._deshacer(progreso)

    def limpiar_historial(self) -> int:
        """Borra el historial (sin mover ningún fichero) y devuelve cuántos movimientos tenía.

        Lanza ErrorHistorialNoEncontrado si no hay y ErrorCarpetaEnUso si otra operación usa la
        carpeta.
        """
        if not self.carpeta_destino.is_dir():
            raise ErrorHistorialNoEncontrado(
                f"No hay historial guardado en: {self.carpeta_destino}"
            )
        with self.bloqueo:
            self.mover.cargar_historial()
            cantidad = sum(len(o.registros) for o in self.mover.obtener_operaciones())
            self.mover.limpiar_historial()
            self.mover.guardar_historial()
            return cantidad

    def _deshacer(self, progreso: CallbackProgreso | None) -> ResultadoDeshacer | None:
        try:
            self.mover.cargar_historial()
        except ErrorHistorialNoEncontrado:
            logger.info("No hay historial guardado para deshacer", extra=SOLO_EN_FICHERO)
            return None

        logger.info("Deshaciendo el último paso del historial...")
        resultado = self.mover.deshacer_todo(carpeta_base=self.carpeta_destino, progreso=progreso)
        # La consola y la GUI muestran este resumen a su manera: aquí solo queda en el fichero.
        logger.info(
            f"Deshacer completado | Restaurados: {resultado.exitosos} | Fallidos: {resultado.fallidos}",
            extra=SOLO_EN_FICHERO,
        )
        return resultado
